import numpy as np
import pandas as pd
import pytest

from bot.data.features import add_features, rsi


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


def test_rsi_bounds(df):
    values = rsi(df["close"], 14).dropna()
    assert ((values >= 0) & (values <= 100)).all()


def test_no_lookahead(df):
    full = add_features(df)
    for cut in (100, 250, 400):
        prefix = add_features(df.iloc[:cut])
        assert np.allclose(prefix["rsi"].values, full["rsi"].iloc[:cut].values, equal_nan=True)
        assert np.allclose(prefix["macd"].values, full["macd"].iloc[:cut].values, equal_nan=True)
        assert np.allclose(
            prefix["atr_pct"].values, full["atr_pct"].iloc[:cut].values, equal_nan=True
        )
        assert np.allclose(
            prefix["rvol_24"].values, full["rvol_24"].iloc[:cut].values, equal_nan=True
        )
        assert np.allclose(
            prefix["trend_24"].values, full["trend_24"].iloc[:cut].values, equal_nan=True
        )
        assert np.allclose(
            prefix["volume_ratio"].values, full["volume_ratio"].iloc[:cut].values, equal_nan=True
        )


def test_feature_columns_present(df):
    out = add_features(df)
    for col in (
        "ret_1",
        "ret_4",
        "ret_24",
        "macd",
        "rsi",
        "atr_pct",
        "boll_pctb",
        "hour_sin",
        "dow_cos",
    ):
        assert col in out.columns


def test_candlestick_pattern_columns(df):
    out = add_features(df)
    for col in (
        "hammer", "hanging_man", "shooting_star", "inverted_hammer",
        "marubozu", "closing_marubozu", "belt_hold",
        "spinning_top", "high_wave",
        "dragonfly_doji", "gravestone_doji", "long_legged_doji",
        "harami", "harami_cross", "piercing_line", "dark_cloud_cover",
        "tweezer_top", "tweezer_bottom", "matching_low",
        "morning_star", "evening_star", "morning_doji_star", "evening_doji_star",
        "three_white_soldiers", "three_black_crows",
        "three_inside_up", "three_inside_down",
        "three_outside_up", "three_outside_down", "abandoned_baby",
    ):
        assert col in out.columns
        assert out[col].abs().max() <= 1.0 + 1e-9


def test_patterns_no_lookahead(df):
    full = add_features(df)
    for cut in (100, 250):
        prefix = add_features(df.iloc[:cut])
        for col in ("engulfing", "morning_star", "cdl_score"):
            assert np.allclose(
                prefix[col].values, full[col].iloc[:cut].values, equal_nan=True
            )


def test_talib_patterns_match_reference(df):
    talib = pytest.importorskip("talib")
    out = add_features(df)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    ref_map = {
        "hammer": "CDLHAMMER",
        "shooting_star": "CDLSHOOTINGSTAR",
        "doji": "CDLDOJI",
        "marubozu": "CDLMARUBOZU",
        "engulfing": "CDLENGULFING",
        "harami": "CDLHARAMI",
        "piercing_line": "CDLPIERCING",
        "morning_star": "CDLMORNINGSTAR",
    }
    for col, fn in ref_map.items():
        ref = np.sign(getattr(talib, fn)(o, h, low, c))
        mine = np.sign(out[col].to_numpy())
        # NaN-free and identical except TA-Lib warmup rows (label 0)
        assert np.array_equal(mine, ref)
