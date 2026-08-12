import gymnasium as gym
import numpy as np

from bot.data.features import normalized_frame

POSITION_LEVELS = np.array([-1.0, 0.0, 1.0], dtype=np.float32)


class ForexTradingEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        df,
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
        feature_columns=None,
    ):
        super().__init__()
        self.window = window
        self.episode_len = episode_len
        self.spread = spread
        self.slippage = slippage
        self.sl_frac = sl_frac
        self.trade_penalty = trade_penalty
        self.risk_penalty = risk_penalty
        self.align_bonus = align_bonus
        self.reward_clip = reward_clip
        # Domain randomization: per-episode cost samples from [base, hi].
        # Defaults to (None, None) meaning fixed spread/slippage -> no change
        # for existing callers.
        self.spread_rng = spread_range
        self.slippage_rng = slippage_range
        self.closes = df["close"].to_numpy(dtype=np.float64)
        self.lows = df["low"].to_numpy(dtype=np.float64)
        self.highs = df["high"].to_numpy(dtype=np.float64)
        if features_arr is None:
            features_arr = (
                normalized_frame(
                    df,
                    stats=feature_stats,
                    cross_asset_dfs=cross_asset_dfs,
                    feature_columns=feature_columns,
                )
                .replace([np.inf, -np.inf], 0.0)
                .fillna(0.0)
                .to_numpy(dtype=np.float32)
            )
        self.features = features_arr
        self.sup_probs = sup_probs
        lookback = 60
        if len(self.closes) > lookback:
            trend = np.zeros(len(self.closes), dtype=np.float32)
            trend[lookback:] = np.sign(
                self.closes[lookback:] - self.closes[:-lookback]
            ).astype(np.float32)
            self.trend = trend
        n_feat = self.features.shape[1]
        sup_dim = sup_probs.shape[1] if sup_probs is not None else 0
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=(window * n_feat + 3 + sup_dim,), dtype=np.float32
        )
        self.action_space = gym.spaces.Discrete(len(POSITION_LEVELS))
        self._rng = np.random.default_rng(seed)
        self.reset()

    def _obs(self):
        i = min(self.i, len(self.closes) - 1)
        window = self.features[i - self.window : i].reshape(-1)
        account = np.array(
            [self.equity / self.start_equity, self.position, self.pnl],
            dtype=np.float32,
        )
        if self.sup_probs is not None:
            sup = self.sup_probs[i].astype(np.float32)
            return np.concatenate([window, account, sup]).astype(np.float32)
        return np.concatenate([window, account]).astype(np.float32)

    def _sample_costs(self):
        """Domain randomization: sample fresh spread/slippage per episode."""
        rng = self._rng
        if self.spread_rng is not None:
            low, high = self.spread_rng
            self.spread = float(rng.uniform(min(low, high), max(low, high)))
        if self.slippage_rng is not None:
            low, high = self.slippage_rng
            self.slippage = float(rng.uniform(min(low, high), max(low, high)))

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        start_idx = (options or {}).get("start_idx")
        if start_idx is not None:
            self._episode_start = int(start_idx)
        else:
            max_start = len(self.closes) - self.window - self.episode_len - 1
            self._episode_start = self.window + int(self._rng.integers(0, max(1, max_start)))
        self._sample_costs()
        self.i = self._episode_start
        self.start_equity = 10000.0
        self.equity = self.start_equity
        self.position = 0.0
        self._mark_price = self.closes[self._episode_start]
        self.pnl = 0.0
        return self._obs(), {}

    def _set_position(self, target):
        target = float(target)
        if abs(target - self.position) < 1e-6:
            return
        delta = abs(target - self.position)
        cost = (self.spread / 2 + self.slippage) * delta
        self.equity *= 1 - cost
        self.position = target
        self._mark_price = self.closes[self.i]
        self.pnl = 0.0

    def _mark(self, price):
        ret = price / self._mark_price - 1
        self.pnl = ret * self.position
        self.equity *= 1 + self.pnl
        if not (self.equity > 0.0):
            self.equity = 1e-9
        self._mark_price = price

    def step(self, action):
        prev_equity = self.equity
        target = POSITION_LEVELS[int(action)]
        old_pos = self.position
        changed = abs(target - old_pos) > 1e-6

        if self.position != 0.0:
            if self.sl_frac > 0:
                if self.position > 0:
                    stop = self._mark_price * (1 - self.sl_frac)
                    if self.lows[self.i] <= stop:
                        self._mark(stop)
                        self._set_position(0.0)
                elif self.position < 0:
                    stop = self._mark_price * (1 + self.sl_frac)
                    if self.highs[self.i] >= stop:
                        self._mark(stop)
                        self._set_position(0.0)
            if self.position != 0.0:
                self._mark(self.closes[self.i])

        self._set_position(target)
        self.i += 1
        terminated = (self.i - self._episode_start >= self.episode_len) or (self.equity <= 0.0)

        log_ret = float(np.log(self.equity / prev_equity)) if prev_equity > 0 else -10.0
        if not np.isfinite(log_ret):
            log_ret = -10.0
        if self.reward_clip > 0:
            log_ret = float(np.clip(log_ret, -self.reward_clip, self.reward_clip))
        risk_pen = self.risk_penalty * (self.position ** 2)
        reward = log_ret - risk_pen
        if self.align_bonus > 0 and self.position != 0.0:
            trend = getattr(self, "trend", None)
            if trend is not None and trend[self.i] != 0.0:
                if np.sign(self.position) == trend[self.i]:
                    reward += self.align_bonus
        if changed:
            reward -= self.trade_penalty
        return self._obs(), reward, terminated, False, {}