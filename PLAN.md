# AI Crypto Trading Bot — Full Implementation Plan

**Market:** Crypto (BTCUSDT, ETHUSDT, ...) via Binance public API
**Core AI:** Reinforcement Learning (RL)
**Mode:** Paper trading first (local simulated broker, optional Binance testnet) → live later

> The same codebase supports forex via OANDA (`MARKET=forex`); crypto is the default because OANDA isn't reachable in your region.

---

# PART A — THE PLAN, DIFFERENTIATED INTO PHASES

> Each phase has: **Goal** · **Tasks** · **Deliverables (Definition of Done)** · **Est. time**.
> Phases are sequential; never start a later phase before the previous DoD passes.

---

## PHASE 0 — Foundation & Secure Setup

**Goal:** A clean, secure project skeleton with a verified Binance public-API connection.

### Tasks
- [ ] 0.1 Create `.env` from `.env.example` (gitignored) with `MARKET=crypto`, `TRADING_SYMBOL=BTCUSDT`, `TRADING_GRANULARITY=5m`.
- [ ] 0.2 `config/settings.py` loads `.env` via `python-dotenv` (single source of truth). No secrets committed.
- [ ] 0.3 Package skeleton: `bot/{data,ai,trading,paper}/`, `scripts/`, `tests/`.
- [ ] 0.4 `git init`, `.gitignore`, `requirements.txt`, `pyproject.toml`, `pytest` + `ruff` configured.
- [ ] 0.5 `scripts/check_connection.py`: fetch last price + 5 klines of `BTCUSDT` from Binance.
- [ ] 0.6 Verify ML stack installs on Python 3.14 (`torch`, `gymnasium`, `stable-baselines3`). Already confirmed working.

### Definition of Done
- [ ] `check_connection.py` prints a live `BTCUSDT` price and klines.
- [ ] `.env` + config loads; secrets never in git.
- [ ] `pytest` and `ruff` run clean.

**Est. time:** 0.5–1 day (done)

---

## PHASE 1 — Data Pipeline

**Goal:** Reliable, cached, look-ahead-free historical + live data.

### Tasks
- [ ] 1.1 `bot/data/binance_client.py` — Binance public `/api/v3/klines` (no API key needed), paginated fetch, retry/rate-limit handling.
- [ ] 1.2 Support symbols (start): `BTCUSDT`, `ETHUSDT`; intervals `5m`, `15m`, `1h`, `4h`, `1d`.
- [ ] 1.3 `bot/data/cache.py` — append-only SQLite per (symbol, interval), `ensure_range` fetches only missing slices.
- [ ] 1.4 `bot/data/features.py` — rolling features: returns, log-returns, rolling mean/std, RSI(14), MACD, ATR(14), Bollinger %B, SMA/EMA crossover, hour-of-day / day-of-week.
- [ ] 1.5 `bot/data/quality.py` — gap detection, duplicate removal, sanity checks.
- [ ] 1.6 `scripts/fetch_data.py` — download 2 years of `BTCUSDT` 5m + 1h + 1d.
- [ ] 1.7 Test: feature vector at time *t* uses **only** data ≤ *t* (no look-ahead).

### Definition of Done
- [ ] 2 years cached locally; rerunning fetch adds nothing.
- [ ] `pytest` proves zero look-ahead leakage.
- [ ] Gap/duplicate report generates.

**Est. time:** 1–2 days

---

## PHASE 2 — RL Environment & Model

**Goal:** A Gymnasium environment that honestly simulates trading, and a PPO agent that trains.

### Tasks
- [ ] 2.1 `bot/ai/env.py` — `CryptoTradingEnv`:
      - **State:** normalized feature window (60–128 steps) + account info (equity, position, P&L).
      - **Action (discrete):** `{0: hold, 1: buy, 2: sell}` fixed sizing. *(Continuous −1..+1 via SAC = stretch.)*
      - **Reward:** `log(equity_t / equity_{t-1})` minus per-step cost + trade penalty.
      - **Mechanics:** spread, slippage, position persists, no same-bar open+close, stop enforced.
