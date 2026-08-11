import sqlite3
from pathlib import Path

import pandas as pd

from config import settings


class Journal:
    def __init__(self, path=None):
        self.path = Path(path) if path else settings.JOURNAL_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self._create()

    def _create(self):
        c = self.conn
        c.execute(
            "CREATE TABLE IF NOT EXISTS signals ("
            "ts TEXT, pair TEXT, granularity TEXT, signal INTEGER, "
            "confidence REAL, price REAL, equity REAL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "ts TEXT, pair TEXT, side TEXT, units INTEGER, price REAL, "
            "sl REAL, tp REAL, order_id TEXT, status TEXT)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS trades ("
            "ts TEXT, pair TEXT, direction TEXT, units INTEGER, "
            "entry REAL, exit REAL, pnl REAL)"
        )
        c.execute("CREATE TABLE IF NOT EXISTS equity (ts TEXT, equity REAL, day_pnl REAL)")
        self.conn.commit()

    def log_signal(self, ts, pair, granularity, signal, confidence, price, equity):
        self.conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?)",
            (ts, pair, granularity, signal, confidence, price, equity),
        )
        self.conn.commit()

    def log_order(self, ts, pair, side, units, price, sl, tp, order_id=None, status="submitted"):
        self.conn.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, pair, side, units, price, sl, tp, order_id, status),
        )
        self.conn.commit()

    def update_order(self, ts, order_id, status):
        self.conn.execute(
            "UPDATE orders SET order_id=?, status=? WHERE ts=?",
            (order_id, status, ts),
        )
        self.conn.commit()

    def log_trade(self, ts, pair, direction, units, entry, exit_price, pnl):
        self.conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
            (ts, pair, direction, units, entry, exit_price, pnl),
        )
        self.conn.commit()

    def log_equity(self, ts, equity, day_pnl):
        self.conn.execute(
            "INSERT INTO equity VALUES (?,?,?)",
            (ts, equity, day_pnl),
        )
        self.conn.commit()

    def equity_curve(self):
        return pd.read_sql_query("SELECT ts, equity FROM equity ORDER BY ts", self.conn)

    def recent_signals(self, limit=50):
        return pd.read_sql_query(
            "SELECT * FROM signals ORDER BY ts DESC LIMIT ?", self.conn, params=(limit,)
        )
