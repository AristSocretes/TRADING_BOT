import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.binance_client import BinanceClient  # noqa: E402
from config import settings  # noqa: E402


def main():
    if settings.MARKET == "forex":
        if not settings.OANDA_API_KEY:
            print("OANDA_API_KEY is missing. Copy .env.example to .env and set your key.")
            sys.exit(1)
        run_forex_check()
        return
    run_crypto_check()


def run_crypto_check():
    client = BinanceClient()
    symbol = settings.SYMBOL
    price = client.latest_price(symbol)
    print(f"Symbol: {symbol} | Last price: {price}")
    df = client.fetch_klines(symbol, settings.GRANULARITY, limit=5)
    print(df.tail())


def run_forex_check():
    from oandapyV20 import API
    from oandapyV20.endpoints.accounts import AccountSummary
    from oandapyV20.endpoints.instruments import InstrumentsCandles

    client = API(access_token=settings.OANDA_API_KEY, environment=settings.OANDA_ENV)
    request = AccountSummary(settings.OANDA_ACCOUNT_ID)
    client.request(request)
    account = request.response["account"]
    print(f"Account: {account['id']}")
    print(f"Balance: {account['balance']} {account.get('currency')}")
    candles = InstrumentsCandles("EUR_USD", params={"granularity": "D", "count": 5})
    client.request(candles)
    for candle in candles.response["candles"]:
        print(candle["time"], candle["mid"])


if __name__ == "__main__":
    main()
