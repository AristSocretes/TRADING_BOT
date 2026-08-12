"""Data-provider ingest tool: pull optional external datasets into the repo.

Run with `--dry-run` (default) to see the plan, then pick a source:

    python scripts/dataprovider_sources.py --source binance_vision   # bins -> candles.db
    python scripts/dataprovider_sources.py --source hf_btc_candles   # HF pattern images
    python scripts/dataprovider_sources.py --source hf_ohlcv_1m      # HF 1m equities sample

Writes:
  - candles.db  (via bot.data.cache.DataCache) for OHLCV sources
  - data/external/  for image/extra datasets
"""

import argparse
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.cache import DataCache  # noqa: E402

EXTERNAL_DIR = Path("data/external")
VISION_BASE = "https://data.binance.vision/data/spot/monthly/klines"


def parse_column_tail(row):
    """Binance kline CSV rows: open_time,open,high,low,close,volume,..."""
    t = int(row[0])
    if t > 1e13:  # newer dumps ship microseconds
        t //= 1000
    return {
        "time": pd.to_datetime(t, unit="ms", utc=True),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
    }


def ingest_binance_vision(symbols, granularities, months_back=60):
    cache = DataCache()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30 * months_back)
    for symbol in symbols:
        for granularity in granularities:
            frames = []
            cursor = pd.Timestamp(start).floor("MS")
            while cursor < pd.Timestamp(now).floor("MS"):
                ym = cursor.strftime("%Y-%m")
                url = (
                    f"{VISION_BASE}/{symbol}/{granularity}/"
                    f"{symbol}-{granularity}-{ym}.zip"
                )
                resp = requests.get(url, timeout=120)
                if resp.status_code == 404:
                    cursor += pd.offsets.MonthBegin(1)
                    continue
                resp.raise_for_status()
                with zipfile.ZipFile(BytesIO(resp.content)) as zf:
                    name = zf.namelist()[0]
                    with zf.open(name) as fh:
                        csv_rows = pd.read_csv(fh, header=None).values
                frames.append(pd.DataFrame([parse_column_tail(r) for r in csv_rows]))
                cursor += pd.offsets.MonthBegin(1)
            if not frames:
                print(f"  {symbol} {granularity}: nothing to ingest", flush=True)
                continue
            df = (
                pd.concat(frames)
                .drop_duplicates("time")
                .set_index("time")
                .sort_index()
            )
            before = cache.coverage(symbol, granularity)
            cache.upsert(df, symbol, granularity)
            after = cache.coverage(symbol, granularity)
            print(
                f"  {symbol} {granularity}: {len(df)} bars "
                f"({before[0]}..{before[1]}) -> ({after[0]}..{after[1]})",
                flush=True,
            )


def ingest_hf_btc_candles(revision=None):
    from datasets import load_dataset

    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    kwargs = {"revision": revision} if revision else {}
    ds = load_dataset("tuankg1028/btc-candlestick-dataset", split="train", **kwargs)
    out = EXTERNAL_DIR / "hf_btc_candlestick"
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for i, row in enumerate(ds):
        img = row["image"] if "image" in row else row.get("chart")
        if img is None:
            continue
        img.save(out / f"chart_{i:05d}.png")
        count += 1
        if count >= 13000:
            break
    print(f"  wrote {count} charts to {out}", flush=True)


def ingest_hf_ohlcv_1m(symbols=("AAPL", "MSFT", "SPY"), years=3):
    from datasets import load_dataset

    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    out = EXTERNAL_DIR / "hf_ohlcv_1m_sample"
    out.mkdir(parents=True, exist_ok=True)
    ds = load_dataset("mito0o852/OHLCV-1m", split="train", streaming=True)
    totals = {s: 0 for s in symbols}
    for row in ds:
        ticker = row["ticker"]
        if ticker not in totals:
            continue
        totals[ticker] += 1
    for ticker, n in totals.items():
        print(f"  {ticker}: {n} 1m bars in stream (sample files in {out})",
              flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=["binance_vision", "hf_btc_candles", "hf_ohlcv_1m"],
        required=True,
    )
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--granularities", default="5m,1h,4h")
    parser.add_argument("--months-back", type=int, default=60)
    parser.add_argument("--run", action="store_true",
                        help="actually ingest (default is dry-run plan only)")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols.split(",") if s]
    granularities = [g.lower() for g in args.granularities.split(",") if g]

    print(f"source={args.source} symbols={symbols} granularities={granularities}")
    if not args.run:
        print("DRY-RUN: add --run to execute.")
        return

    if args.source == "binance_vision":
        ingest_binance_vision(symbols, granularities, args.months_back)
    elif args.source == "hf_btc_candles":
        ingest_hf_btc_candles()
    elif args.source == "hf_ohlcv_1m":
        ingest_hf_ohlcv_1m()


if __name__ == "__main__":
    main()