import json
import random
from datetime import datetime, timezone
from pathlib import Path

from bot.data.binance_client import BinanceClient
from bot.trading.broker import Broker
from config import settings


class CryptoPaperBroker(Broker):
    def __init__(self, client=None, balance=None, state_path=None, seed=0):
        self.client = client or BinanceClient()
        self.balance = float(balance or settings.STARTING_BALANCE)
        self.rng = random.Random(seed)
        default_state = Path(settings.DATA_DIR) / "paper_state.json"
        self.state_path = Path(state_path) if state_path else default_state
        self.positions = []
        self._load_state()

    def _load_state(self):
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            self.balance = float(data.get("balance", self.balance))
            self.positions = data.get("positions", [])

    def _save_state(self):
        payload = {"balance": self.balance, "positions": self.positions}
        self.state_path.write_text(json.dumps(payload))

    def get_pricing(self, symbol):
        mid = self.client.latest_price(symbol)
        half = settings.SPREAD / 2
        return {"bid": mid * (1 - half), "ask": mid * (1 + half)}

    def get_account(self):
        equity = self.balance
        for position in self.positions:
            equity += self._unrealized(position, self.client.latest_price(position["symbol"]))
        return {"balance": self.balance, "equity": equity}

    def get_positions(self):
        return self.positions

    def place_order(self, order):
        symbol = order["instrument"]
        units = int(order["units"])
        side = "buy" if units > 0 else "sell"
        pricing = self.get_pricing(symbol)
        price = pricing["ask"] if side == "buy" else pricing["bid"]
        self.close_position(symbol, "any")
        notional = abs(units) * price
        self.balance -= (settings.SPREAD / 2 + settings.SLIPPAGE) * notional
        self.positions.append(
            {
                "symbol": symbol,
                "side": side,
                "units": abs(units),
                "entry": price,
                "sl": float(order.get("stopLossOnFill", {}).get("price", 0.0)),
                "tp": float(order.get("takeProfitOnFill", {}).get("price", 0.0)),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_state()
        return {"orderCreateTransaction": {"id": "simulated"}, "filled": price}

    def _unrealized(self, position, price):
        units = position["units"]
        if position["side"] == "buy":
            return (price - position["entry"]) * units
        return (position["entry"] - price) * units

    def close_position(self, symbol, side="any"):
        kept = []
        realized = 0.0
        for position in self.positions:
            if position["symbol"] != symbol:
                kept.append(position)
                continue
            if side != "any" and position["side"] != side:
                kept.append(position)
                continue
            price = self.client.latest_price(symbol)
            pnl = self._unrealized(position, price)
            cost = (settings.SPREAD / 2 + settings.SLIPPAGE) * position["units"] * price
            self.balance += pnl - cost
            realized += pnl
        self.positions = kept
        self._save_state()
        return {"closed": symbol, "realized_pnl": realized}
