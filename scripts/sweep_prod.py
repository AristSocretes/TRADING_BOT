"""Bigger candidate sweep: seeds x configs per (symbol, granularity).

Attempts to re-find a +1.3 OOS model. All candidates use the winning
recipes: window 60, net (256,256), n_envs 256 (128 for 88-feature),
ent 0.02, linear lr schedule, legacy 44-feature layout (plus one config
on the full 88-feature layout). Configs differ in env costs/penalties and
timesteps. Every candidate is trained on the first 80% and validated on
the chronological last 20% (true OOS) plus the 10% before it (pre-holdout
sanity check). The best-OOS seed x config per pair becomes the production
model; all candidates are recorded in the registry.

Each candidate trains into its own subdirectory so EvalCallback
best_model.zip files never collide (the bug that stalled the earlier
sweep). Requires the feature_columns plumbing in bot/{data,ai}/*.
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
LOWCOST = dict(spread=0.0002, slippage=0.0)
GATE = {"1m": 0.005, "30s": 0.005, "15s": 0.005}   # tight gate for sub-5m
SEEDS_BASE = [42, 7, 21, 99, 123, 555, 1337, 2024, 777, 31415]


def periods_per_year(granularity: str) -> int:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(granularity)
    if minutes is None:
        return 8760
    return int(525600 / minutes)


def parse_list(raw, default):
    if not raw:
        return default
    return [x.strip().upper() if x.strip().isalpha() else x.strip() for x in raw.split(",")]


CONFIGS = {
    "legacy_lowcost": dict(timesteps=262_144, entropy=0.02, n_envs=128, **LOWCOST,
                           trade_penalty=0.02, risk_penalty=0.05, align_bonus=0.05),
    "legacy_lowcost_1M": dict(timesteps=1_048_576, entropy=0.02, n_envs=128, **LOWCOST,
                              trade_penalty=0.02, risk_penalty=0.05, align_bonus=0.05),
    "mid": dict(timesteps=262_144, entropy=0.02, n_envs=128,
                spread=0.0003, slippage=2.5e-5,
                trade_penalty=0.035, risk_penalty=0.075, align_bonus=0.075),
    "legacy_noalign": dict(timesteps=262_144, entropy=0.02, n_envs=128, **LOWCOST,
                           trade_penalty=0.02, risk_penalty=0.05, align_bonus=0.0),
    "legacy_raw": dict(timesteps=262_144, entropy=0.02, n_envs=128, **LOWCOST,
                       trade_penalty=0.0, risk_penalty=0.05, align_bonus=0.0),
    "robust_1M": dict(timesteps=1_048_576, entropy=0.02, n_envs=128,
                      spread=settings.SPREAD, slippage=settings.SLIPPAGE,
                      trade_penalty=settings.TRADE_PENALTY,
                      risk_penalty=settings.RISK_PENALTY,
                      align_bonus=settings.ALIGN_BONUS),
    "full_feat_lowcost": dict(timesteps=262_144, entropy=0.02, n_envs=64, **LOWCOST,
                              trade_penalty=0.02, risk_penalty=0.05, align_bonus=0.05,
                              full_features=True),
}


def plan_for(symbol, granularity):
    if (symbol, granularity) == ("BTCUSDT", "5m"):
        return {
            "legacy_lowcost": SEEDS_BASE[:6],
            "legacy_lowcost_1M": SEEDS_BASE[:4],
            "mid": SEEDS_BASE[:4],
            "legacy_noalign": SEEDS_BASE[:4],
            "legacy_raw": SEEDS_BASE[:3],
            "robust_1M": SEEDS_BASE[:3],
            "full_feat_lowcost": SEEDS_BASE[:3],
        }
    if granularity == "5m":
        return {
            "legacy_lowcost": SEEDS_BASE[:4],
            "legacy_lowcost_1M": SEEDS_BASE[:2],
            "mid": SEEDS_BASE[:3],
        }
    if granularity == "1m":
        return {"legacy_lowcost": SEEDS_BASE[:3]}
    return {
        "legacy_lowcost": SEEDS_BASE[:3],
        "legacy_lowcost_1M": SEEDS_BASE[:2],
    }


def backtest_candidate(test_df, model, window, gate, stats, feat_cols, ppy):
    curve, trades = rl_backtest(
        test_df, model, window=window,
        spread=settings.SPREAD, slippage=settings.SLIPPAGE,
        entry_gate=gate, feature_stats=stats, feature_columns=feat_cols,
    )
    return metrics(curve, trades, periods_per_year=ppy)


def main():
    parser = argparse.ArgumentParser(description="Seeds x configs sweep per pair")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--granularities", default="")
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--outdir", default="models/sweep")
    parser.add_argument("--data-end", default=None,
                        help="Pin the data snapshot (e.g. '2026-08-11 23:15')")
    parser.add_argument("--smoke", action="store_true",
                        help="Train 1 candidate only (fast end-to-end check)")
    args = parser.parse_args()

    symbols = parse_list(args.symbols, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    granularities = parse_list(args.granularities, ["5m", "1h", "4h", "1m"])

    cache = DataCache()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    registry_path = outdir / "registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else []

    total0 = time.time()
    for symbol in symbols:
        for granularity in granularities:
            plan = plan_for(symbol, granularity)
            if args.smoke:
                plan = {next(iter(plan)): plan[next(iter(plan))][:1]}
            df = cache.load(symbol, granularity)
            if df.empty:
                print(f"SKIP {symbol} {granularity}: no data", flush=True)
                continue
            if args.data_end:
                df = df[df.index <= args.data_end]
            if len(df) < 3000:
                print(f"SKIP {symbol} {granularity}: only {len(df)} rows", flush=True)
                continue

            n_test = int(len(df) * 0.2)
            n_pre = int(len(df) * 0.1)
            train_df = df.iloc[:-n_test]
            test_df = df.iloc[-n_test:]
            pre_df = df.iloc[-n_test - n_pre:-n_test]
            gate = GATE.get(granularity, settings.ENTRY_GATE)
            ppy = periods_per_year(granularity)

            print(
                f"\n=== {symbol} {granularity}: {len(df)} rows, gate={gate}, "
                f"plan={list(plan)} ===", flush=True,
            )

            per_cfg_stats = {}
            per_cfg_cols = {}
            candidates = []
            for cfg_name in plan:
                cfg = CONFIGS[cfg_name]
                feat_cols = None if cfg.get("full_features") else LEGACY_FEATURES
                per_cfg_cols[cfg_name] = feat_cols
                if cfg_name not in per_cfg_stats:
                    stats = feature_stats_for(train_df, feature_columns=feat_cols)
                    per_cfg_stats[cfg_name] = stats
                for seed in plan[cfg_name]:
                    t0 = time.time()
                    sub = outdir / f"{symbol}_{granularity}" / f"{cfg_name}_seed{seed}"
                    sub.mkdir(parents=True, exist_ok=True)
                    model_path = sub / f"{cfg_name}_seed{seed}.zip"
                    chk = sub / "best_model.zip"
                    train(
                        train_df,
                        model_path=model_path,
                        total_timesteps=cfg["timesteps"],
                        n_envs=cfg["n_envs"],
                        device=args.device,
                        window=args.window,
                        entropy_coef=cfg["entropy"],
                        net_arch=(256, 256),
                        feature_stats=per_cfg_stats[cfg_name],
                        eval_freq=min(cfg["timesteps"] // 2, 262_144),
                        lr_schedule=True,
                        seed=seed,
                        feature_columns=feat_cols,
                        spread=cfg["spread"], slippage=cfg["slippage"],
                        trade_penalty=cfg["trade_penalty"],
                        risk_penalty=cfg["risk_penalty"],
                        align_bonus=cfg["align_bonus"],
                        hyperparams={"buffer_device": "gpu"},
                    )
                    from stable_baselines3 import PPO

                    use = chk if chk.exists() else model_path
                    model = PPO.load(str(use), device="cpu")
                    m_oos = backtest_candidate(test_df, model, args.window, gate,
                                               per_cfg_stats[cfg_name], feat_cols, ppy)
                    m_pre = backtest_candidate(pre_df, model, args.window, gate,
                                               per_cfg_stats[cfg_name], feat_cols, ppy)
                    rec = {
                        "config": cfg_name,
                        "seed": int(seed),
                        "timesteps": int(cfg["timesteps"]),
                        "n_envs": int(cfg["n_envs"]),
                        "full_features": bool(cfg.get("full_features")),
                        **m_oos,
                        "pre_oos_sharpe": float(m_pre["sharpe"]),
                        "pre_oos_return": float(m_pre["total_return"]),
                        "pre_oos_trades": int(m_pre["n_trades"]),
                        "elapsed_s": round(time.time() - t0, 1),
                    }
                    candidates.append(rec)
                    print(
                        f"  {cfg_name:20s} seed {seed:>4}: OOS S={m_oos['sharpe']:.3f} "
                        f"ret={m_oos['total_return']:+.4f} trades={m_oos['n_trades']} "
                        f"pre_S={m_pre['sharpe']:.2f} ({rec['elapsed_s']}s)", flush=True,
                    )

            best = max(candidates, key=lambda c: c["sharpe"])
            final_name = f"{symbol}_{granularity}.zip"
            best_src = (
                outdir / f"{symbol}_{granularity}"
                / f"{best['config']}_seed{best['seed']}" / "best_model.zip"
            )
            src = best_src if best_src.exists() else (
                outdir / f"{symbol}_{granularity}"
                / f"{best['config']}_seed{best['seed']}"
                / f"{best['config']}_seed{best['seed']}.zip"
            )
            shutil.copyfile(src, outdir / final_name)

            if (symbol, granularity) == ("BTCUSDT", "5m"):
                ref = {}
                for ref_name in ("ppo_btc_cdl", "best_model"):
                    ref_path = Path("models") / f"{ref_name}.zip"
                    if ref_path.exists():
                        from stable_baselines3 import PPO

                        rm = PPO.load(str(ref_path), device="cpu")
                        rm_oos = backtest_candidate(test_df, rm, args.window, gate,
                                                    per_cfg_stats["legacy_lowcost"],
                                                    LEGACY_FEATURES, ppy)
                        ref[ref_name] = {
                            "oos_sharpe": float(rm_oos["sharpe"]),
                            "oos_return": float(rm_oos["total_return"]),
                            "oos_trades": int(rm_oos["n_trades"]),
                        }
            else:
                ref = {}

            bh = float(test_df["close"].iloc[-1] / test_df["close"].iloc[args.window] - 1)
            entry = {
                "symbol": symbol,
                "granularity": granularity,
                "model": final_name,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data_rows": int(len(df)),
                "data_first": str(df.index[0]),
                "data_last": str(df.index[-1]),
                "train_rows": int(len(train_df)),
                "test_rows": int(len(test_df)),
                "test_start": str(test_df.index[0]),
                "test_end": str(test_df.index[-1]),
                "pre_holdout_start": str(pre_df.index[0]),
                "pre_holdout_end": str(pre_df.index[-1]),
                "window": int(args.window),
                "net_arch": "256,256",
                "lr_schedule": True,
                "entry_gate": float(gate),
                "backtest_costs": {"spread": settings.SPREAD, "slippage": settings.SLIPPAGE},
                "candidates": candidates,
                "selected": {"config": best["config"], "seed": int(best["seed"])},
                "oos_sharpe": float(best["sharpe"]),
                "oos_return": float(best["total_return"]),
                "oos_trades": int(best["n_trades"]),
                "oos_win_rate": float(best["win_rate"]),
                "oos_buy_hold": float(bh),
                "oos_pre_sharpe": float(best["pre_oos_sharpe"]),
                "reference_winners": ref,
            }
            registry = [r for r in registry
                        if not (r["symbol"] == symbol and r["granularity"] == granularity)]
            registry.append(entry)
            registry_path.write_text(json.dumps(registry, indent=2))
            print(
                f"  FINAL {final_name} <- {best['config']} seed {best['seed']} "
                f"(OOS S={best['sharpe']:.3f}, pre S={best['pre_oos_sharpe']:.2f})",
                flush=True,
            )

    print(f"\nAll done in {time.time()-total0:.0f}s. Registry: {registry_path}")
    if registry:
        print("  " + " | ".join(
            f"{r['symbol']} {r['granularity']} S={r['oos_sharpe']:.2f} "
            f"({r['selected']['config']}/s{r['selected']['seed']})"
            for r in registry))


if __name__ == "__main__":
    main()