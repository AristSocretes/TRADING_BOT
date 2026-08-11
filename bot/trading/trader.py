from datetime import datetime, timedelta, timezone

from bot.ai.signal import SignalGenerator
from bot.data.cache import DataCache
from bot.data.clients import make_data_client, make_paper_broker
from bot.data.features import add_features
from bot.trading.journal import Journal
from bot.trading.orders import compute_stop, compute_take_profit, market_order, size_position
from bot.trading.risk import RiskManager
from config import settings


def run_once(symbol=None, granularity=None, model_path=None, sup_model=None,
             entry_gate=None, position=0.0, equity_ratio=1.0, pnl=0.0,
             dry_run=True, generator=None):
    symbol = symbol or settings.SYMBOL
    granularity = granularity or settings.GRANULARITY
    model_path = model_path or settings.MODEL_PATH
    if entry_gate is None:
        entry_gate = settings.ENTRY_GATE
    journal = Journal()
    now = datetime.now(timezone.utc)

    cache = DataCache()
    client = make_data_client()
    cache.ensure_range(client, symbol, granularity, now - timedelta(days=3), now,
                       max_rows=2500)
    df = cache.load(symbol, granularity, limit=2500)
    if len(df) < 200:
        return {"status": "not enough data"}
    df = df.iloc[-2000:]  # recent window only: fast features, current regime

    broker = None if dry_run else make_paper_broker()
    equity = float(broker.get_account()["balance"]) if broker else 100_000.0
    open_positions = len(broker.get_positions()) if broker else 0

    features = add_features(df).dropna()
    latest = features.iloc[-1]
    last_close = float(latest["close"])
    atr_value = float(latest["atr"]) if "atr" in features else 0.0

    sup_probs = None
    if sup_model:
        from bot.ai.supervised import load_supervised_model, supervised_probs

        sup_probs = supervised_probs(
            load_supervised_model(sup_model), features,
            window=30, feature_stats=None, cross_asset_dfs=None,
        )

    if generator is None:
        generator = SignalGenerator(model_path, sup_probs=sup_probs, entry_gate=entry_gate)
    result = generator.predict(
        features, position=position, equity_ratio=equity_ratio, pnl=pnl,
    )

    risk = RiskManager(
        max_risk_per_trade=settings.MAX_RISK_PER_TRADE,
        max_daily_loss=settings.MAX_DAILY_LOSS,
    )
    decision = risk.can_place(result["signal"], open_positions, equity)
    journal.log_signal(
        now.isoformat(),
        symbol,
        granularity,
        result["signal"],
        result["confidence"],
        last_close,
        equity,
    )

    summary = {
        "signal": result["signal"],
        "allowed": decision.allowed,
        "reason": decision.reason,
        "close": last_close,
    }
    if not decision.allowed or result["signal"] == position:
        journal.log_equity(now.isoformat(), equity, 0.0)
        return summary

    entry = last_close
    stop_distance = atr_value if atr_value > 0 else entry * 0.002
    units = size_position(
        equity,
        settings.MAX_RISK_PER_TRADE,
        abs(entry - compute_stop(result["signal"], entry, stop_distance)),
        max_notional=equity,
        price=entry,
    )

    # Close the existing position on flip/exit
    if position != 0.0 and broker:
        close_side = -1.0 if position > 0 else 1.0
        close_order = {
            "type": "MARKET",
            "instrument": symbol,
            "units": str(int(units * close_side)),
        }
        response = broker.place_order(close_order)
        order_id = response.get("orderCreateTransaction", {}).get("id")
        journal.update_order(now.isoformat(), order_id, "filled")
        summary["closed"] = True

    if result["signal"] != 0:
        if broker:
            pricing = broker.get_pricing(symbol)
            entry = float(pricing["ask"]) if result["signal"] > 0 else float(pricing["bid"])
        stop = compute_stop(result["signal"], entry, stop_distance)
        tp = compute_take_profit(result["signal"], entry, stop)
        order = market_order(symbol, result["signal"], units, stop, tp)
        journal.log_order(
            now.isoformat(), symbol,
            "buy" if result["signal"] > 0 else "sell", units, entry, stop, tp,
        )
        if not dry_run:
            response = broker.place_order(order)
            order_id = response.get("orderCreateTransaction", {}).get("id")
            journal.update_order(now.isoformat(), order_id, "filled")
        summary.update({"units": units, "entry": entry, "stop": stop, "tp": tp})

    journal.log_equity(now.isoformat(), equity, 0.0)
    return summary
