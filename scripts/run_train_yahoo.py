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
]
runpy.run_path(str(Path(__file__).resolve().parent / "train_many.py"),
               run_name="__main__")