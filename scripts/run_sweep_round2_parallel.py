"""Round-2 RL sweep: 4 workers, 9 still-negative crypto pairs.

Round 1 found BTC 5m S=3.58 (full_lowcost_1M/s42) but the weak pairs stayed
negative. Round 2 scales up the winning recipe: 1M-timestep configs across
all 10 seeds, robust_1M + full_impact + mid backups.

RAM budget: user raised the cap to 90% -> leave ~10% free (~1.6 GB).
4 workers x ~1.4 GB = 5.6 GB + server ~1 GB, safely inside.
"""

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SWEEP = ROOT / "scripts" / "sweep_prod.py"
PROD = ROOT / "models" / "prod"
SWEEP_DIR = ROOT / "models" / "sweep"

IMPACT = 0.25
DD_FLOOR = 0.10
WORKERS = 4

# Still-negative after round 1 (prod registry values), ordered heavy-first
# so round-robin [i::WORKERS] balances 1m/5m heavy pairs across workers.
PAIRS = [
    ("BTCUSDT", "1h"), ("ETHUSDT", "1h"), ("SOLUSDT", "1h"),
    ("BTCUSDT", "4h"), ("ETHUSDT", "4h"), ("SOLUSDT", "4h"),
    ("ETHUSDT", "1m"), ("SOLUSDT", "1m"),
    ("SOLUSDT", "5m"),
]


def main():
    chunks = [PAIRS[i::WORKERS] for i in range(WORKERS)]
    procs = []
    t0 = time.time()
    for w, chunk in enumerate(chunks):
        if not chunk:
            continue
        pairs_csv = ",".join(f"{s}:{g}" for s, g in chunk)
        outdir = SWEEP_DIR.parent / f"sweep_r2_w{w}"
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(PY), str(SWEEP),
            "--pairs", pairs_csv,
            "--impact", str(IMPACT),
            "--dd-floor", str(DD_FLOOR),
            "--outdir", str(outdir),
            "--round2",
        ]
        log = ROOT / "logs" / f"sweep_r2_w{w}.log"
        with log.open("w") as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT))
        procs.append((w, p, log, outdir))
        print(f"worker {w}: {pairs_csv} -> {outdir} (pid {p.pid})", flush=True)

    for w, p, log, _ in procs:
        p.wait()
        print(f"worker {w} done rc={p.returncode} ({time.time()-t0:.0f}s)", flush=True)
        if p.returncode != 0:
            print("  last log lines:")
            print("\n".join("  " + ln for ln in log.read_text().splitlines()[-20:]), flush=True)

    # ---- merge sweep registries into models/sweep_r2/registry.json
    sweep_registry = []
    for w, p, log, outdir in procs:
        reg = outdir / "registry.json"
        if reg.exists():
            sweep_registry += json.loads(reg.read_text())
    if sweep_registry:
        merged_dir = SWEEP_DIR.parent / "sweep_r2"
        merged_dir.mkdir(parents=True, exist_ok=True)
        dedup = {}
        for e in sweep_registry:
            dedup[(e["symbol"], e["granularity"])] = e
        (merged_dir / "registry.json").write_text(
            json.dumps(list(dedup.values()), indent=2))
        print(f"merged {len(dedup)} round-2 sweep entries", flush=True)

    # ---- promote winners to prod (same guard as round 1)
    prod_registry = []
    prod_reg_path = PROD / "registry.json"
    if prod_reg_path.exists():
        prod_registry = json.loads(prod_reg_path.read_text())
    existing = {(r["symbol"], r["granularity"]): r for r in prod_registry}
    swept = []
    for w, p, log, outdir in procs:
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
                print(f"  skip {key}: round-2 S={new_sharpe:.2f} <= 0", flush=True)
                if old is not None:
                    swept.append(old)
                continue
            if old is not None and new_sharpe < old_sharpe:
                print(f"  keep existing {key} (S={old_sharpe:.2f} >= r2 {new_sharpe:.2f})",
                      flush=True)
                swept.append(old)
                continue
            final_name = entry["model"]
            src = outdir / final_name
            if not src.exists():
                continue
            shutil.copyfile(src, PROD / final_name)
            entry["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry["promoted"] = True
            swept.append(entry)
            print(f"  promote {key} S={new_sharpe:.2f} <- "
                  f"{entry['selected']['config']}/s{entry['selected']['seed']}",
                  flush=True)
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
    main()