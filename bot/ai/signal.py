import numpy as np

from bot.data.features import (
    FEATURE_COLUMNS,
    normalized_frame,
)

POSITION_LEVELS = np.array([-1.0, 0.0, 1.0], dtype=np.float32)


def _obs_from_features(features, i, window, account=None, sup_probs=None):
    window_feat = features[i - window : i].reshape(-1)
    if account is None:
        account = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    if sup_probs is not None:
        sup = sup_probs[i].astype(np.float32)
        return np.concatenate([window_feat, account, sup]).astype(np.float32)
    return np.concatenate([window_feat, account]).astype(np.float32)


class SignalGenerator:
    # Feature counts this codebase has used over time; the window is derived
    # from the observation dim so models trained on either layout load fine.
    _KNOWN_FEATURE_COUNTS = (44, len(FEATURE_COLUMNS), 57)

    def __init__(self, model_path, window=None, feature_stats=None, sup_probs=None,
                 cross_asset_dfs=None, entry_gate=0.0, min_confidence=0.0):
        from stable_baselines3 import PPO

        self.model = PPO.load(model_path, device="cpu")
        if window is None:
            obs_dim = self.model.observation_space.shape[0]
            sup_dim = sup_probs.shape[1] if sup_probs is not None else 0
            base = obs_dim - 3 - sup_dim
            matches = [
                n_feat for n_feat in self._KNOWN_FEATURE_COUNTS
                if base % n_feat == 0 and base // n_feat > 0
            ]
            if matches:
                n_feat = min(matches, key=lambda n: abs(base // n - 60))
            else:
                n_feat = len(FEATURE_COLUMNS)
            self.window = base // n_feat
            self.n_feat = n_feat
        else:
            self.window = window
            self.n_feat = len(FEATURE_COLUMNS)
        self.feature_stats = feature_stats
        self.sup_probs = sup_probs
        self.cross_asset_dfs = cross_asset_dfs
        self.entry_gate = entry_gate
        self.min_confidence = min_confidence

    def _features(self, df):
        feats = normalized_frame(
            df, stats=self.feature_stats, cross_asset_dfs=self.cross_asset_dfs
        )
        if self.n_feat != len(FEATURE_COLUMNS):
            feats = feats.iloc[:, : self.n_feat]  # legacy 60-feature layout
        return (
            feats.replace([np.inf, -np.inf], 0.0)
            .fillna(0.0)
            .to_numpy(dtype=np.float32)
        )

    def predict(self, df, position=0.0, equity_ratio=1.0, pnl=0.0):
        import torch
        from stable_baselines3.common.utils import obs_as_tensor

        features = self._features(df)
        if len(features) < self.window + 1:
            raise ValueError(f"need at least {self.window + 1} rows of features")
        i = len(features) - 1
        account = np.array([equity_ratio, position, pnl], dtype=np.float32)
        obs = _obs_from_features(features, i, self.window, account=account,
                                 sup_probs=self.sup_probs)
        with torch.no_grad():
            obs_t = obs_as_tensor(obs, self.model.policy.device)
            if obs_t.ndim == 1:
                obs_t = obs_t.unsqueeze(0)
            dist = self.model.policy.get_distribution(obs_t)
            probs = dist.distribution.probs
            action, _ = self.model.predict(obs, deterministic=True)
            confidence = float(probs[0, int(action)].cpu().numpy())
        signal = float(POSITION_LEVELS[int(action)])
        # Robustness guards: reject NaN confidence and low-confidence calls
        if not np.isfinite(confidence):
            return {"signal": 0.0, "confidence": 0.0, "trend": float(
                df["close"].iloc[-1] / df["close"].iloc[-1 - self.window] - 1
            )}
        if self.min_confidence > 0.0 and signal != 0.0 and confidence < self.min_confidence:
            signal = 0.0
        trend = float(df["close"].iloc[-1] / df["close"].iloc[-1 - self.window] - 1)
        if self.entry_gate > 0.0 and position == 0.0 and signal != 0.0 \
                and abs(trend) < self.entry_gate:
            signal = 0.0
        return {"signal": signal, "confidence": confidence, "trend": trend}

    def predict_signals(self, df):
        from bot.ai.env import ForexTradingEnv
        from config import settings

        env = ForexTradingEnv(
            df,
            window=self.window,
            episode_len=len(df),
            spread=settings.SPREAD,
            slippage=settings.SLIPPAGE,
            sl_frac=0.0,
            trade_penalty=0.0,
            risk_penalty=0.0,
            align_bonus=0.0,
            feature_stats=self.feature_stats,
            sup_probs=self.sup_probs,
            cross_asset_dfs=self.cross_asset_dfs,
        )
        obs, _ = env.reset(options={"start_idx": self.window})
        closes = df["close"].to_numpy(dtype=np.float64)
        n = len(closes)
        signals = np.zeros(n, dtype=np.float32)
        for k in range(n - self.window - 1):
            action, _ = self.model.predict(obs, deterministic=True)
            target = POSITION_LEVELS[int(action)]
            pos = float(env.position)
            if self.entry_gate > 0.0 and pos == 0.0 and target != 0.0:
                ret = closes[self.window + k] / closes[k] - 1
                if abs(ret) < self.entry_gate:
                    target = 0.0
                    action = int(np.where(POSITION_LEVELS == target)[0][0])
            obs, _, terminated, _, _ = env.step(action)
            signals[self.window + k] = float(env.position)
            if terminated:
                break
        return signals