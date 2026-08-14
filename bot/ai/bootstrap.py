"""Statistical significance tools for strategy evaluation.

Implements the standard industry inference tests for backtest Sharpe ratios:

- Lo (2002) asymptotic standard error of the Sharpe ratio
  ``SE(SR) = sqrt((1 + 0.5 * SR^2) / T)``
- Bailey & Lopez de Prado (2012) Probabilistic Sharpe Ratio (PSR), which
  measures the probability that the true Sharpe ratio exceeds a benchmark
  Sharpe ratio (default 0), i.e. the probability the strategy is genuinely
  profitable rather than a lucky drawdown of the noise.
- Politis & Romano (1994) stationary block bootstrap for finite-sample
  confidence intervals of the Sharpe ratio, robust to autocorrelation.
"""

import numpy as np


def sharpe_ratio(returns, periods_per_year=252, risk_free=0.0):
    """Annualized Sharpe ratio of a return series (zero-mean excess)."""
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size == 0:
        return 0.0
    excess = returns - risk_free / periods_per_year
    std = excess.std(ddof=1)
    if not np.isfinite(std) or std <= 0.0:
        return 0.0
    sr = excess.mean() / std
    return sr * np.sqrt(periods_per_year)


def sharpe_se(sharpe, n_obs, periods_per_year=252):
    """Lo (2002) asymptotic standard error of the annualized Sharpe ratio.

    Per-period: SE(SR_period) = sqrt((1 + 0.5 * SR_period^2) / T).
    Annualizing: SE(SR_annual) = sqrt((ppy + 0.5 * SR_annual^2) / T).
    """
    sharpe = float(sharpe)
    n = int(n_obs)
    if n < 2:
        return np.inf
    ppy = float(periods_per_year)
    return np.sqrt((ppy + 0.5 * sharpe**2) / n)


def sharpe_ci(sharpe, n_obs, level=0.95, periods_per_year=252):
    """Normal-approximation CI for the annualized Sharpe ratio (Lo 2002)."""
    from scipy import stats

    se = sharpe_se(sharpe, n_obs, periods_per_year=periods_per_year)
    z = stats.norm.ppf(0.5 + level / 2.0)
    return float(sharpe - z * se), float(sharpe + z * se)


def psr(returns, benchmark_sharpe=0.0, periods_per_year=252):
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012).

    Probability that the true annualized Sharpe ratio exceeds
    ``benchmark_sharpe``, given the sample of returns. Uses the non-normal
    corrected variance: Var(SR) = (1 - skew*SR + (kurt-1)/4 * SR^2) / T.
    """
    from scipy import stats

    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 2:
        return 0.5
    sr = sharpe_ratio(returns, periods_per_year=periods_per_year)
    skew = float(stats.skew(returns))
    kurt = float(stats.kurtosis(returns, fisher=True))  # excess kurtosis
    sr_bar = sr / np.sqrt(periods_per_year)
    bench = benchmark_sharpe / np.sqrt(periods_per_year)
    var = (
        1 - skew * sr_bar + (kurt - 1) / 4.0 * sr_bar**2
    ) / returns.size
    if var <= 0.0:
        return 0.5
    z = (sr_bar - bench) / np.sqrt(var)
    return float(stats.norm.cdf(z))


def stationary_bootstrap(returns, n_boot=1000, mean_block=20, seed=0):
    """Politis & Romano (1994) stationary block bootstrap.

    Returns the array of bootstrapped annualized Sharpe ratios; callers can
    take percentiles for CIs. Block length is geometric with expectation
    ``mean_block``, so resampled series are autocorrelation-robust.
    """
    rng = np.random.default_rng(seed)
    returns = np.asarray(returns, dtype=np.float64)
    n = returns.size
    if n < 2:
        return np.zeros(n_boot)
    p = 1.0 / max(float(mean_block), 1.0)
    out = np.empty(n_boot, dtype=np.float64)
    idx = np.arange(n)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n)
        lens = rng.geometric(p, size=n).astype(np.int64)
        lens = np.minimum(lens, n)
        ends = starts + lens
        ends = np.minimum(ends, n)
        samp = np.concatenate(
            [idx[s:e] for s, e in zip(starts, ends)]
        )
        out[b] = sharpe_ratio(returns[samp])
    return out


def bootstrap_ci(returns, n_boot=1000, mean_block=20, level=0.95, seed=0):
    """Percentile CI of the annualized Sharpe ratio via stationary bootstrap."""
    boot = stationary_bootstrap(returns, n_boot, mean_block, seed)
    lo = (1 - level) / 2.0
    return float(np.percentile(boot, 100 * lo)), float(np.percentile(boot, 100 * (1 - lo)))
