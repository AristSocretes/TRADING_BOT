import pandas as pd

from bot.data.cache import DataCache


class FakeClient:
    def __init__(self, df):
        self.df = df

    def fetch_history(self, symbol, granularity, start, end):
        return self.df


def make_df(periods=100):
    index = pd.date_range("2024-01-01", periods=periods, freq="5min")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=index
    )


def test_roundtrip(tmp_path):
    df = make_df()
    cache = DataCache(path=tmp_path / "c.db")
    cache.upsert(df, "EUR_USD", "M5")
    out = cache.load("EUR_USD", "M5")
    assert len(out) == 100
    assert out["close"].iloc[-1] == 1.0


def test_ensure_range_fetches_and_caches(tmp_path):
    df = make_df()
    cache = DataCache(path=tmp_path / "c.db")
    result = cache.ensure_range(FakeClient(df), "EUR_USD", "M5", df.index[0], df.index[-1])
    assert len(result) == 100
    assert cache.coverage("EUR_USD", "M5")[2] == 100


def test_ensure_range_returns_cached(tmp_path):
    df = make_df()
    cache = DataCache(path=tmp_path / "c.db")
    cache.upsert(df, "EUR_USD", "M5")
    result = cache.ensure_range(FakeClient(df), "EUR_USD", "M5", df.index[0], df.index[-1])
    assert len(result) == 100
