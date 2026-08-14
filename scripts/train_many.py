import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from bot.ai.backtest import metrics, rl_backtest
from bot.ai.bootstrap import bootstrap_ci, psr, sharpe_ci, sharpe_se
from bot.ai.rl_trainer import train
from bot.data.cache import DataCache
from bot.data.features import feature_stats_for
from config import settings


def add_bootstrap_stats(rec, curve, ppy):
    """Lo SR SE/CI + PSR + stationary-bootstrap CI on the OOS curve returns."""
    returns = curve.pct_change().dropna().to_numpy(dtype=np.float64)
    n = int(len(returns))
    sr = float(rec["sharpe"])
    if n < 30 or not np.isfinite(sr):
        rec["oos_sharpe_se"] = None
        rec["oos_psr"] = None
        rec["oos_sharpe_ci"] = None
        rec["oos_sharpe_ci_boot"] = None
        return rec
    rec["oos_sharpe_se"] = round(sharpe_se(sr, n, periods_per_year=ppy), 4)
    lo, hi = sharpe_ci(sr, n, periods_per_year=ppy)
    rec["oos_sharpe_ci"] = [round(lo, 3), round(hi, 3)]
    rec["oos_psr"] = round(psr(returns, benchmark_sharpe=0.0,
                               periods_per_year=ppy), 4)
    try:
        blo, bhi = bootstrap_ci(returns, n_boot=500, mean_block=20, seed=0)
        rec["oos_sharpe_ci_boot"] = [round(blo, 3), round(bhi, 3)]
    except Exception:
        rec["oos_sharpe_ci_boot"] = None
    return rec


def periods_per_year(granularity: str) -> int:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(granularity)
    if minutes is None:
        return 8760
    return int(525600 / minutes)


