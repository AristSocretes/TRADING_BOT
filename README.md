# AI Crypto Trading Bot

Reinforcement-learning crypto trading bot using Binance public market data. Paper trading first.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

No API key is needed for data — Binance public klines are free. Edit `.env` only if you want
Binance testnet execution or the optional forex (OANDA) mode.

## Scripts

| Command | Purpose | Phase |
|---|---|---|
| `python scripts/check_connection.py` | Verify Binance connection (BTCUSDT price) | 0 |
| `python scripts/fetch_data.py --symbol BTCUSDT --granularity 5m --years 2` | Download + cache klines | 1 |
| `python scripts/train.py --timesteps 200000` | Train PPO agent | 2 |
| `python scripts/backtest.py --model models/ppo_btc.zip` | Walk-forward backtest | 3 |
| `python scripts/run_agent.py` | One trading pass (simulated paper orders) | 4 |
| `python app.py` | Unattended scheduled trading loop | 4 |
| `streamlit run scripts/dashboard.py` | Monitor journal | 5 |

Run tests with `pytest`. Lint with `ruff check .`.

## How paper trading works

- Default `PAPER_MODE=simulated`: fills orders against live Binance prices, keeps cash/P&L
  in `data/paper_state.json` and the trade journal in `data/journal.db`. No registration needed.
- `PAPER_MODE=testnet`: executes on the Binance spot testnet with keys from `.env`.

## Structure

- `bot/data/` — Binance client, SQLite cache, feature engine
- `bot/ai/` — Gymnasium env, PPO trainer, signal generator, backtester
- `bot/trading/` — broker interface, orders, risk manager, journal, trading loop
- `bot/paper/` — simulated + testnet paper brokers
- `scripts/` — CLI entry points
- `tests/` — pytest suite
