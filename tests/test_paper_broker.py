from bot.paper.binance_paper_broker import CryptoPaperBroker


class FakePriceClient:
    def __init__(self, price):
        self.price = price

    def latest_price(self, symbol):
        return self.price


def test_open_position(tmp_path):
    broker = CryptoPaperBroker(
        client=FakePriceClient(50000.0),
        balance=100000.0,
        state_path=tmp_path / "state.json",
    )
    account = broker.get_account()
    assert account["balance"] == 100000.0
    broker.place_order(
        {
            "instrument": "BTCUSDT",
            "units": 1,
            "stopLossOnFill": {"price": "49000"},
            "takeProfitOnFill": {"price": "52000"},
        }
    )
    assert len(broker.get_positions()) == 1
    position = broker.get_positions()[0]
    assert position["side"] == "buy"
    assert position["units"] == 1
    assert broker.balance < 100000.0


def test_close_realizes_pnl(tmp_path):
    broker = CryptoPaperBroker(
        client=FakePriceClient(50000.0),
        balance=100000.0,
        state_path=tmp_path / "state.json",
    )
    broker.place_order(
        {
            "instrument": "BTCUSDT",
            "units": 2,
            "stopLossOnFill": {},
            "takeProfitOnFill": {},
        }
    )
    broker.client = FakePriceClient(51000.0)
    result = broker.close_position("BTCUSDT")
    assert result["realized_pnl"] > 0
    assert broker.balance > 100000.0
    assert len(broker.get_positions()) == 0


def test_state_persists(tmp_path):
    path = tmp_path / "state.json"
    broker = CryptoPaperBroker(client=FakePriceClient(50000.0), state_path=path)
    broker.place_order(
        {"instrument": "BTCUSDT", "units": 1, "stopLossOnFill": {}, "takeProfitOnFill": {}}
    )
    reloaded = CryptoPaperBroker(client=FakePriceClient(50000.0), state_path=path)
    assert len(reloaded.get_positions()) == 1
