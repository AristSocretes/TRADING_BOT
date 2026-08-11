import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

MARKET = os.getenv("MARKET", "crypto")

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")

BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
BINANCE_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")

PAPER_MODE = os.getenv("PAPER_MODE", "simulated")
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "100000"))

SYMBOL = os.getenv("TRADING_SYMBOL", "BTCUSDT")
GRANULARITY = os.getenv("TRADING_GRANULARITY", "5m")

SPREAD = float(os.getenv("TRADING_SPREAD", "0.0004"))
SLIPPAGE = float(os.getenv("TRADING_SLIPPAGE", "0.00005"))
MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "0.01"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "0.02"))

DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
LOGS_DIR = BASE_DIR / "logs"
RESULTS_DIR = BASE_DIR / "results"
JOURNAL_DB = DATA_DIR / "journal.db"

MODEL_PATH = os.getenv("MODEL_PATH", str(MODELS_DIR / "prod" / "BTCUSDT_5m.zip"))
SUP_MODEL_PATH = os.getenv("SUP_MODEL_PATH", str(MODELS_DIR / "ppo_btc_sup.pkl"))

# RL training / trading defaults (validated by walk-forward, 2026-08-10)
ALIGN_BONUS = float(os.getenv("ALIGN_BONUS", "0.1"))
ENTRY_GATE = float(os.getenv("ENTRY_GATE", "0.05"))
RISK_PENALTY = float(os.getenv("RISK_PENALTY", "0.1"))
ENTROPY_COEF = float(os.getenv("ENTROPY_COEF", "0.02"))
TRADE_PENALTY = float(os.getenv("TRADE_PENALTY", "0.05"))

for _dir in (DATA_DIR, MODELS_DIR, LOGS_DIR, RESULTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def oanda_base_url(environment=None):
    env = environment or OANDA_ENV
    return "https://api-fxpractice.oanda.com" if env == "practice" else "https://api-fxtrade.oanda.com"
