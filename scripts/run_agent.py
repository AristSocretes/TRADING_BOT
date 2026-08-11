import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.trading.trader import run_once  # noqa: E402
from config import settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=settings.SYMBOL)
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    parser.add_argument("--model", default=settings.MODEL_PATH)
    parser.add_argument("--sup-model", default=settings.SUP_MODEL_PATH)
    parser.add_argument("--entry-gate", type=float, default=settings.ENTRY_GATE)
    parser.add_argument("--position", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    summary = run_once(
        symbol=args.symbol,
        granularity=args.granularity,
        model_path=args.model,
        sup_model=args.sup_model,
        entry_gate=args.entry_gate,
        position=args.position,
        dry_run=args.dry_run,
    )
    print(summary)


if __name__ == "__main__":
    main()
