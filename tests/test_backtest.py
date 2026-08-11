import numpy as np
import pandas as pd
import pytest

from bot.ai.backtest import backtest, metrics


@pytest.fixture
def df():
    index = pd.date_range("2024-01-01", periods=200, freq="5min")
    close = np.linspace(1.0, 1.5, 200)
    return pd.DataFrame({"close": close}, index=index)


def test_flat_when_no_trades(df):
    signals = np.zeros(len(df), dtype=int)
    curve, trades = backtest(df, signals, spread=0.0002)
    assert np.allclose(curve, curve.iloc[0])
    assert len(trades) == 0


def test_long_profits(df):
    signals = np.zeros(len(df), dtype=int)
    signals[10:100] = 1
    curve, _ = backtest(df, signals, spread=0.0002)
    assert curve.iloc[-1] > curve.iloc[0]


def test_metrics_keys(df):
    signals = np.zeros(len(df), dtype=int)
    signals[10:100] = 1
    curve, trades = backtest(df, signals)
    result = metrics(curve, trades)
    assert {
        "total_return",
        "sharpe",
        "max_drawdown",
        "n_trades",
        "win_rate",
        "profit_factor",
    } <= set(result)
