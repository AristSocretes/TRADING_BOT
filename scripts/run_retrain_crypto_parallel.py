"""Retrain crypto prod models in parallel (4 workers, full PC utilization).

Each worker trains a disjoint subset of (symbol, granularity) pairs with the
new execution-aware recipe:
  - full 93-feature layout (GARCH/EWMA conditional vol + vol-target ratio)
  - Almgren-Chriss square-root market impact in env + backtest (--impact 0.25)
  - max-drawdown floor (prop risk rule) in env + backtest (--dd-floor 0.10)
  - bootstrap significance stats (PSR, Lo SE/CI) recorded in the registry

Workers write to per-worker outdirs (no registry race), then the driver
merges zips + registries into models/prod.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
TRAIN_MANY = ROOT / "scripts" / "train_many.py"
PROD = ROOT / "models" / "prod"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
GRANS = ["1m", "5m", "1h", "4h"]
TIMESTEPS = 300_000
IMPACT = 0.25
DD_FLOOR = 0.10
# RAM budget: keep 4 GB free for the user. Each worker uses ~1.3-1.5 GB
# RAM + ~1.4 GB GPU, so 2 workers stay comfortably inside the cap.
WORKERS = 2


def main():
    pairs = [(s, g) for s in SYMBOLS for g in GRANS]
    chunks = [pairs[i::WORKERS] for i in range(WORKERS)]
    procs = []
    t0 = time.time()
    for w, chunk in enumerate(chunks):
        if not chunk:
            continue
        syms = ",".join(s for s, _ in chunk)
        grans = ",".join(g for _, g in chunk)
        outdir = PROD.parent / f"prod_w{w}"
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(PY), str(TRAIN_MANY),
            "--symbols", syms,
            "--granularities", grans,
            "--timesteps", str(TIMESTEPS),
            "--splits", "1",
            "--device", "cuda",
            "--impact", str(IMPACT),
            "--dd-floor", str(DD_FLOOR),
            "--outdir", str(outdir),
            "--skip-existing",
        ]
        log = ROOT / "logs" / f"retrain_crypto_w{w}.log"
        with log.open("w") as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT))
        procs.append((w, p, log))
        print(f"worker {w}: {syms} {grans} -> {outdir} (pid {p.pid})", flush=True)

    for w, p, log in procs:
        p.wait()
        print(f"worker {w} done rc={p.returncode} ({time.time()-t0:.0f}s)", flush=True)
        if p.returncode != 0:
            print(f"  last log lines of {log}:")
            lines = log.read_text().splitlines()[-15:]
            print("\n".join("  " + ln for ln in lines), flush=True)

    # Merge: copy zips + merge registries from per-worker dirs into prod,
    # preserving entries not covered by this retrain (e.g. Yahoo 1d).
    merged = []
    for w, p, log in procs:
        wdir = PROD.parent / f"prod_w{w}"
        reg = wdir / "registry.json"
        if reg.exists():
            for entry in json.loads(reg.read_text()):
                merged.append(entry)
        for zipf in wdir.glob("*.zip"):
            shutil.copyfile(zipf, PROD / zipf.name)
    if merged:
        existing = []
        prod_reg = PROD / "registry.json"
        if prod_reg.exists():
            existing = json.loads(prod_reg.read_text())
        covered = {(e["symbol"], e["granularity"]) for e in merged}
        kept = [r for r in existing
                if (r["symbol"], r["granularity"]) not in covered]
        final_registry = kept + merged
        final_registry.sort(key=lambda e: (e["symbol"], e["granularity"]))
        (PROD / "registry.json").write_text(json.dumps(final_registry, indent=2))
        print(f"merged {len(merged)} registry entries + zips into {PROD} "
              f"({len(kept)} existing kept)", flush=True)
    print(f"all done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()