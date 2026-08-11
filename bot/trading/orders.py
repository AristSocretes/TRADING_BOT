def compute_stop(side, entry_price, atr_value, sl_mult=1.5):
    if side > 0:
        return entry_price - sl_mult * atr_value
    return entry_price + sl_mult * atr_value


def compute_take_profit(side, entry_price, stop_price, rr_ratio=2.0):
    risk = abs(entry_price - stop_price)
    if side > 0:
        return entry_price + rr_ratio * risk
    return entry_price - rr_ratio * risk


def size_position(equity, risk_frac, stop_distance, max_notional=None, price=None):
    if stop_distance <= 0:
        return 0
    risk_amount = equity * risk_frac
    units = max(1, int(risk_amount / stop_distance))
    if max_notional and price and units * price > max_notional:
        units = max(1, int(max_notional / price))
    return units


def market_order(symbol, side, units, stop_loss, take_profit):
    return {
        "type": "MARKET",
        "instrument": symbol,
        "units": str(int(units * side)),
        "stopLossOnFill": {"timeInForce": "GTC", "price": f"{stop_loss:.5f}"},
        "takeProfitOnFill": {"timeInForce": "GTC", "price": f"{take_profit:.5f}"},
    }
