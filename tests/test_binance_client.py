from bot.data.binance_client import parse_klines


def test_parse_klines():
    rows = [
        [1704067200000, "42000.0", "42100.0", "41900.0", "42050.0", "10.5"],
        [1704067500000, "42050.0", "42200.0", "42000.0", "42100.0", "12.0"],
    ]
    df = parse_klines(rows)
    assert len(df) == 2
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 42100.0
    assert df.index[0].tz is not None


def test_parse_empty():
    df = parse_klines([])
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
