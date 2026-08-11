import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from bot.ai.backtest import metrics, rl_backtest  # noqa: E402
from bot.ai.env import ForexTradingEnv  # noqa: E402
from bot.ai.env_batched import BatchedForexVecEnv  # noqa: E402
from bot.ai.rl_trainer import resolve_device  # noqa: E402
from bot.ai.supervised import (  # noqa: E402
    supervised_probs,
    train_supervised_model,
)
from bot.data.cache import DataCache  # noqa: E402
from bot.data.features import feature_stats_for  # noqa: E402
from config import settings  # noqa: E402

PERIODS = {"1m": 525600, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "1d": 365}


def periods_per_year(granularity: str) -> int:
    return PERIODS.get(granularity, 8760)


def fold_split(df, splits=4, test_size=0.2, fold=0):
    n = len(df)
    n_test = int(n * test_size / splits)
    start_test = n - splits * n_test
    fold_start = start_test + fold * n_test
    fold_end = start_test + (fold + 1) * n_test
    return df.iloc[:fold_start], df.iloc[fold_start:fold_end]


VARIANTS = [
    {"name": "baseline", "risk_penalty": 0.1, "trade_penalty": 0.05, "align_bonus": 0.1},
    {"name": "no_align", "risk_penalty": 0.1, "trade_penalty": 0.05, "align_bonus": 0.0},
    {"name": "scaled", "risk_penalty": 0.02, "trade_penalty": 0.01, "align_bonus": 0.02},
    {"name": "low_align", "risk_penalty": 0.1, "trade_penalty": 0.05, "align_bonus": 0.02},
    {"name": "light_trade", "risk_penalty": 0.1, "trade_penalty": 0.01, "align_bonus": 0.1},
    {"name": "low_risk", "risk_penalty": 0.02, "trade_penalty": 0.05, "align_bonus": 0.1},
]


