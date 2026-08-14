"""Regime classifier: Gaussian Hidden Markov Model (Baum-Welch EM).

Dependency-free (NumPy only) Gaussian HMM fit on [log-return, realized vol]
per-bar observations. Used as an execution overlay: label the current market
state (CALM / TREND / VOLATILE) and scale position sizes accordingly.

Design notes (production-safe):
  - Batch Baum-Welch EM fit with log-space forward/backward for stability.
  - `regime_probs` uses the *filtered* (causal) forward pass only: the
    probability of the state at bar t depends on bars 0..t, never on the
    future — identical to what a live server can compute bar by bar.
  - States are labeled by return volatility rank; the regime with the
    largest |mean return| is TREND, else the middle-vol state is CHOP.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REGIMES = ("CALM", "TREND", "VOLATILE", "CHOP")
DEFAULT_FACTORS = {"CALM": 1.0, "TREND": 1.0, "VOLATILE": 0.6, "CHOP": 0.85}


def _observations(df, vol_window: int = 20) -> np.ndarray:
    """[log-return, realized vol] per bar; vol is std of the last N returns."""
    closes = df["close"].to_numpy(dtype=np.float64)
    lr = np.log(closes[1:] / closes[:-1])
    lr = np.nan_to_num(lr, nan=0.0, posinf=0.0, neginf=0.0)
    n = len(lr)
    vol = np.zeros(n)
    for i in range(n):
        lo = max(0, i + 1 - vol_window)
        seg = lr[lo:i + 1]
        vol[i] = float(seg.std()) if len(seg) >= 3 else 0.0
    obs = np.column_stack([lr, vol])
    obs = obs[vol > 0.0]
    if len(obs) < 50:
        raise ValueError("not enough bars for regime fit")
    return obs


def _log_gauss(x, means, inv_covs, log_dets, k):
    d = x.shape[1]
    z = x - means[k]
    logp = -0.5 * (d * np.log(2 * np.pi) + log_dets[k]
                   + np.sum(z * z * inv_covs[k], axis=1))
    return logp


def _fit_em(obs, n_states: int = 3, n_iter: int = 120, tol: float = 1e-5,
            seed: int = 0):
    """Baum-Welch EM with Rabiner-scaled forward-backward (robust on long
    sequences: alpha/beta are renormalized each step, log-likelihood is
    accumulated from the per-step scale factors)."""
    n, d = obs.shape
    pi = np.full(n_states, 1.0 / n_states)
    A = np.full((n_states, n_states), 0.2)
    A[np.diag_indices(n_states)] = 0.6
    A /= A.sum(axis=1, keepdims=True)
    km = obs.min(axis=0)
    kmax = obs.max(axis=0)
    means = np.array([km + (kmax - km) * (k + 0.5) / n_states
                      for k in range(n_states)])
    covs = np.tile(np.var(obs, axis=0) * 0.5 + 1e-9, (n_states, 1))
    prev_ll = -np.inf
    for it in range(n_iter):
        inv_covs = 1.0 / covs
        log_dets = np.log(covs).sum(axis=1)
        B = np.exp(np.column_stack(
            [_log_gauss(obs, means, inv_covs, log_dets, k)
             for k in range(n_states)]))
        # forward with per-step scaling
        alpha = np.zeros((n, n_states))
        scale = np.zeros(n)
        alpha[0] = pi * B[0]
        scale[0] = alpha[0].sum()
        alpha[0] /= scale[0] + 1e-300
        for t in range(1, n):
            alpha[t] = B[t] * (alpha[t - 1] @ A)
            scale[t] = alpha[t].sum()
            alpha[t] /= scale[t] + 1e-300
        ll = float(np.log(scale + 1e-300).sum())
        # backward with the same scales
        beta = np.zeros((n, n_states))
        beta[-1] = 1.0
        for t in range(n - 2, -1, -1):
            beta[t] = (A @ (B[t + 1] * beta[t + 1])) / (scale[t + 1] + 1e-300)
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True) + 1e-300
        xi = np.zeros((n - 1, n_states, n_states))
        for t in range(n - 1):
            m = alpha[t][:, None] * A * (B[t + 1] * beta[t + 1])[None, :]
            xi[t] = m / (m.sum() + 1e-300)
        pi = gamma[0] / (gamma[0].sum() + 1e-300)
        A = xi.sum(axis=0) + 1e-9
        A /= A.sum(axis=1, keepdims=True)
        gsum = gamma.sum(axis=0)
        means = (gamma.T @ obs) / (gsum[:, None] + 1e-300)
        for k in range(n_states):
            diff = obs - means[k]
            covs[k] = np.diag(
                (gamma[:, k, None] * diff).T @ diff / (gsum[k] + 1e-300))
            covs[k] = np.maximum(covs[k], 1e-9)
        if abs(ll - prev_ll) < tol:
            break
        prev_ll = ll
    return pi, A, means, covs


def _label_states(means, covs):
    """Label states by volatility rank; the highest-|mean| state is TREND."""
    stds = np.sqrt(covs[:, 0])
    order = np.argsort(stds)  # 0 = calmest
    labels = [None] * len(stds)
    labels[order[0]] = "CALM"
    labels[order[-1]] = "VOLATILE"
    if len(order) == 3:
        mid, hi = order[1], order[2]
        labels[order[1]] = "TREND" if abs(means[hi, 0]) <= abs(means[mid, 0]) \
            else "CHOP"
    elif len(order) == 2:
        labels[order[1]] = "TREND"
    return labels


class RegimeHMM:
    """Gaussian HMM regime classifier with persistent state mapping."""

    def __init__(self, n_states: int = 3, vol_window: int = 20,
                 factors: dict | None = None):
        self.n_states = n_states
        self.vol_window = vol_window
        self.factors = dict(DEFAULT_FACTORS)
        if factors:
            self.factors.update(factors)
        self.pi = None
        self.A = None
        self.means = None
        self.covs = None
        self.labels = None
        self.fitted = False

    def fit(self, df):
        obs = _observations(df, self.vol_window)
        self.pi, self.A, self.means, self.covs = _fit_em(obs, self.n_states)
        self.labels = _label_states(self.means, self.covs)
        self.fitted = True
        return self

    def _log_B(self, obs):
        inv_covs = 1.0 / self.covs
        log_dets = np.log(self.covs).sum(axis=1)
        return np.column_stack([_log_gauss(obs, self.means, inv_covs, log_dets, k)
                                for k in range(self.n_states)])

    def regime_probs(self, df):
        """Causal filtered state probs per bar (no lookahead)."""
        if not self.fitted:
            raise RuntimeError("RegimeHMM not fitted")
        obs = _observations(df, self.vol_window)
        B = self._log_B(obs)
        n = len(B)
        alpha = np.zeros((n, self.n_states))
        alpha[0] = self.pi * B[0]
        scale0 = alpha[0].sum()
        alpha[0] /= scale0 + 1e-300
        for t in range(1, n):
            alpha[t] = B[t] * (alpha[t - 1] @ self.A)
            s = alpha[t].sum()
            alpha[t] /= s + 1e-300
        return alpha

    def regime(self, df):
        """Label + probs of the most recent bar."""
        probs = self.regime_probs(df)
        k = int(np.argmax(probs[-1]))
        final = np.clip(probs[-1], 0.0, 1.0)
        final /= final.sum() + 1e-300
        return {
            "label": self.labels[k],
            "state": k,
            "probs": {self.labels[j]: round(float(final[j]), 4)
                      for j in range(self.n_states)},
            "size_factor": self.factors.get(self.labels[k], 1.0),
        }

    def save(self, path):
        data = {
            "n_states": self.n_states,
            "vol_window": self.vol_window,
            "factors": self.factors,
            "pi": self.pi.tolist(),
            "A": self.A.tolist(),
            "means": self.means.tolist(),
            "covs": self.covs.tolist(),
            "labels": self.labels,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        hmm = cls(n_states=data["n_states"], vol_window=data["vol_window"],
                  factors=data.get("factors"))
        hmm.pi = np.array(data["pi"])
        hmm.A = np.array(data["A"])
        hmm.means = np.array(data["means"])
        hmm.covs = np.array(data["covs"])
        hmm.labels = data["labels"]
        hmm.fitted = True
        return hmm