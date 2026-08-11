from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountSummary
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.orders import OrderCreate
from oandapyV20.endpoints.positions import ClosePosition, OpenPositions
from oandapyV20.endpoints.pricing import PricingInfo

from bot.trading.broker import Broker
from config import settings


class PaperBroker(Broker):
    def __init__(self, api_key=None, account_id=None, environment=None):
        self.api_key = api_key or settings.OANDA_API_KEY
        self.account_id = account_id or settings.OANDA_ACCOUNT_ID
        self.client = API(
            access_token=self.api_key,
            environment=environment or settings.OANDA_ENV,
        )

    def get_account(self):
        request = AccountSummary(self.account_id)
        self.client.request(request)
        return request.response["account"]

    def get_pricing(self, symbol):
        request = PricingInfo(accountID=self.account_id, params={"instruments": symbol})
        self.client.request(request)
        prices = request.response["prices"][0]
        return {
            "bid": float(prices["bids"][0]["price"]),
            "ask": float(prices["asks"][0]["price"]),
        }

    def place_order(self, order):
        request = OrderCreate(self.account_id, data={"order": order})
        self.client.request(request)
        return request.response

    def get_positions(self):
        request = OpenPositions(self.account_id)
        self.client.request(request)
        return request.response["positions"]

    def close_position(self, symbol, side):
        data = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}
        request = ClosePosition(self.account_id, instrument=symbol, data=data)
        self.client.request(request)
        return request.response

    def get_candles(self, symbol, granularity, count=500):
        request = InstrumentsCandles(
            symbol, params={"granularity": granularity, "count": count, "price": "BA"}
        )
        self.client.request(request)
        return request.response["candles"]
