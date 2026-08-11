from dataclasses import dataclass


@dataclass
class RiskResult:
    allowed: bool
    reason: str = ""


class RiskManager:
    def __init__(self, max_risk_per_trade=0.01, max_daily_loss=0.02, max_positions=1):
        self.max_risk_per_trade = max_risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_positions = max_positions
        self.day_start_equity = None

    def start_day(self, equity):
        self.day_start_equity = equity

    def can_place(self, signal, open_positions, equity):
        if signal == 0:
            return RiskResult(False, "hold")
        if self.day_start_equity and equity / self.day_start_equity - 1 <= -self.max_daily_loss:
            return RiskResult(False, "daily loss limit hit")
        if open_positions >= self.max_positions:
            return RiskResult(False, "max positions reached")
        return RiskResult(True, "")

    def position_units(self, equity, stop_distance):
        if stop_distance <= 0:
            return 0
        risk_amount = equity * self.max_risk_per_trade
        return max(1, int(risk_amount / stop_distance))
