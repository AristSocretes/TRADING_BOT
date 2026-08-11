import numpy as np
from stable_baselines3.common.vec_env import VecEnv

from bot.ai.env import POSITION_LEVELS
from bot.data.features import normalized_frame


class BatchedForexVecEnv(VecEnv):
    """Fully vectorized trading env: all instances step in one numpy call.

    This removes the per-env Python loop so the GPU policy becomes the
    bottleneck instead of CPU env stepping.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        df,
        n_envs=64,
        window=30,
        episode_len=2000,
        spread=0.0002,
        slippage=0.0,
        sl_frac=0.0,
        trade_penalty=0.02,
        risk_penalty=0.05,
        align_bonus=0.0,
        feature_stats=None,
        sup_probs=None,
        cross_asset_dfs=None,
        features_arr=None,
        spread_range=None,
        slippage_range=None,
        reward_clip=0.25,
        seed=0,
    ):
        self.closes = df["close"].to_numpy(dtype=np.float64)
        self.lows = df["low"].to_numpy(dtype=np.float64)
        self.highs = df["high"].to_numpy(dtype=np.float64)
        if features_arr is None:
            features_arr = (
                normalized_frame(df, stats=feature_stats, cross_asset_dfs=cross_asset_dfs)
                .replace([np.inf, -np.inf], 0.0)
                .fillna(0.0)
                .to_numpy(dtype=np.float32)
            )
        self.features = features_arr
        self.sup_probs = sup_probs
        self.n = len(self.closes)
        self.window = window
        self.episode_len = episode_len
        self.spread = spread
        self.slippage = slippage
        self.sl_frac = sl_frac
        self.trade_penalty = trade_penalty
        self.risk_penalty = risk_penalty
        self.align_bonus = align_bonus
        self.reward_clip = reward_clip
        self.spread_range = spread_range
        self.slippage_range = slippage_range
        if len(self.closes) > 60:
            trend = np.zeros(len(self.closes), dtype=np.float32)
            trend[60:] = np.sign(self.closes[60:] - self.closes[:-60]).astype(np.float32)
            self.trend = trend

        n_feat = self.features.shape[1]
        sup_dim = sup_probs.shape[1] if sup_probs is not None else 0
        obs_dim = window * n_feat + 3 + sup_dim
        self.obs_dim = obs_dim
        self._rng = np.random.default_rng(seed)

        super().__init__(n_envs, obs_space_placeholder(df, obs_dim), action_space_placeholder())

        self.i = np.zeros(n_envs, dtype=np.int64)
        self.ep_start = np.zeros(n_envs, dtype=np.int64)
        self.position = np.zeros(n_envs, dtype=np.float64)
        self.equity = np.full(n_envs, 10000.0, dtype=np.float64)
        self.start_equity = np.full(n_envs, 10000.0, dtype=np.float64)
        self.mark_price = np.zeros(n_envs, dtype=np.float64)
        self.pnl = np.zeros(n_envs, dtype=np.float64)
        self.ep_spread = np.full(n_envs, self.spread, dtype=np.float64)
        self.ep_slippage = np.full(n_envs, self.slippage, dtype=np.float64)
        self.reset()

    def _sample_costs(self, mask=None):
        if self.spread_range is not None:
            lo, hi = min(self.spread_range), max(self.spread_range)
            n = mask.sum() if mask is not None else self.num_envs
            idx = mask if mask is not None else slice(None)
            self.ep_spread[idx] = self._rng.uniform(lo, hi, size=n)
        if self.slippage_range is not None:
            lo, hi = min(self.slippage_range), max(self.slippage_range)
            n = mask.sum() if mask is not None else self.num_envs
            idx = mask if mask is not None else slice(None)
            self.ep_slippage[idx] = self._rng.uniform(lo, hi, size=n)

    def reset(self):
        max_start = self.n - self.window - self.episode_len - 1
        start = self.window + self._rng.integers(0, max(1, max_start), size=self.num_envs)
        self.i = start.astype(np.int64)
        self.ep_start = self.i.copy()
        self._sample_costs()
        self.position[:] = 0.0
        self.equity[:] = 10000.0
        self.start_equity[:] = 10000.0
        self.mark_price = self.closes[start]
        self.pnl[:] = 0.0
        return self._obs()

    def _obs(self):
        i = np.minimum(self.i, self.n - 1)
        w = self.window
        rows = i[:, None] - np.arange(w)[None, :]
        win = self.features[rows].reshape(self.num_envs, w * self.features.shape[1])
        acct = np.stack(
            [self.equity / self.start_equity, self.position, self.pnl], axis=1
        )
        if self.sup_probs is not None:
            sp = self.sup_probs[i].astype(np.float32)
            obs = np.concatenate([win, acct, sp], axis=1)
        else:
            obs = np.concatenate([win, acct], axis=1)
        return obs.astype(np.float32)

    def step_async(self, actions):
        self._actions = np.asarray(actions, dtype=np.int64)

    def step_wait(self):
        actions = self._actions
        i = self.i
        prev_equity = self.equity.copy()

        # Stop-loss checks first
        if self.sl_frac > 0:
            long_pos = self.position > 0
            short_pos = self.position < 0
            stop_long = self.mark_price * (1 - self.sl_frac)
            stop_short = self.mark_price * (1 + self.sl_frac)
            hit_long = long_pos & (self.lows[i] <= stop_long)
            hit_short = short_pos & (self.highs[i] >= stop_short)
            self._mark_at(hit_long, stop_long)
            self._mark_at(hit_short, stop_short)

        # Mark remaining open positions at current close
        open_pos = self.position != 0.0
        mark_pnl = np.zeros(self.num_envs, dtype=np.float64)
        mark_pnl[open_pos] = (
            (self.closes[i[open_pos]] / self.mark_price[open_pos] - 1)
            * self.position[open_pos]
        )
        self.equity *= 1 + mark_pnl
        self.mark_price = self.closes[i]

        # Transition to target position with costs
        target = POSITION_LEVELS[actions].astype(np.float64)
        changed = np.abs(target - self.position) > 1e-6
        cost = (self.ep_spread / 2 + self.ep_slippage) * np.abs(target - self.position)
        self.equity *= 1 - cost
        self.position = target
        self.mark_price = self.closes[i]
        # Mirror single env: pnl keeps last mark value unless position changed
        self.pnl = np.where(changed, 0.0, mark_pnl)
        # Numeric safety: never let equity go negative/NaN (log-reward guard)
        self.equity = np.maximum(self.equity, 1e-9)

        self.i += 1
        terminated = (self.i - self.ep_start >= self.episode_len) | (self.equity <= 0.0)

        log_ret = np.log(np.maximum(self.equity, 1e-12) / np.maximum(prev_equity, 1e-12))
        if self.reward_clip > 0:
            log_ret = np.clip(log_ret, -self.reward_clip, self.reward_clip)
        rewards = log_ret - self.risk_penalty * self.position ** 2
        rewards[np.isnan(rewards)] = -self.reward_clip if self.reward_clip > 0 else -10.0
        rewards[changed] -= self.trade_penalty
        if self.align_bonus > 0:
            trend = getattr(self, "trend", None)
            if trend is not None:
                aligned = (
                    (self.position != 0.0)
                    & (trend[i] != 0.0)
                    & (np.sign(self.position) == trend[i])
                )
                rewards[aligned] += self.align_bonus

        # Auto-reset terminated envs
        if terminated.any():
            self._reset_envs(terminated)

        infos = [{} for _ in range(self.num_envs)]
        return self._obs(), rewards.astype(np.float32), terminated, infos

    def _mark_at(self, mask, stop_price):
        """Mark stopped positions at their stop price, charge exit cost, flatten."""
        if mask.any():
            pos = self.position[mask]
            ratio = stop_price[mask] / self.mark_price[mask]
            pnl = np.where(pos > 0, ratio - 1, 1 - ratio)
            self.equity[mask] *= 1 + pnl
            cost = (self.ep_spread[mask] / 2 + self.ep_slippage[mask]) * np.abs(pos)
            self.equity[mask] *= 1 - cost
            self.equity[mask] = np.maximum(self.equity[mask], 1e-9)
            self.position[mask] = 0.0

    def _reset_envs(self, mask):
        max_start = self.n - self.window - self.episode_len - 1
        start = self.window + self._rng.integers(0, max(1, max_start), size=mask.sum())
        self.i[mask] = start.astype(np.int64)
        self.ep_start[mask] = self.i[mask]
        self._sample_costs(mask)
        self.position[mask] = 0.0
        self.equity[mask] = 10000.0
        self.start_equity[mask] = 10000.0
        self.mark_price[mask] = self.closes[start]
        self.pnl[mask] = 0.0

    def get_attr(self, attr_name, indices=None):
        idx = range(self.num_envs) if indices is None else _to_indices(indices)
        return [getattr(self, attr_name) for _ in idx]

    def set_attr(self, attr_name, value, indices=None):
        for i in range(self.num_envs):
            if indices is None or i in _to_indices(indices):
                setattr(self, attr_name, value)

    def env_method(self, method_name, *args, indices=None, **kwargs):
        idx = range(self.num_envs) if indices is None else _to_indices(indices)
        return [getattr(self, method_name)(*args, **kwargs) for _ in idx]

    def close(self):
        pass

    def env_is_wrapped(self, wrapper_class, indices=None):
        return [False] * self.num_envs


def _to_indices(indices):
    if isinstance(indices, int):
        return [indices]
    if isinstance(indices, np.ndarray):
        return indices.tolist()
    return list(indices)


def obs_space_placeholder(df, obs_dim):
    import gymnasium as gym

    return gym.spaces.Box(low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32)


def action_space_placeholder():
    import gymnasium as gym

    return gym.spaces.Discrete(len(POSITION_LEVELS))
