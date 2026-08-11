import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.ai.rl_trainer import train  # noqa: E402
from bot.data.cache import DataCache  # noqa: E402
from config import settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=settings.SYMBOL)
    parser.add_argument("--granularity", default=settings.GRANULARITY)
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--n-envs", type=int, default=8)
    parser.add_argument("--penalty", type=float, default=settings.TRADE_PENALTY)
    parser.add_argument("--risk-penalty", type=float, default=settings.RISK_PENALTY)
    parser.add_argument("--align-bonus", type=float, default=settings.ALIGN_BONUS)
    parser.add_argument("--entropy", type=float, default=settings.ENTROPY_COEF)
    parser.add_argument("--entry-gate", type=float, default=settings.ENTRY_GATE)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--spread-range", type=float, nargs=2, default=None,
                        help="domain-randomize spread per episode: lo hi")
    parser.add_argument("--slippage-range", type=float, nargs=2, default=None,
                        help="domain-randomize slippage per episode: lo hi")
    parser.add_argument("--reward-clip", type=float, default=0.25)
    parser.add_argument("--lr-schedule", action="store_true",
                        help="linearly decay learning rate to 0")
    args = parser.parse_args()

    cache = DataCache()
    df = cache.load(args.symbol, args.granularity)
    if df.empty:
        print("No cached data. Run scripts/fetch_data.py first.")
        return
    path = train(
        df,
        total_timesteps=args.timesteps,
        n_envs=args.n_envs,
        device=args.device,
        trade_penalty=args.penalty,
        risk_penalty=args.risk_penalty,
        align_bonus=args.align_bonus,
        entropy_coef=args.entropy,
        seed=args.seed,
        spread_range=args.spread_range,
        slippage_range=args.slippage_range,
        reward_clip=args.reward_clip,
        lr_schedule=args.lr_schedule,
    )
    print("Saved model:", path)


if __name__ == "__main__":
    main()
