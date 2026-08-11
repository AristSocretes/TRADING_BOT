from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import EvalCallback
from stable_baselines3.common.env_checker import check_env
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from bot.ai.env import ForexTradingEnv
from config import settings

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.benchmark = True


def linear_schedule(initial_value):
    """Decay rate linearly from initial_value to 0 over training."""

    def schedule(progress_remaining):
        return progress_remaining * initial_value

    return schedule


def seed_everything(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_device(device="auto"):
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def build_env(df, **env_kwargs):
    env = ForexTradingEnv(df, **env_kwargs)
    check_env(env)
    return env


def print_gpu_status():
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        mem = torch.cuda.mem_get_info(0)
        print(f"  GPU: {name} | free {mem[0]/1e9:.1f}GB / {mem[1]/1e9:.1f}GB")
    else:
        print("  GPU: CUDA NOT AVAILABLE - training on CPU")


def train(
    df,
    model_path=None,
    total_timesteps=500_000,
    n_envs=256,
    device="auto",
    trade_penalty=0.0,
    risk_penalty=0.0,
    align_bonus=0.0,
    window=30,
    spread=0.0004,
    slippage=0.0,
    sl_frac=0.0,
    net_arch=(256, 256),
    feature_stats=None,
    sup_probs=None,
    cross_asset_dfs=None,
    entropy_coef=0.0,
    hyperparams=None,
    vec_env_type="batched",
    eval_freq=100_000,
    spread_range=None,
    slippage_range=None,
    reward_clip=0.25,
    max_grad_norm=0.5,
    lr_schedule=False,
    seed=42,
):
    device = resolve_device(device)
    print(f"  device: {device}")
    print_gpu_status()
    seed_everything(seed)

    env_kwargs = {
        "window": window,
        "spread": spread,
        "slippage": slippage,
        "sl_frac": sl_frac,
        "trade_penalty": trade_penalty,
        "risk_penalty": risk_penalty,
        "align_bonus": align_bonus,
        "feature_stats": feature_stats,
        "sup_probs": sup_probs,
        "cross_asset_dfs": cross_asset_dfs,
        "spread_range": spread_range,
        "slippage_range": slippage_range,
        "reward_clip": reward_clip,
        "seed": seed,
        "episode_len": 2000,
    }
    # Compute features once, share the matrix across all parallel envs

    from bot.data.features import normalized_frame

    features_arr = (
        normalized_frame(df, stats=feature_stats, cross_asset_dfs=cross_asset_dfs)
        .replace([np.inf, -np.inf], 0.0)
        .fillna(0.0)
        .to_numpy(dtype=np.float32)
    )
    if not np.isfinite(features_arr).all():
        print("  WARNING: non-finite values in feature matrix (will be zeroed)")
        features_arr[~np.isfinite(features_arr)] = 0.0
    env_kwargs["features_arr"] = features_arr
    env = build_env(df, **env_kwargs)

    if vec_env_type == "batched":
        from bot.ai.env_batched import BatchedForexVecEnv

        vec_env = BatchedForexVecEnv(df, n_envs=n_envs, **env_kwargs)
    else:
        # Unique seed per env so parallel trajectories differ
        def _make_env(i):
            return lambda: ForexTradingEnv(df, **{**env_kwargs, "seed": seed + i})

        vec_cls = DummyVecEnv if vec_env_type == "dummy" else SubprocVecEnv
        vec_env = vec_cls([_make_env(i) for i in range(n_envs)])

    rollout_size = n_envs * 2048
    batch_size = max(256, rollout_size // 8)
    # EvalCallback counts callback invocations (one per step-call == n_envs
    # timesteps each), not timesteps; convert so the configured eval_freq (in
    # timesteps) actually fires at the right cadence.
    eval_calls = max(1, int(eval_freq // n_envs))
    lr = linear_schedule(3e-4) if lr_schedule else 3e-4
    params = {
        "learning_rate": lr,
        "n_steps": 2048,
        "batch_size": batch_size,
        "gamma": 0.99,
        "clip_range": 0.2,
        "clip_range_vf": 0.2,
        "ent_coef": entropy_coef,
        "max_grad_norm": max_grad_norm,
        "policy_kwargs": {"net_arch": list(net_arch)},
        "verbose": 1,
        "seed": seed,
        "device": device,
    }
    params.update(hyperparams or {})
    model = PPO("MlpPolicy", vec_env, **params)
    model_path = model_path or Path(settings.MODEL_PATH)
    model_dir = Path(model_path).parent
    model_dir.mkdir(parents=True, exist_ok=True)
    eval_callback = EvalCallback(
        env,
        best_model_save_path=str(model_dir),
        eval_freq=eval_calls,
        n_eval_episodes=3,
        deterministic=True,
        verbose=1,
    )
    model.learn(total_timesteps=total_timesteps, callback=eval_callback)
    model.save(str(model_path))
    # Robustness smoke test: verify the saved model still predicts finite
    # actions (catches NaN-weight blow-ups from unstable training).
    try:
        probe = np.zeros((1,) + vec_env.observation_space.shape, dtype=np.float32)
        with torch.no_grad():
            action, _ = model.predict(probe, deterministic=True)
        if not np.isfinite(action).all():
            print("  WARNING: saved model produced non-finite actions")
    except Exception as exc:  # pragma: no cover
        print(f"  WARNING: post-training smoke test failed: {exc}")
    return model_path