def parse_list(raw, default):
    if not raw:
        return default
    return [x.strip().upper() if x.strip().isalpha() else x.strip() for x in raw.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Mass-train walk-forward-validated PPO models")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--granularities", default="5m")
    parser.add_argument("--timesteps", type=int, default=500_000)
    parser.add_argument("--n-envs", type=int, default=64)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--net-arch", default="1024,512")
    parser.add_argument("--splits", type=int, default=2)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--entry-gate", type=float, default=settings.ENTRY_GATE)
    parser.add_argument("--entropy", type=float, default=settings.ENTROPY_COEF)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--eval-freq", type=int, default=250_000)
    parser.add_argument("--outdir", default="models/prod")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip (symbol, granularity) pairs already in the registry")
    parser.add_argument("--small-tf", action="store_true",
                        help="Use low-cost reward scaling + tight entry gate for 1m (and below)")
    parser.add_argument("--data-end", default=None,
                        help="Pin the data snapshot (e.g. '2026-08-10 16:00') so "
                        "validation folds don't drift as the cache grows")
    parser.add_argument("--impact", type=float, default=0.0,
                        help="Almgren-Chriss square-root market impact coefficient")
    parser.add_argument("--dd-floor", type=float, default=0.0,
                        help="Max drawdown floor as fraction of peak equity (prop rule)")
    args = parser.parse_args()

    symbols = parse_list(args.symbols, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    granularities = parse_list(args.granularities, ["5m"])

    cache = DataCache()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    registry_path = outdir / "registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else []

    total0 = time.time()
    for symbol in symbols:
        for granularity in granularities:
            if args.skip_existing and any(
                    r["symbol"] == symbol and r["granularity"] == granularity
                    for r in registry):
                print(f"SKIP {symbol} {granularity}: already in registry", flush=True)
                continue
            df = cache.load(symbol, granularity)
            if df.empty:
                print(f"SKIP {symbol} {granularity}: no data", flush=True)
                continue
            if args.data_end:
                df = df[df.index <= args.data_end]
            if len(df) < 3000:
                print(f"SKIP {symbol} {granularity}: only {len(df)} rows", flush=True)
                continue

            if args.small_tf and granularity in ("1m", "30s", "15s"):
                trade_penalty, risk_penalty, align_bonus, entry_gate = (
                    0.02, 0.05, 0.05, 0.005)
            else:
                trade_penalty = settings.TRADE_PENALTY
                risk_penalty = settings.RISK_PENALTY
                align_bonus = settings.ALIGN_BONUS
                entry_gate = args.entry_gate

            cross_asset_dfs = {}
            for other in symbols:
                if other == symbol:
                    continue
                cdf = cache.load(other, granularity)
                if args.data_end:
                    cdf = cdf[cdf.index <= args.data_end]
                if not cdf.empty:
                    cross_asset_dfs[other] = cdf
            print(
                f"\n=== {symbol} {granularity}: {len(df)} rows "
                f"({df.index[0]} .. {df.index[-1]}) cross={list(cross_asset_dfs)} ===",
                flush=True,
            )

            n = len(df)
            n_test = int(n * args.test_size / args.splits)
            start_test = n - args.splits * n_test
            folds = []
            for fold in range(args.splits):
                fold_start = start_test + fold * n_test
                fold_end = start_test + (fold + 1) * n_test
                train_df = df.iloc[:fold_start]
                test_df = df.iloc[fold_start:fold_end]
                if len(train_df) < 2000 or len(test_df) < 2:
                    continue
                t0 = time.time()
                feat_stats = feature_stats_for(train_df, cross_asset_dfs)
                model_path = train(
                    train_df,
                    model_path=outdir / f"{symbol}_{granularity}_fold{fold}.zip",
                    total_timesteps=args.timesteps,
                    n_envs=args.n_envs,
                    device=args.device,
                    window=args.window,
                    trade_penalty=trade_penalty,
                    risk_penalty=risk_penalty,
                    align_bonus=align_bonus,
                    entropy_coef=args.entropy,
                    net_arch=tuple(int(x) for x in args.net_arch.split(",")),
                    feature_stats=feat_stats,
                    cross_asset_dfs=cross_asset_dfs,
                    eval_freq=args.eval_freq,
                    seed=args.seed,
                    impact_coef=args.impact,
                    max_dd_floor=args.dd_floor,
                )
                from stable_baselines3 import PPO

                best = Path(model_path).parent / "best_model.zip"
                use = best if best.exists() else model_path
                model = PPO.load(str(use), device="auto")
                curve, trades = rl_backtest(
                    test_df, model, window=args.window,
                    spread=settings.SPREAD, slippage=settings.SLIPPAGE,
                    trade_penalty=trade_penalty,
                    align_bonus=align_bonus,
                    entry_gate=entry_gate,
                    feature_stats=feat_stats,
                    cross_asset_dfs=cross_asset_dfs,
                    impact_coef=args.impact,
                    max_dd_floor=args.dd_floor,
                )
                ppy = periods_per_year(granularity)
                report = {"fold": fold, **metrics(curve, trades, periods_per_year=ppy)}
                add_bootstrap_stats(report, curve, ppy)
                bh = float(test_df["close"].iloc[-1] / test_df["close"].iloc[60] - 1)
                report["buy_hold"] = bh
                report["train_rows"] = len(train_df)
                report["test_rows"] = len(test_df)
                report["test_start"] = str(test_df.index[0])
                report["test_end"] = str(test_df.index[-1])
                folds.append(report)
                print(f"  fold {fold}: OOS sharpe={report['sharpe']:.3f} "
                      f"PSR={report.get('oos_psr', 0):.2f} "
                      f"ret={report['total_return']:.4f} trades={report['n_trades']} "
                      f"({time.time()-t0:.0f}s)", flush=True)

            # Final production model on all data
            t0 = time.time()
            feat_stats = feature_stats_for(df, cross_asset_dfs)
            final_name = f"{symbol}_{granularity}.zip"
            final_path = outdir / final_name
            model_path = train(
                df,
                model_path=final_path,
                total_timesteps=args.timesteps,
                n_envs=args.n_envs,
                device=args.device,
                window=args.window,
                trade_penalty=trade_penalty,
                risk_penalty=risk_penalty,
                align_bonus=align_bonus,
                entropy_coef=args.entropy,
                net_arch=tuple(int(x) for x in args.net_arch.split(",")),
                feature_stats=feat_stats,
                cross_asset_dfs=cross_asset_dfs,
                eval_freq=args.eval_freq,
                seed=args.seed,
                impact_coef=args.impact,
                max_dd_floor=args.dd_floor,
            )
            best = Path(model_path).parent / "best_model.zip"
            if best.exists():
                shutil.copyfile(best, final_path)

            entry = {
                "symbol": symbol,
                "granularity": granularity,
                "model": final_name,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data_rows": int(len(df)),
                "data_first": str(df.index[0]),
                "data_last": str(df.index[-1]),
                "cross_assets": list(cross_asset_dfs),
                "timesteps": int(args.timesteps),
                "n_envs": int(args.n_envs),
                "window": int(args.window),
                "net_arch": args.net_arch,
                "entry_gate": float(entry_gate),
                "trade_penalty": float(trade_penalty),
                "risk_penalty": float(risk_penalty),
                "align_bonus": float(align_bonus),
                "entropy": float(args.entropy),
                "seed": int(args.seed),
                "folds": folds,
                "mean_oos_sharpe": float(np.mean([f["sharpe"] for f in folds])) if folds else None,
                "market_impact_coef": float(args.impact),
                "max_dd_floor": float(args.dd_floor),
                "train_wall_s": round(time.time() - t0, 1),
            }
            registry = [r for r in registry
                        if not (r["symbol"] == symbol and r["granularity"] == granularity)]
            registry.append(entry)
            registry_path.write_text(json.dumps(registry, indent=2))
            print(f"  FINAL {final_name} saved ({entry['train_wall_s']}s) "
                  f"mean OOS sharpe={entry['mean_oos_sharpe']}", flush=True)

    print(f"\nAll done in {time.time()-total0:.0f}s. Registry: {registry_path}")
    if registry:
        print("  " + " | ".join(
            f"{r['symbol']} {r['granularity']} S={r['mean_oos_sharpe']}"
            for r in registry))


if __name__ == "__main__":
    main()
