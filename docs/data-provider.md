# Data Provider Manifest — role=dataprovider

**Generated:** 2026-08-12 · **Refresh:** Binance live klines · Verif. date 2026-08-12

This is the feed delivered to the two background training agents
(`train_many.py` — 1h/4h BTC/ETH/SOL, and `sweep_prod.py` — seeds×configs
sweep), plus a catalog of every dataset source this project knows about,
with the exact command to ingest each one.

## 1. What was just delivered (check/timestamp on 2026-08-12)

All rows live in `data/candles.db` (schema: symbol, granularity, time, ohlcv),
read by `bot/data/cache.py`. Trainers load per (symbol, granularity) at pair
start, so nothing had to be restarted.

| Source | Symbol | Granularity | Rows | Coverage (UTC) | Delta |
|---|---|---|---|---|---|
| Binance REST | BTCUSDT | 5m | 211,072 | 2024-08-09 .. 2026-08-12 07:35 | +1 |
| Binance REST | BTCUSDT | 1h | 43,859 | 2021-08-10 .. 2026-08-12 07:00 | +66 |
| Binance REST | BTCUSDT | 4h | 10,965 | 2021-08-10 .. 2026-08-12 04:00 | +10 |
| Binance REST | BTCUSDT | 1m | 264,712 | 2026-02-09 .. 2026-08-12 07:36 | +1,192 |
| Binance REST | ETHUSDT | 5m | 213,500 | 2024-08-01 .. 2026-08-12 07:35 | +307 |
| Binance REST | **ETHUSDT** | **1h** | **43,797** | **2021-08-13 .. 2026-08-12 07:00** | **was EMPTY — backfilled** |
| Binance REST | ETHUSDT | 4h | 10,963 | 2021-08-11 .. 2026-08-12 04:00 | +13 |
| Binance REST | ETHUSDT | 1m | 264,712 | 2026-02-09 .. 2026-08-12 07:36 | +1,192 |
| Binance REST | SOLUSDT | 5m | 213,500 | 2024-08-01 .. 2026-08-12 07:35 | +307 |
| Binance REST | **SOLUSDT** | **1h** | **43,797** | **2021-08-13 .. 2026-08-12 07:00** | **was EMPTY — backfilled** |
| Binance REST | SOLUSDT | 4h | 10,963 | 2021-08-11 .. 2026-08-12 04:00 | +13 |
| Binance REST | SOLUSDT | 1m | 264,712 | 2026-02-09 .. 2026-08-12 07:36 | +1,192 |

**Quality gate:** for the two new 1h series — 0 nulls, 0 negative volumes,
0 OHLC inconsistencies, 2 large gaps (known Binance outages). Re-run anytime:
`python scripts/refresh_cache.py`.

> Without this feed, `sweep_prod.py` and `train_many.py` would have printed
> `SKIP ETHUSDT 1h: no data` / `SKIP SOLUSDT 1h: no data` when they reached
> those pairs. The backfill unblocked 4 (symbol × granularity) training runs.

## 2. Every data source catalog (all sources, all access methods)

### 2.1 Live / REST (no key, continuous)

| # | Source | What | Access | Notes |
|---|---|---|---|---|
| 1 | Binance public klines (`/api/v3/klines`) | OHLCV BTC/ETH/SOL all intervals | REST, free | **Primary feed.** Wrapped in `bot/data/binance_client.py`; `scripts/refresh_cache.py` keeps it fresh |
| 2 | Binance **data.binance.vision** dumps | Monthly/daily klines ZIPs, all intervals (1s..1mo); aggTrades/trades too | `data.binance.vision/data/spot/{monthly,daily}/klines/...` + `.CHECKSUM` | Faster bulk backfill than REST. `scripts/dataprovider_sources.py --source binance_vision --run` |
| 3 | Binance testnet / futures (`fapi`) | Funding rates, mark/index klines, order book | REST/key | Only if going live; funding is 8-hourly |
| 4 | yfinance downloader | NASDAQ/daily+crypto history | pip `yfinance`, `downloader.py --all` | Cross-check of close prices |

### 2.2 Kaggle (browser login; `kaggle` CLI)

