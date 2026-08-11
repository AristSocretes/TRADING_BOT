import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.cache import DataCache  # noqa: E402
from bot.data.clients import make_data_client  # noqa: E402
from bot.data.quality import sanity_report  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--granularity", default="5m")
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    cache = DataCache()
    client = make_data_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * args.years)
    df = cache.ensure_range(client, args.symbol, args.granularity, start, end)
    print("Coverage:", cache.coverage(args.symbol, args.granularity))
    print("Sanity:", sanity_report(df))
    print(df.tail())


if __name__ == "__main__":
    main()
