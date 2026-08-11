import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.ai.signal import SignalGenerator  # noqa: E402
from bot.trading.trader import run_once  # noqa: E402
from config import settings  # noqa: E402

GRAN_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=settings.SYMBOL)
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    parser.add_argument("--model", default=settings.MODEL_PATH)
    parser.add_argument("--sup-model", default=settings.SUP_MODEL_PATH)
    parser.add_argument("--entry-gate", type=float, default=settings.ENTRY_GATE)
    parser.add_argument("--candle-hours", type=int, default=None,
                        help="Override the loop interval (defaults to the "
                        "granularity's own bar length)")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--once", action="store_true", default=False)
    args = parser.parse_args()

    generator = SignalGenerator(args.model, entry_gate=args.entry_gate)
    state = {
        "equity": 100_000.0,
        "position": 0.0,
        "pnl": 0.0,
        "mark": None,
    }
    spread, slippage = settings.SPREAD, settings.SLIPPAGE
    interval = (
        args.candle_hours * 3600
        if args.candle_hours
        else GRAN_SECONDS.get(args.granularity, 300)
    )
    while True:
        summary = run_once(
            symbol=args.symbol,
            granularity=args.granularity,
            model_path=args.model,
            sup_model=args.sup_model,
            entry_gate=args.entry_gate,
            position=state["position"],
            equity_ratio=state["equity"] / 100_000.0,
            pnl=state["pnl"],
            dry_run=args.dry_run,
            generator=generator,
        )
        close = summary.get("close")
        if close is not None:
            if state["position"] != 0.0 and state["mark"] is not None:
                ret = close / state["mark"] - 1
                state["equity"] *= 1 + ret * state["position"]
            state["mark"] = close
            target = float(summary.get("signal", state["position"]))
            delta = abs(target - state["position"])
            if delta > 1e-6:
                state["equity"] *= 1 - (spread / 2 + slippage) * delta
                state["position"] = target
            state["pnl"] = 0.0
        position = state["position"]
        print(
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"signal={summary.get('signal')} allowed={summary.get('allowed')} "
            f"reason={summary.get('reason')} position={position} "
            f"equity={state['equity']:.2f}",
            flush=True,
        )
        if args.once:
            break
        now = time.time()
        next_bar = (int(now) // interval + 1) * interval
        time.sleep(max(5.0, next_bar - now - 15.0))


if __name__ == "__main__":
    main()
