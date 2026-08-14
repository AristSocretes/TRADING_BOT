import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\SUSHANT\Desktop\TRADING_BOT")

from bot.data.cache import DataCache
from bot.data.clients import make_data_client
from bot.data.yahoo_client import YahooClient

START = datetime(2024, 8, 1, tzinfo=timezone.utc)

PLAN = [
    # (symbol, gran, start, client_kind)
    ("PAXGUSDT", "5m", START, "binance"),
    ("PAXGUSDT", "1h", START, "binance"),
    ("PAXGUSDT", "4h", START, "binance"),
    ("PAXGUSDT", "1d", START, "binance"),
    ("^GSPC", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("^IXIC", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("^FTSE", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("^GDAXI", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("^N225", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("EURUSD=X", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("USDINR=X", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
    ("SLV", "1d", datetime(2010, 1, 1, tzinfo=timezone.utc), "yahoo"),
]

cache = DataCache()
binance = make_data_client()
yahoo = YahooClient()

for symbol, gran, start, kind in PLAN:
    t0 = time.time()
    client = binance if kind == "binance" else yahoo
    now = datetime.now(timezone.utc)
    try:
        cache.ensure_range(client, symbol, gran, start, now)
        cov = cache.coverage(symbol, gran)
        print(f"{symbol:12s} {gran:3s}: rows={cov[2]:>7} "
              f"{cov[0]} -> {cov[1]} ({time.time()-t0:.1f}s)", flush=True)
    except Exception as exc:
        print(f"{symbol:12s} {gran:3s}: FAIL {type(exc).__name__}: {exc}", flush=True)