def _train_ppo(train_df, outdir, timesteps, n_envs, window, v, feat_stats,
               sup_probs, cross_asset_dfs, entropy, net_arch, seed):
    """Train PPO, persisting only EvalCallback's best_model.zip (avoids the
    slow/None-final full model.save())."""

    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import EvalCallback

    from bot.data.features import normalized_frame

    device = resolve_device("auto")
    env_kwargs = {
        "window": window,
        "spread": settings.SPREAD,
        "slippage": settings.SLIPPAGE,
        "sl_frac": 0.0,
        "trade_penalty": v["trade_penalty"],
        "risk_penalty": v["risk_penalty"],
        "align_bonus": v["align_bonus"],
        "feature_stats": feat_stats,
        "sup_probs": sup_probs,
        "cross_asset_dfs": cross_asset_dfs,
        "seed": seed,
        "episode_len": 2000,
    }
    features_arr = (
        normalized_frame(train_df, stats=feat_stats, cross_asset_dfs=cross_asset_dfs)
        .replace([np.inf, -np.inf], 0.0)
        .fillna(0.0)
        .to_numpy(dtype=np.float32)
    )
    env_kwargs["features_arr"] = features_arr
    vec_env = BatchedForexVecEnv(train_df, n_envs=n_envs, **env_kwargs)
    eval_env = ForexTradingEnv(train_df, **env_kwargs)

    rollout_size = n_envs * 2048
    batch_size = max(256, rollout_size // 8)
    eval_calls = max(1, int(max(100_000, timesteps) // n_envs))
    model = PPO(
        "MlpPolicy", vec_env,
        learning_rate=3e-4, n_steps=2048, batch_size=batch_size,
        gamma=0.99, clip_range=0.2, ent_coef=entropy,
        policy_kwargs={"net_arch": list(net_arch)},
        verbose=0, seed=seed, device=device,
    )
    callback = EvalCallback(
        eval_env, best_model_save_path=str(outdir), eval_freq=eval_calls,
        n_eval_episodes=3, deterministic=True, verbose=0,
    )
    model.learn(total_timesteps=timesteps, callback=callback)
    return outdir / "best_model.zip"


def main():
    parser = argparse.ArgumentParser(
        description="Reward-shaping sweep on a single walk-forward fold"
    )
    parser.add_argument("--symbol", default=settings.SYMBOL)
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    parser.add_argument("--fold", type=int, default=3)
    parser.add_argument("--timesteps", type=int, default=300_000)
    parser.add_argument("--n-envs", type=int, default=256)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--net-arch", default="1024,512")
    parser.add_argument("--entry-gate", type=float, default=settings.ENTRY_GATE)
    parser.add_argument("--entropy", type=float, default=settings.ENTROPY_COEF)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    cache = DataCache()
    df = cache.load(args.symbol, args.granularity)
    if df.empty:
        print("No cached data. Run scripts/fetch_data.py first.")
        return

    cross_asset_dfs = {}
    for sym in ["ETHUSDT", "SOLUSDT"]:
        cdf = cache.load(sym, args.granularity)
        if not cdf.empty:
            cross_asset_dfs[sym] = cdf

    train_df, test_df = fold_split(
        df, splits=4, test_size=0.2, fold=args.fold
    )
    print(f"fold {args.fold}: train={len(train_df)} test={len(test_df)}")

    feat_stats = feature_stats_for(train_df, cross_asset_dfs)
    sup_model, sup_acc = train_supervised_model(
        train_df, train_df, window=args.window,
        feature_stats=feat_stats, cross_asset_dfs=cross_asset_dfs,
    )
    print(f"supervised acc: {sup_acc:.3f}")
    sup_train = supervised_probs(
        sup_model, train_df, window=args.window,
        feature_stats=feat_stats, cross_asset_dfs=cross_asset_dfs,
    )
    sup_test = supervised_probs(
        sup_model, test_df, window=args.window,
        feature_stats=feat_stats, cross_asset_dfs=cross_asset_dfs,
    )

    ppy = periods_per_year(args.granularity)
    bh = test_df["close"].iloc[-1] / test_df["close"].iloc[args.window] - 1
    results = []
    for v in VARIANTS:
        t0 = time.time()
        print(f"\n=== {v['name']} (risk={v['risk_penalty']}, "
              f"trade={v['trade_penalty']}, align={v['align_bonus']}) ===")
        outdir = Path("models") / "reward_sweep" / v["name"]
        outdir.mkdir(parents=True, exist_ok=True)
        best_path = _train_ppo(
            train_df,
            outdir,
            args.timesteps,
            args.n_envs,
            args.window,
            v,
            feat_stats,
            sup_train,
            cross_asset_dfs,
            args.entropy,
            tuple(int(x) for x in args.net_arch.split(",")),
            args.seed,
        )
        from stable_baselines3 import PPO

        model = PPO.load(str(best_path), device="cpu")
        curve, trades = rl_backtest(
            test_df,
            model,
            window=args.window,
            spread=settings.SPREAD,
            slippage=settings.SLIPPAGE,
            sl_frac=0.0,
            entry_gate=args.entry_gate,
            feature_stats=feat_stats,
            sup_probs=sup_test,
            cross_asset_dfs=cross_asset_dfs,
        )
        report = {
            "name": v["name"], "fold": args.fold,
            **metrics(curve, trades, periods_per_year=ppy),
        }
        report["buy_hold"] = float(bh)
        report["elapsed_s"] = round(time.time() - t0, 1)
        results.append(report)
        print(f"  {report}")
        print(f"  elapsed {report['elapsed_s']}s")

    results.sort(key=lambda r: r["sharpe"], reverse=True)
    print("\n=== SWEEP SUMMARY (out-of-sample fold {}) ===".format(args.fold))
    print(f"{'variant':<12}{'sharpe':>8}{'ret%':>9}{'maxDD%':>9}{'trades':>8}{'win%':>7}{'PF':>6}")
    for r in results:
        print(
            f"{r['name']:<12}{r['sharpe']:>8.2f}{r['total_return']*100:>9.1f}"
            f"{r['max_drawdown']*100:>9.1f}{r['n_trades']:>8}{r['win_rate']*100:>7.1f}"
            f"{r['profit_factor']:>6.2f}"
        )
    out = Path("results") / "reward_sweep.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=float))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
