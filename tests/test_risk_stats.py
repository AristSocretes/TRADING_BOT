import numpy as np
import pandas as pd
import pytest

from bot.ai.bootstrap import (
    bootstrap_ci,
    psr,
    sharpe_ci,
    sharpe_ratio,
    sharpe_se,
    stationary_bootstrap,
)
from bot.ai.regime_hmm import RegimeHMM
from bot.data.features import add_features, ewma_variance, garch11_variance


@pytest.fixture
def returns():
    rng = np.random.default_rng(42)
    return rng.normal(0.0005, 0.01, 1000)


# ---------------------------------------------------------------- bootstrap
def test_sharpe_ratio_known_value(returns):
    sr = sharpe_ratio(returns, periods_per_year=252)
    assert 0.0 < sr < 1.0
    assert np.isfinite(sr)


def test_sharpe_ratio_annualizes():
    # Annualization must scale exactly with sqrt(ppy) on the same series
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 2000)
    a = sharpe_ratio(r, periods_per_year=252)
    b = sharpe_ratio(r, periods_per_year=63)
    assert abs(a - 2 * b) < 1e-9


def test_sharpe_se_decreases_with_n():
    sr = 0.5
    se1 = sharpe_se(sr, 100, periods_per_year=252)
    se2 = sharpe_se(sr, 10000, periods_per_year=252)
    assert se2 < se1


def test_sharpe_ci_contains_point_estimate(returns):
    sr = sharpe_ratio(returns, periods_per_year=252)
    lo, hi = sharpe_ci(sr, len(returns), periods_per_year=252)
    assert lo < sr < hi
    assert lo < hi


def test_psr_bounds(returns):
    p = psr(returns, benchmark_sharpe=0.0, periods_per_year=252)
    assert 0.0 <= p <= 1.0


def test_psr_zero_for_losing_strategy():
    rng = np.random.default_rng(3)
    bad = rng.normal(-0.001, 0.01, 500)
    assert psr(bad, benchmark_sharpe=0.0, periods_per_year=252) < 0.5


def test_stationary_bootstrap_shapes(returns):
    idx = stationary_bootstrap(len(returns), mean_block=20, seed=0)
    assert len(idx) == len(returns)
    assert set(idx).issubset(set(range(len(returns))))


def test_bootstrap_ci_covers_zero_for_noise():
    rng = np.random.default_rng(9)
    noise = rng.normal(0.0, 0.01, 800)
    lo, hi = bootstrap_ci(noise, n_boot=200, mean_block=20, seed=0)
    assert lo <= 0.0 <= hi  # a pure-noise curve should straddle zero


def test_bootstrap_ci_positive_for_edge():
    rng = np.random.default_rng(4)
    good = rng.normal(0.01, 0.01, 800)
    lo, hi = bootstrap_ci(good, n_boot=200, mean_block=20, seed=0)
    assert lo > 0.0


# ---------------------------------------------------------------- vol features
@pytest.fixture
def df():
    index = pd.date_range("2024-01-01", periods=500, freq="5min")
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.1, 500))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": 100,
        },
        index=index,
    )


def test_ewma_vol_no_lookahead(df):
    lr = np.log(df["close"] / df["close"].shift(1))
    ppy = 105120
    full = ewma_variance(lr, ppy)
    for cut in (100, 250):
        prefix_lr = lr.iloc[:cut]
        prefix = ewma_variance(prefix_lr, ppy)
        assert np.allclose(prefix.values, full.iloc[:cut].values, equal_nan=True)


def test_garch_vol_no_lookahead(df):
    lr = np.log(df["close"] / df["close"].shift(1))
    ppy = 105120
    full = garch11_variance(lr, ppy)
    for cut in (100, 250):
        prefix_lr = lr.iloc[:cut]
        prefix = garch11_variance(prefix_lr, ppy)
        assert np.allclose(prefix.values, full.iloc[:cut].values, equal_nan=True)


def test_garch_vol_positive_finite(df):
    lr = np.log(df["close"] / df["close"].shift(1))
    out = garch11_variance(lr, 105120).dropna()
    assert (out > 0).all()
    assert np.isfinite(out).all()


def test_ewma_vol_positive_finite(df):
    lr = np.log(df["close"] / df["close"].shift(1))
    out = ewma_variance(lr, 105120).dropna()
    assert (out > 0).all()
    assert np.isfinite(out).all()


def test_vol_features_are_last_columns(df):
    from bot.data.features import FEATURE_COLUMNS

    assert FEATURE_COLUMNS[-5:] == [
        "ewma_vol", "garch_vol", "vol_target_ratio", "vol_spike", "vol_trend",
    ]
    out = add_features(df)
    for col in ("ewma_vol", "garch_vol", "vol_target_ratio", "vol_spike", "vol_trend"):
        assert col in out.columns


def test_vol_columns_reproducible(df):
    a = add_features(df)[["ewma_vol", "garch_vol"]]
    b = add_features(df)[["ewma_vol", "garch_vol"]]
    assert a.equals(b)


# ---------------------------------------------------------------- regime HMM
def test_regime_fit_and_predict(df):
    hmm = RegimeHMM().fit(df)
    assert hmm.fitted
    r = hmm.regime(df)
    assert r["label"] in ("CALM", "TREND", "VOLATILE", "CHOP")
    assert 0.0 < r["size_factor"] <= 1.0
    total = sum(r["probs"].values())
    assert abs(total - 1.0) < 1e-6


def test_regime_save_load_roundtrip(df, tmp_path):
    hmm = RegimeHMM().fit(df)
    path = tmp_path / "regime.json"
    hmm.save(path)
    loaded = RegimeHMM.load(path)
    r1 = hmm.regime(df)
    r2 = loaded.regime(df)
    assert r1["label"] == r2["label"]
    assert abs(sum(r1["probs"].values()) - 1.0) < 1e-6


def test_regime_probs_causal(df):
    hmm = RegimeHMM().fit(df)
    probs = hmm.regime_probs(df)
    assert probs.shape == (len(df) - 3, 3)  # 1 ret + 2 vol warmup rows dropped
    assert np.isfinite(probs).all()
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-6)
    # prefix computation must agree with the full-window filter (no lookahead)
    prefix = hmm.regime_probs(df.iloc[:300])
    assert prefix.shape[0] == 297
    assert np.allclose(prefix[-1], probs[296], atol=1e-6)