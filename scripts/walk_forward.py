import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from bot.ai.backtest import metrics, rl_backtest  # noqa: E402
from bot.ai.rl_trainer import train  # noqa: E402
from bot.ai.supervised import (  # noqa: E402
    save_supervised_model,
    supervised_probs,
    train_supervised_model,
)
from bot.data.cache import DataCache  # noqa: E402
from bot.data.features import feature_stats_for  # noqa: E402
from config import settings  # noqa: E402


def periods_per_year(granularity: str) -> int:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(granularity)
    if minutes is None:
        return 8760
    return int(525600 / minutes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=settings.SYMBOL)
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--timesteps", type=int, default=500_000)
    parser.add_argument("--n-envs", type=int, default=256)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--net-arch", default="1024,512")
    parser.add_argument("--penalty", type=float, default=settings.TRADE_PENALTY)
    parser.add_argument("--risk-penalty", type=float, default=settings.RISK_PENALTY)
    parser.add_argument("--align-bonus", type=float, default=settings.ALIGN_BONUS)
    parser.add_argument("--entry-gate", type=float, default=settings.ENTRY_GATE)
    parser.add_argument("--sl-frac", type=float, default=0.0)
    parser.add_argument("--sup-horizon", type=int, default=4)
    parser.add_argument("--sup-threshold", type=float, default=0.001)
    parser.add_argument("--entropy", type=float, default=settings.ENTROPY_COEF)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--eval-freq", type=int, default=500_000)
    parser.add_argument("--outdir", default="models/wf")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fold-only", type=int, default=None)
    parser.add_argument(
        "--data-end",
        default=None,
        help="Pin the data snapshot: only rows up to this timestamp (e.g. "
        "2026-08-09 16:00 or ISO) are used, so fold boundaries don't drift "
        "as the cache grows.",
    )
    args = parser.parse_args()

    cache = DataCache()
    df = cache.load(args.symbol, args.granularity)
    if df.empty:
        print("No cached data. Run scripts/fetch_data.py first.")
        return
    if args.data_end:
        df = df[df.index <= args.data_end]
        print(f"Pinned data snapshot to <= {df.index[-1]} ({len(df)} rows)")

    # Load cross-asset data
    cross_asset_dfs = {}
    for sym in ["ETHUSDT", "SOLUSDT"]:
        cdf = cache.load(sym, args.granularity)
        if not cdf.empty:
            cross_asset_dfs[sym] = cdf
    print(f"Cross-asset data loaded: {list(cross_asset_dfs.keys())}")

    n = len(df)
    n_test = int(n * args.test_size / args.splits)
    start_test = n - args.splits * n_test
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    total0 = time.time()
    reports = []
    curves = []
    for fold in range(args.splits):
        if args.fold_only is not None and fold != args.fold_only:
            continue
        fold_start = start_test + fold * n_test
        fold_end = start_test + (fold + 1) * n_test
        train_df = df.iloc[:fold_start]
        test_df = df.iloc[fold_start:fold_end]
        if len(train_df) < 2000 or len(test_df) < 2:
            continue
        t0 = time.time()
        feat_stats = feature_stats_for(train_df, cross_asset_dfs)
        
        # Train supervised model on train fold
        sup_model, sup_acc = train_supervised_model(
            train_df,
            train_df,  # use train for both train/val for now (no separate val split)
            window=args.window,
            horizon=args.sup_horizon,
            threshold=args.sup_threshold,
            feature_stats=feat_stats,
            cross_asset_dfs=cross_asset_dfs,
        )
        print(f"  supervised accuracy: {sup_acc:.3f}")
        save_supervised_model(sup_model, outdir / f"fold{fold}_sup.pkl")
        sup_probs = supervised_probs(
            sup_model, train_df, window=args.window, feature_stats=feat_stats,
            cross_asset_dfs=cross_asset_dfs)
        
        model_path = train(
            train_df,
            model_path=outdir / f"fold{fold}.zip",
            total_timesteps=args.timesteps,
            n_envs=args.n_envs,
            device=args.device,
            window=args.window,
            trade_penalty=args.penalty,
            risk_penalty=args.risk_penalty,
            align_bonus=args.align_bonus,
            sl_frac=args.sl_frac,
            entropy_coef=args.entropy,
            net_arch=tuple(int(x) for x in args.net_arch.split(",")),
            feature_stats=feat_stats,
            sup_probs=sup_probs,
            cross_asset_dfs=cross_asset_dfs,
            eval_freq=args.eval_freq,
            seed=args.seed,
        )
        best = Path(model_path).parent / "best_model.zip"
        use = best if best.exists() else model_path
        if best.exists():
            import shutil

            shutil.copyfile(best, outdir / f"fold{fold}_best.zip")
        from stable_baselines3 import PPO

        model = PPO.load(str(use), device="cpu")
        
        # Supervised probs for test fold
        sup_probs_test = supervised_probs(
            sup_model, test_df, window=args.window, feature_stats=feat_stats,
            cross_asset_dfs=cross_asset_dfs)
        
        curve, trades = rl_backtest(
            test_df,
            model,
            window=args.window,
            spread=settings.SPREAD,
            slippage=settings.SLIPPAGE,
            trade_penalty=args.penalty,
            sl_frac=args.sl_frac,
            align_bonus=args.align_bonus,
            entry_gate=args.entry_gate,
            feature_stats=feat_stats,
            sup_probs=sup_probs_test,
            cross_asset_dfs=cross_asset_dfs,
        )
        ppy = periods_per_year(args.granularity)
        report = {"fold": fold, **metrics(curve, trades, periods_per_year=ppy)}
        bh = test_df["close"].iloc[-1] / test_df["close"].iloc[60] - 1
        report["buy_hold"] = float(bh)
        sma = test_df["close"].rolling(72).mean()
        report["bull_frac"] = float((test_df["close"] > sma).mean())
        report["train_rows"] = len(train_df)
        report["test_rows"] = len(test_df)
        report["fold_start"] = str(test_df.index[0])
        report["fold_end"] = str(test_df.index[-1])
        reports.append(report)
        curves.append(curve)
        np.save(outdir / f"fold{fold}_curve.npy", np.array(curve, dtype=np.float64))
        np.save(outdir / f"fold{fold}_trades.npy", np.array(trades, dtype=np.float64))
        print(
            f"fold {fold}: train={len(train_df)} test={len(test_df)} "
            f"trained in {time.time()-t0:.0f}s"
        )
        print("  ", {k: round(v, 4) if isinstance(v, float) else v for k, v in report.items()})

    if reports:
        mean_sharpe = np.mean([r["sharpe"] for r in reports])
        print("Mean out-of-sample Sharpe:", round(float(mean_sharpe), 4))
        print("Mean buy&hold return:", round(float(np.mean([r["buy_hold"] for r in reports])), 4))
        import json

        with open(outdir / "reports.json", "w") as fh:
            json.dump(reports, fh, indent=2, default=float)
        meta = {
            "data_rows": int(len(df)),
            "data_first": str(df.index[0]),
            "data_last": str(df.index[-1]),
            "n_test": int(n_test),
            "splits": int(args.splits),
            "test_size": float(args.test_size),
            "window": int(args.window),
            "align_bonus": float(args.align_bonus),
            "entry_gate": float(args.entry_gate),
            "risk_penalty": float(args.risk_penalty),
            "trade_penalty": float(args.penalty),
            "entropy": float(args.entropy),
            "timesteps": int(args.timesteps),
            "n_envs": int(args.n_envs),
            "seed": int(args.seed),
        }
        with open(outdir / "run_meta.json", "w") as fh:
            json.dump(meta, fh, indent=2)
        print(f"Reports saved to {outdir / 'reports.json'}")
    print(f"Total time: {time.time()-total0:.0f}s")


if __name__ == "__main__":
    main()
