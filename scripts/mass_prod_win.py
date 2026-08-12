"""Mass-produce PPO models that reproduce the winning BTC 5m CDL recipe.

Recipe recovered from models/best_model.zip + models/ppo_btc_cdl.zip
(obs 60*44+3, net (256,256), n_envs=256 -> batch 65536, n_steps 2048,
ent 0.02, linear lr schedule, ~1M timesteps, legacy 44-feature layout).

The winners were produced by a *selection* process (ablation picks the
best candidate on an out-of-sample window), so this script:
  1. trains `--seeds` candidates per (symbol, granularity) on the first 80%
     of data, with the legacy env-cost configuration
  2. validates every candidate on the chronological last 20% (true OOS)
  3. keeps the best-OOS candidate as the production model and records all
     candidates in the registry
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.ai.backtest import metrics, rl_backtest
from bot.ai.rl_trainer import train
from bot.data.cache import DataCache
from bot.data.features import FEATURE_COLUMNS, feature_stats_for
from config import settings

LEGACY_FEATURES = FEATURE_COLUMNS[:44]

# Legacy env-cost config (pre-settings era): low costs + moderate penalties.
LEGACY_ENV = dict(spread=0.0002, slippage=0.0,
                  trade_penalty=0.02, risk_penalty=0.05, align_bonus=0.05)


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
    parser = argparse.ArgumentParser(description="Mass-produce winning-recipe PPO models")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--granularities", default="")
    parser.add_argument("--timesteps", type=int, default=1_048_576)
    parser.add_argument("--timesteps-1m", type=int, default=262_144)
    parser.add_argument("--seeds", default="42,7")
    parser.add_argument("--n-envs", type=int, default=64)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--net-arch", default="256,256")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--eval-freq", type=int, default=262_144)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--outdir", default="models/prod_win")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip (symbol, granularity) pairs already in the registry")
    parser.add_argument("--data-end", default=None,
                        help="Pin the data snapshot (e.g. '2026-08-10 16:00')")
    args = parser.parse_args()

    symbols = parse_list(args.symbols, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    granularities = parse_list(args.granularities, ["5m", "1h", "4h", "1m"])
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

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

            timesteps = args.timesteps_1m if granularity in ("1m", "30s", "15s") \
                else args.timesteps
            n_test = int(len(df) * args.test_size)
            train_df, test_df = df.iloc[:-n_test], df.iloc[-n_test:]
            stats = feature_stats_for(train_df, feature_columns=LEGACY_FEATURES)
            ppy = periods_per_year(granularity)

            print(
                f"\n=== {symbol} {granularity}: {len(df)} rows, {timesteps} steps, "
                f"seeds={seeds}, train={len(train_df)} test={len(test_df)} ===",
                flush=True,
            )

            candidates = []
            for seed in seeds:
                t0 = time.time()
                seed_name = f"{symbol}_{granularity}_seed{seed}"
                seed_dir = outdir / seed_name
                seed_dir.mkdir(parents=True, exist_ok=True)
                model_path = train(
                    train_df,
                    model_path=seed_dir / f"{seed_name}.zip",
                    total_timesteps=timesteps,
                    n_envs=args.n_envs,
                    device=args.device,
                    window=args.window,
                    entropy_coef=settings.ENTROPY_COEF,
                    net_arch=tuple(int(x) for x in args.net_arch.split(",")),
                    feature_stats=stats,
                    eval_freq=args.eval_freq,
                    lr_schedule=True,
                    seed=seed,
                    feature_columns=LEGACY_FEATURES,
                    **LEGACY_ENV,
                )
                from stable_baselines3 import PPO

                best = seed_dir / "best_model.zip"
                use = best if best.exists() else model_path
                model = PPO.load(str(use), device="cpu")
                curve, trades = rl_backtest(
                    test_df, model, window=args.window,
                    spread=settings.SPREAD, slippage=settings.SLIPPAGE,
                    entry_gate=settings.ENTRY_GATE,
                    feature_stats=stats,
                    feature_columns=LEGACY_FEATURES,
                )
                report = {"seed": seed, **metrics(curve, trades, periods_per_year=ppy)}
                report["elapsed_s"] = round(time.time() - t0, 1)
                candidates.append(report)
                print(f"  seed {seed}: OOS sharpe={report['sharpe']:.3f} "
                      f"ret={report['total_return']:.4f} trades={report['n_trades']} "
                      f"({report['elapsed_s']}s)", flush=True)

            best_cand = max(candidates, key=lambda c: c["sharpe"])
            final_name = f"{symbol}_{granularity}.zip"
            best_src = (
                outdir / f"{symbol}_{granularity}_seed{best_cand['seed']}" / "best_model.zip"
            )
            src = best_src if best_src.exists() else (
                outdir / f"{symbol}_{granularity}_seed{best_cand['seed']}"
                / f"{symbol}_{granularity}_seed{best_cand['seed']}.zip"
            )
            shutil.copyfile(src, outdir / final_name)

            bh = float(test_df["close"].iloc[-1] / test_df["close"].iloc[args.window] - 1)
            entry = {
                "symbol": symbol,
                "granularity": granularity,
                "model": final_name,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data_rows": int(len(df)),
                "data_first": str(df.index[0]),
                "data_last": str(df.index[-1]),
                "recipe": "WINNING_CDL_LEGACY_ENV",
                "feature_cols": int(len(LEGACY_FEATURES)),
                "timesteps": int(timesteps),
                "n_envs": int(args.n_envs),
                "window": int(args.window),
                "net_arch": args.net_arch,
                "lr_schedule": True,
                "eval_freq": int(args.eval_freq),
                "env": LEGACY_ENV,
                "entry_gate": float(settings.ENTRY_GATE),
                "entropy": float(settings.ENTROPY_COEF),
                "candidates": candidates,
                "selected_seed": int(best_cand["seed"]),
                "oos_sharpe": float(best_cand["sharpe"]),
                "oos_return": float(best_cand["total_return"]),
                "oos_trades": int(best_cand["n_trades"]),
                "oos_win_rate": float(best_cand["win_rate"]),
                "oos_buy_hold": float(bh),
            }
            registry = [r for r in registry
                        if not (r["symbol"] == symbol and r["granularity"] == granularity)]
            registry.append(entry)
            registry_path.write_text(json.dumps(registry, indent=2))
            print(f"  FINAL {final_name} <- seed {best_cand['seed']} "
                  f"(OOS S={best_cand['sharpe']:.3f})", flush=True)

    print(f"\nAll done in {time.time()-total0:.0f}s. Registry: {registry_path}")
    if registry:
        print("  " + " | ".join(
            f"{r['symbol']} {r['granularity']} S={r['oos_sharpe']}"
            for r in registry))


if __name__ == "__main__":
    main()