
from bot.trading.risk import RiskManager


def test_allow_signal():
    risk = RiskManager()
    assert risk.can_place(1, 0, 100_000).allowed


def test_daily_loss_kill():
    risk = RiskManager(max_daily_loss=0.02)
    risk.start_day(100_000)
    assert not risk.can_place(1, 0, 98_000).allowed


def test_max_positions():
    risk = RiskManager(max_positions=1)
    assert not risk.can_place(-1, 1, 100_000).allowed


def test_position_units():
    risk = RiskManager(max_risk_per_trade=0.01)
    assert risk.position_units(100_000, stop_distance=1.0) == 1000


def test_zero_stop_distance():
    risk = RiskManager()
    assert risk.position_units(100_000, stop_distance=0.0) == 0
