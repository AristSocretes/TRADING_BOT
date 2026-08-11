# Candlestick Pattern Data Sources

Catalog of external places to get candlestick-pattern data, collected while
searching for "all candlestick patterns". Pattern *definitions* come from the
official TA-Lib CDL\* catalog (61 functions) - used as the authoritative
feature provider in `bot/data/features.py` (native fallback if TA-Lib is not
installed). The datasets below are for training/eval material.

## 1. Kaggle

| Dataset | Contents |
| --- | --- |
| "Candle Stick Patterns (500+ Unique)" | 35,000+ synthetic 40x40 pattern images for unsupervised learning / autoencoders |
| "Human Labeled OHLCV Stock Market Data" | 1,219 JSON files, synthetic 300-candle windows with human-annotated support/resistance + trendlines |
| "FOREX Candlestick Patterns" | CSV for AAPL with binary indicator columns (Doji, Hammer, ...) |
| "Historical Forex Data" | 16 raw text files (>6 GB), 1-minute pairs (EUR/USD, GBP/JPY ...) 2001-2023 |
| "FX Candle Stick images from TradingView" | 152 TradingView exports, "UP"/"Down" folders for CNN trend training |

## 2. Hugging Face (programmatic)

- `load_dataset("tuankg1028/btc-candlestick-dataset")` - 13,081 PNG charts +
  text descriptions + trading labels for BTCUSDT
- `load_dataset("mito0o852/OHLCV-1m", split="train")` - 87.7 GB of 1-minute US
  stock data (1992-2026)
- `load_dataset("parquet", data_dir="data")` (crypto-ohlcv-1m) - 90 days of
  1-minute Binance spot history
- hfdatalibrary.com - 1.53B rows of 1-minute data (free registration for API key)

## 3. Research / tools

- DPP framework - Taiwan Futures Exchange TX dataset (113.2 MB CSV, in paper
  supplemental materials)
- PRML framework - two-day / three-day pattern permutation ZIPs
  (semen.buaa.edu.cn/Faculty/Finance/YANG_Haijun/Profile.htm)
- "Enhancing Market Trend" study - EUR/USD live data at forexsb.com; CNN impl
  + processed samples on GitHub
- `pip install pricegenerator` - generate infinite synthetic OHLCV random
  walks with controlled statistics
- YfinanceDownloader repo - `python downloader.py --all` for NASDAQ history

## How this repo uses patterns

- `bot/data/features.py` - 30 pattern features (+`cdl_score`), official TA-Lib
  values when `import talib` succeeds, otherwise a native vectorized port.
- Features feed the PPO observation space (window of normalized features),
  supervised model, and backtests - no changes needed at call sites.