| # | Dataset | Contents | Ingest |
|---|---|---|---|
| 5 | Candle Stick Patterns (500+ unique) | 35k synthetic 40×40 pattern images | `kaggle datasets download -d <slug> -p data/external` |
| 6 | Human Labeled OHLCV (barathanaslan/human-labeled-synthetic-stock-market-data) | 1,219 JSON, 300-candle windows with support/resistance + trendline labels | same pattern |
| 7 | FOREX Candlestick Patterns | CSV AAPL w/ binary pattern flags | same |
| 8 | Historical Forex Data (16 txt, >6 GB, 1m pairs 2001–2023) | EUR/USD, GBP/JPY ... | same |
| 9 | FX Candlestick images from TradingView | 152 exports, UP/DOWN folders (CNN trend training) | same |

### 2.3 Hugging Face (programmatic, `datasets`)

| # | Dataset | Contents | Verified | Ingest |
|---|---|---|---|---|
| 10 | `tuankg1028/btc-candlestick-dataset` | 13,081 BTCUSDT chart PNGs + text + trading labels | **alive, updated 2025-06-30** | `--source hf_btc_candles --run` |
| 11 | `mito0o852/OHLCV-1m` | 87.7 GB 1m US stocks 1992–2026 (from Finnhub), parquet/mo | **alive (10,271 dl/mo)** | stream-only: `--source hf_ohlcv_1m` (sample first) |
| 12 | `Wrigggy/crypto-ohlcv-1m` | 90 d of Binance 1m spot (~128 MB, cc-by-4.0) | **alive 2026-03-17, now gated (share contact info)** | `load_dataset("parquet", data_dir="data")` |

### 2.4 Research / generated / free archives

| # | Source | What | Access |
|---|---|---|---|
| 13 | TA-Lib CDL* catalog (61 functions) | **Authoritative pattern feature provider** — already default in `bot/data/features.py` (native fallback if `talib` absent) | `pip install TA-Lib` or repo-native |
| 14 | hfdatalibrary.com | 1.5B rows 1m US equities/ETFs (1,391 tickers, 2002–now), Raw+Clean, 25 academic vars, REST API, MCP | free key, CC BY 4.0 |
| 15 | cryptodatadownload.com | Free OHLCV CSV (1d/1h/1m) for 20+ exchanges, since 2017 | no login, refreshed 00:00 UTC |
| 16 | pricegenerator (pip) | Infinite synthetic OHLCV walks with controlled stats | `pip install pricegenerator` |
| 17 | DPP framework (Taiwan Futures TX) | 113.2 MB CSV in paper supplements | paper site |
| 18 | PRML framework | 2-/3-day pattern permutation ZIPs | semen.buaa.edu.cn (Yang Haijun profile) |
| 19 | "Enhancing Market Trend" study | EUR/USD live data at forexsb.com + CNN impl on GitHub | as linked in docs/data-sources.md |
| 20 | Academic: Formal candlestick classification (Applied Soft Computing 2019) | 103 patterns in first-order logic → rule-based synthetic data generator | sciencedirect DOI 10.1016/j.asoc.2019.105700 |
| 21 | Academic: Intraday forecasts using candlestick patterns in crypto (2026) | ~15M pattern instances; 1,935 pairs hourly, 55 TA-Lib patterns | ScienceDirect S1059056026002716 (results/context only) |

## 3. Consumption model for the two agents

```
scripts/{train_many,sweep_prod}.py --device auto(cuda)          (agent 1: 1h/4h — now unblocked)
        ^
        |  bot.data.cache.DataCache.load(symbol, granularity)
        |
data/candles.db  <==  refresh_cache.py (live REST) / dataprovider_sources.py (vision backfill)
data/external/   <==  pattern images / parquet samples (supervised/CNN experiments)
```

- `python scripts/refresh_cache.py` — incremental catch-up feed (run it in a
  schedule; safe while trainers are live — separate SQLite connections).
- `python scripts/dataprovider_sources.py --source binance_vision --run`
  — bulk backfill via Binance vision ZIPs.
- Upstream source catalog with pattern usage: `docs/data-sources.md`.

## 4. Environment facts (what the "NPU" processes really are)

The two background agents are the Python training processes on this machine —
both consume the cache above, not live prompts:

| Agent | PID | Command | Device | Memory (peak WS) |
|---|---|---|---|---|
| Sweeper | sweep_prod | seeds×configs sweep (BTC 5m → … → SOL 1m/4h) | cuda (RTX 5050 Laptop, 8.5 GB) | ~3.3 GB |
| Trainer | train_many | 500k steps, 128 envs, 1h+4h × BTC/ETH/SOL | cuda | ~4.5 GB |

Verified alive and unbumped during the feed.