"""Data-provider refresh: bring every (symbol, granularity) slice in the
SQLite cache up to the latest closed bar, and backfill any empty slices.

Safe to run while training processes (train_many.py / sweep_prod.py) are
live: reads and writes go through separate SQLite connections, upserts are
INSERT OR REPLACE, and only missing/new bars are fetched from Binance.

Usage:
    python scripts/refresh_cache.py
    python scripts/refresh_cache.py --symbols BTCUSDT --granularities 5m
    python scripts/refresh_cache.py --days 3 --history-years 5
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.cache import DataCache  # noqa: E402
from bot.data.clients import make_data_client  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
GRANULARITIES = ["5m", "1h", "4h", "1m"]


def parse_list(raw, default):
    if not raw:
        return default
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="comma list (default all)")
    parser.add_argument("--granularities", default="", help="comma list (default all)")
    parser.add_argument("--days", type=int, default=4,
                        help="backfill window in days for the fresh tail")
    parser.add_argument("--history-years", type=int, default=5,
                        help="full history to backfill slices that are empty")
    args = parser.parse_args()

    symbols = parse_list(args.symbols, SYMBOLS)
    granularities = parse_list(args.granularities, GRANULARITIES)
    cache = DataCache()
    client = make_data_client()
    now = datetime.now(timezone.utc)

    print(f"Client: {type(client).__name__} | cache: {cache.path}", flush=True)
    for symbol in symbols:
        for granularity in granularities:
            before = cache.coverage(symbol, granularity)
            have_start, have_end, count = before
            if not count:
                start = now - timedelta(days=365 * args.history_years)
                end = now
                mode = "BACKFILL (was empty)"
            else:
                start = now - timedelta(days=args.days)
                end = now
                mode = "REFRESH tail"
            cache.ensure_range(client, symbol, granularity, start, end)
            after = cache.coverage(symbol, granularity)
            got = after[2] - (count or 0)
            print(
                f"{mode:20s} {symbol:8s} {granularity:3s}: "
                f"rows {count} -> {after[2]} (+{got}) | "
                f"now covers {after[0]} .. {after[1]}", flush=True,
            )

    print("Done.", flush=True)


if __name__ == "__main__":
    main()