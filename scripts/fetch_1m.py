import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.cache import DataCache
from bot.data.clients import make_data_client

cache = DataCache()
client = make_data_client()
end = datetime.now(timezone.utc)
start = end - timedelta(days=183)
for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
    print(sym, "fetching 1m", start.date(), "->", end.date(), flush=True)
    df = cache.ensure_range(client, sym, "1m", start, end)
    print(sym, "coverage:", cache.coverage(sym, "1m"), flush=True)
print("done")