- [ ] 2.2 Train/validate/test split **by time** (70/15/15), never random.
- [ ] 2.3 `bot/ai/rl_trainer.py` — PPO via Stable-Baselines3; grid search on lr, n_steps, batch, γ, clip.
- [ ] 2.4 Save best checkpoint by **validation Sharpe** to `models/`.
- [ ] 2.5 `bot/ai/signal.py` — policy → `{-1,0,+1}` signal + confidence.
- [ ] 2.6 (Stretch) Continuous actions with SAC; ensemble of seeds.

### Definition of Done
- [ ] `scripts/train.py --symbol BTCUSDT --granularity 5m --timesteps 200000` runs and saves a checkpoint.
- [ ] In-sample equity curve is sensible; reward converges.

**Est. time:** 3–5 days

---

## PHASE 3 — Backtesting & Validation

**Goal:** Honest out-of-sample performance with transaction costs; kill bad models early.

### Tasks
- [ ] 3.1 `bot/ai/backtest.py` — cost-aware backtester (spread per round-trip, slippage).
- [ ] 3.2 Metrics: total return, Sharpe, max drawdown, win rate, profit factor, # trades.
- [ ] 3.3 Baselines: buy-and-hold for the same period.
- [ ] 3.4 Walk-forward: multiple chronological train/test windows; results must be stable across windows.
- [ ] 3.5 Sensitivity: vary spread & slippage ±50% — strategy must survive.
- [ ] 3.6 Audit: no accidental future info (shift-by-one, leaks in resets).
- [ ] 3.7 Generate `results/report.html` (equity curves + metrics table).

