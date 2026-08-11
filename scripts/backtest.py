import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.ai.backtest import rl_walk_forward  # noqa: E402
from bot.ai.signal import SignalGenerator  # noqa: E402
from bot.data.cache import DataCache  # noqa: E402
from config import settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=settings.SYMBOL)
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    parser.add_argument("--model", default=settings.MODEL_PATH)
    parser.add_argument("--splits", type=int, default=4)
    args = parser.parse_args()

    cache = DataCache()
    df = cache.load(args.symbol, args.granularity)
    if df.empty:
        print("No cached data. Run scripts/fetch_data.py first.")
        return

    generator = SignalGenerator(args.model, entry_gate=settings.ENTRY_GATE)

    reports, curves = rl_walk_forward(
        df,
        generator.model,
        n_splits=args.splits,
        window=generator.window,
        spread=settings.SPREAD,
        slippage=settings.SLIPPAGE,
        trade_penalty=0.002,
    )
    for report in reports:
        print(report)
    if reports:
        print("Mean out-of-sample Sharpe:", sum(r["sharpe"] for r in reports) / len(reports))


if __name__ == "__main__":
    main()
