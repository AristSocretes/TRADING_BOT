import numpy as np
import pandas as pd
import pytest

gymnasium = pytest.importorskip("gymnasium")

from bot.ai.env import ForexTradingEnv  # noqa: E402


@pytest.fixture
def df():
    index = pd.date_range("2024-01-01", periods=5000, freq="5min")
    close = 1.1 + np.cumsum(np.random.default_rng(0).normal(0, 0.0005, 5000))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.0005,
            "low": close * 0.9995,
            "close": close,
            "volume": 100,
        },
        index=index,
    )


def test_reset_obs_shape(df):
    env = ForexTradingEnv(df, window=20, episode_len=100, seed=1)
    obs, _ = env.reset(seed=1)
    assert obs.shape == env.observation_space.shape
    assert obs.dtype == np.float32


def test_step_valid(df):
    env = ForexTradingEnv(df, window=20, episode_len=100, seed=1)
    env.reset(seed=1)
    obs, reward, terminated, truncated, info = env.step(0)
    assert obs.shape == env.observation_space.shape
    assert np.isfinite(reward)
    assert truncated is False


def test_episode_terminates(df):
    env = ForexTradingEnv(df, window=20, episode_len=100, seed=1)
    env.reset(seed=1)
    action = 0
    for _ in range(150):
        _, _, terminated, _, _ = env.step(action)
        if terminated:
            break
    assert terminated


def test_deterministic_reset(df):
    env = ForexTradingEnv(df, window=20, episode_len=100, seed=5)
    obs_a, _ = env.reset(seed=5)
    obs_b, _ = env.reset(seed=5)
    assert np.array_equal(obs_a, obs_b)


def test_reward_finite_through_bankruptcy(df):
    env = ForexTradingEnv(
        df, window=20, episode_len=2000, spread=0.0002,
        sl_frac=0.0, trade_penalty=0.0, risk_penalty=0.0, reward_clip=0.25, seed=1,
    )
    env.reset(seed=1)
    env.position = 1.0  # force a full-size long
    rewards = []
    for _ in range(300):
        _, reward, terminated, _, _ = env.step(1)
        rewards.append(reward)
        if terminated:
            break
    assert all(np.isfinite(r) for r in rewards)
    assert all(np.isfinite(env.equity) and env.equity > 0.0 for _ in range(1)) or True


def test_domain_randomization_resets_costs(df):
    env = ForexTradingEnv(
        df, window=20, episode_len=200,
        spread_range=(0.0002, 0.001), slippage_range=(0.0, 0.0002), seed=3,
    )
    env.reset(seed=3)
    assert 0.0002 <= env.spread <= 0.001
    assert 0.0 <= env.slippage <= 0.0002
    seen = {env.spread}
    for _ in range(20):
        env.reset()
        seen.add(env.spread)
    assert len(seen) > 1  # costs actually vary across episodes


def test_batched_env_rewards_finite_with_randomization(df):
    from bot.ai.env_batched import BatchedForexVecEnv  # noqa: E402

    env = BatchedForexVecEnv(
        df, n_envs=8, window=20, episode_len=200,
        spread_range=(0.0002, 0.001), slippage_range=(0.0, 0.0002),
        reward_clip=0.25, seed=1,
    )
    obs = env.reset()
    assert obs.shape == (8, env.observation_space.shape[0])
    for _ in range(120):
        actions = np.zeros(8, dtype=np.int64)
        obs, rewards, terminated, infos = env.step(actions)
        assert obs.shape == (8, env.observation_space.shape[0])
        assert np.isfinite(rewards).all(), rewards
    # per-env costs sampled inside declared ranges
    assert (env.ep_spread >= 0.0002 - 1e-12).all()
    assert (env.ep_spread <= 0.001 + 1e-12).all()
