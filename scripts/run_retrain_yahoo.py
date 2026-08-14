"""Retrain the 8 Yahoo 1d prod models with execution-aware env.

Uses runpy (like run_train_yahoo.py) to bypass cmd `^` escaping for
^GSPC-style symbols. New recipe:
  - full 93-feature layout (adds GARCH/EWMA conditional vol + vol-target ratio)
  - Almgren-Chriss square-root market impact in env + backtest (--impact)
  - max-drawdown floor (prop risk rule) in env + backtest (--dd-floor)
  - bootstrap significance stats (PSR, Lo SE/CI) recorded in the registry
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runpy

sys.argv = [
    "train_many.py",
    "--symbols", "^GSPC,^IXIC,^FTSE,^GDAXI,^N225,EURUSD=X,USDINR=X,SLV",
    "--granularities", "1d",
    "--timesteps", "300000",
    "--splits", "1",
    "--device", "cuda",
    "--impact", "0.25",
    "--dd-floor", "0.10",
]
runpy.run_path(str(Path(__file__).resolve().parent / "train_many.py"),
               run_name="__main__")