### Definition of Done
- [ ] Honest out-of-sample report (if Sharpe ≤ 0, that's a valid result → back to Phase 2).
- [ ] Backtest logic covered by `pytest`.

**Est. time:** 2–3 days

---

## PHASE 4 — Paper Trading

**Goal:** The trained agent trades unattended against a simulated (or Binance-testnet) account, within hard risk limits.

### Tasks
- [ ] 4.1 `bot/trading/broker.py` — interface: `place_order`, `get_positions`, `get_account`, `close_position`, `get_pricing`.
- [ ] 4.2 `bot/paper/binance_paper_broker.py` — **simulated fills** from live Binance prices (default, no keys) with cash/P&L accounting in `data/paper_state.json`.
- [ ] 4.3 Optional: Binance **testnet** execution via `BINANCE_TESTNET_API_KEY/SECRET` (`PAPER_MODE=testnet`).
- [ ] 4.4 `bot/trading/risk.py` — per-trade risk ≤1% equity (ATR stop), daily loss kill-switch (2%), max positions, leverage cap (1×).
- [ ] 4.5 `bot/trading/journal.py` — SQLite `journal.db`: signals, orders, trades, equity snapshots.
- [ ] 4.6 `app.py` — scheduler (`apscheduler`) runs `bot/trading/trader.py` each bar close: fetch → features → signal → risk-check → order → journal.
- [ ] 4.7 Paper run ≥ 4–6 weeks; compare paper P&L vs backtest expectation.

### Definition of Done
- [ ] Bot runs unattended for the full paper window; zero risk-limit breaches.
- [ ] `logs/` populated; kill-switch and API errors alert.

**Est. time:** 2–3 days + observation window

---

## PHASE 5 — Monitoring, Refinement & Optional Live

**Goal:** Visibility, a retraining loop, and a disciplined path to live.

### Tasks
- [ ] 5.1 (Optional) Streamlit dashboard reading `journal.db` (equity curve, signals, positions).
- [ ] 5.2 Retraining loop: weekly/monthly retrain; promote a model **only if** it beats the incumbent out-of-sample.
- [ ] 5.3 Drift detection: compare live feature distribution vs training; flag regime change.
- [ ] 5.4 Live transition checklist (only after sustained profitable paper):
      - Funded Binance account + **live API keys in `.env`** (never committed).
      - Start at 50% position size for 2 weeks.
      - Daily manual journal review before unattended trust.

### Definition of Done
- [ ] Dashboard/logs give full transparency into every decision.
- [ ] A documented, repeatable go-live decision gate.

**Est. time:** 1–2 days + ongoing

---

# PART B — THE "OTHER THINGS" (separated out)

## B.1 Dependencies & Environment
```
requests            # Binance REST (public, no key needed)
pandas numpy        # data processing
gymnasium           # RL environment spec
stable-baselines3   # PPO / DQN / SAC
torch               # SB3 backend
scikit-learn        # preprocessing, drift checks
python-dotenv       # .env config loading
apscheduler         # scheduled jobs
loguru              # logging
pytest ruff         # tests + lint
streamlit           # optional dashboard (Phase 5)
oandapyV20          # optional, only for MARKET=forex
```
> Binance public market data needs no API key. Binance **testnet** keys are optional for real paper execution. ML stack confirmed working on Python 3.14.

## B.2 Testing Strategy
- **Unit** (`tests/unit`): features, cache, kline parsing, risk math, order builder — no network.
- **Integration** (`tests/integration`): Binance public-API read calls (klines, price).
- **Regression/Backtest** (`tests/backtest`): env determinism, no-look-ahead, cost sensitivity.
- **Rule:** every Phase's DoD includes a runnable test; `pytest` must stay green.

## B.3 Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| RL overfitting | Looks great in-sample, dies live | Walk-forward validation (Phase 3), multi-window stability |
| Spread/slippage costs | Profitable in backtest, losing live | Always model costs; sensitivity ±50% |
| Non-stationary markets | Model decays over time | Scheduled retraining + drift detection (5.3) |
| API outages / rate limits | Missed trades, crash | Retries, backoff, alerting, kill-switch |
| Credential leak | Real money risk | `.env` only, `.gitignore`, never log keys |
| Crypto volatility | Overnight gaps blow stops | ATR-based stops, 1× leverage cap, kill-switch |

## B.4 Configuration Reference (`.env`)
```
MARKET=crypto
TRADING_SYMBOL=BTCUSDT
TRADING_GRANULARITY=5m
TRADING_SPREAD=0.0004
TRADING_SLIPPAGE=0.00005
MAX_RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.02

PAPER_MODE=simulated            # simulated | testnet
STARTING_BALANCE=100000
BINANCE_TESTNET_API_KEY=        # optional
BINANCE_TESTNET_API_SECRET=     # optional

OANDA_API_KEY=                  # only for MARKET=forex
OANDA_ACCOUNT_ID=
OANDA_ENV=practice
```
`config/settings.py` reads these; code never hard-codes credentials.

## B.5 Glossary
- **Walk-forward validation** — train on window *A*, test on next window *B*, roll forward.
- **Look-ahead leakage** — using future data in features; inflates backtest results.
- **Kill-switch** — hard rule (e.g. 2% daily loss) that halts all trading.
- **Sharpe ratio** — risk-adjusted return; primary model-selection metric.
- **Profit factor** — gross wins / gross losses; >1.5 is a strong target.

## B.6 Realistic Expectations (read this)
- Most RL configurations **lose money out-of-sample**. That's the point of the plan: find out fast and cheap.
- The Definition of Done for v1 is **not** profitability — it's an *honest, risk-controlled paper system* that tells you whether the idea is worth going live.

---

# Build Order Summary
1. **Phase 0** Foundation & connection — Day 1 (done)
2. **Phase 1** Data pipeline — Days 1–3
3. **Phase 2** RL env + PPO — Days 3–8
4. **Phase 3** Backtest + validation — Days 8–11
5. **Phase 4** Paper trading — Days 11–14 (+ 4–6 wk observation)
6. **Phase 5** Monitoring / live gate — ongoing
