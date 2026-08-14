"""Round-3 parallel sweep driver.

Trains the still-negative prod pairs on CUDA with 2M-timestep GPU plans
(n_envs=128), merges per-worker registries, and promotes positive winners
to models/prod with the same guard as round 1/2.

Usage:
    python scripts/run_sweep_round3_parallel.py
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SWEEP = ROOT / "scripts" / "sweep_prod.py"
PROD = ROOT / "models" / "prod"
SWEEP_DIR = ROOT / "models" / "sweep_r3"
LOGS = ROOT / "logs"

# Still-negative after round 2 (prod registry values), ordered heavy-first
# so round-robin [i::WORKERS] balances 1h/4h heavy pairs across workers.
PAIRS = [
    ("BTCUSDT", "1h"), ("ETHUSDT", "1h"), ("SOLUSDT", "1h"),
    ("ETHUSDT", "4h"),
    ("EURUSD=X", "1d"), ("USDINR=X", "1d"), ("^FTSE", "1d"),
]
WORKERS = 3
DEVICE = "cuda"


def main():
    t0 = time.time()
    LOGS.mkdir(exist_ok=True)
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)

    chunks = [PAIRS[i::WORKERS] for i in range(WORKERS)]
    procs = []
    for w, chunk in enumerate(chunks):
        if not chunk:
            continue
        pairs = ",".join(f"{s}:{g}" for s, g in chunk)
        outdir = SWEEP_DIR / f"w{w}"
        log = LOGS / f"sweep_r3_w{w}.log"
        err = LOGS / f"sweep_r3_w{w}.err.log"
        cmd = [
            str(PY), str(SWEEP),
            "--pairs", pairs,
            "--impact", "0.25", "--dd-floor", "0.10",
            "--outdir", str(outdir),
            "--round3", "--resume",
            "--device", DEVICE,
        ]
        print(f"worker {w}: {pairs} -> {outdir}", flush=True)
        f_out = open(log, "w", encoding="utf-8")
        f_err = open(err, "w", encoding="utf-8")
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=f_out, stderr=f_err)
        procs.append((w, p, log, outdir, f_out, f_err))

    for w, p, log, outdir, f_out, f_err in procs:
        rc = p.wait()
        f_out.close()
        f_err.close()
        print(f"worker {w} done rc={rc}", flush=True)
        tail = "\n".join(log.read_text().splitlines()[-20:])
        print("\n".join("  " + ln for ln in tail.splitlines()), flush=True)
        if rc != 0:
            err_tail = "\n".join(err.read_text().splitlines()[-25:])
            print("  ERR:", flush=True)
            print("\n".join("  " + ln for ln in err_tail.splitlines()), flush=True)

    # ---- merge sweep registries into models/sweep_r3/registry.json
    sweep_registry = []
    for w, p, log, outdir, f_out, f_err in procs:
        reg = outdir / "registry.json"
        if reg.exists():
            sweep_registry += json.loads(reg.read_text())
    if sweep_registry:
        merged_dir = SWEEP_DIR / "registry.json"
        dedup = {}
        for e in sweep_registry:
            dedup[(e["symbol"], e["granularity"])] = e
        merged_dir.write_text(json.dumps(list(dedup.values()), indent=2))
        print(f"merged {len(dedup)} round-3 sweep entries", flush=True)

    # ---- promote winners to prod (same guard as round 1/2)
    prod_registry = []
    prod_reg_path = PROD / "registry.json"
    if prod_reg_path.exists():
        prod_registry = json.loads(prod_reg_path.read_text())
    existing = {(r["symbol"], r["granularity"]): r for r in prod_registry}
    swept = []
    for w, p, log, outdir, f_out, f_err in procs:
        reg = outdir / "registry.json"
        if not reg.exists():
            continue
        for entry in json.loads(reg.read_text()):
            key = (entry["symbol"], entry["granularity"])
            old = existing.get(key)
            new_sharpe = float(entry.get("oos_sharpe") or entry.get("mean_oos_sharpe") or -99.0)
            old_val = old.get("oos_sharpe") if old else None
            if old_val is None:
                old_val = old.get("mean_oos_sharpe") if old else None
            old_sharpe = float(old_val) if old_val is not None else -99.0
            if new_sharpe <= 0.0:
                print(f"  skip {key}: round-3 S={new_sharpe:.2f} <= 0", flush=True)
                continue
            if new_sharpe <= old_sharpe:
                print(f"  keep {key}: round-3 S={new_sharpe:.2f} "
                      f"<= existing {old_sharpe:.2f}", flush=True)
                continue
            src = outdir / f"{key[0]}_{key[1]}.zip"
            final_name = src.name
            if not src.exists():
                print(f"  WARN {key}: winner zip missing ({src.name})", flush=True)
                continue
            shutil.copyfile(src, PROD / final_name)
            entry["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry["promoted"] = True
            existing[key] = entry
            swept.append(entry)
            print(f"  promote {key} S={new_sharpe:.2f} <- {entry['selected']}", flush=True)

    covered = {(e["symbol"], e["granularity"]) for e in swept}
    kept = [r for r in prod_registry
            if (r["symbol"], r["granularity"]) not in covered]
    final_registry = kept + swept
    final_registry.sort(key=lambda e: (e["symbol"], e["granularity"]))
    (PROD / "registry.json").write_text(json.dumps(final_registry, indent=2))
    print(f"registry now has {len(final_registry)} entries "
          f"({len(kept)} kept + {len(swept)} swept)", flush=True)
    print(f"all done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())