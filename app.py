from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.trading.trader import run_once
from config import settings


def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_once,
        IntervalTrigger(minutes=5),
        kwargs={
            "symbol": settings.SYMBOL,
            "granularity": settings.GRANULARITY,
            "model_path": settings.MODEL_PATH,
            "dry_run": False,
        },
        id="trade_loop",
        max_instances=1,
        coalesce=True,
    )
    run_once(
        symbol=settings.SYMBOL,
        granularity=settings.GRANULARITY,
        model_path=settings.MODEL_PATH,
        dry_run=False,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
