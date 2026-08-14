"""Parallel multi-seed RL sweep on weak crypto pairs (2 workers, RAM-capped).

Distributes (symbol, granularity) pairs round-robin across workers so each
worker gets a mix of heavy (1m/5m) and light (1h/4h) pairs. Each worker runs
sweep_prod.py into its own outdir (no registry race); the driver then merges:

  - sweep winners promoted to models/prod (zip + registry entry)
  - non-swept prod registry entries preserved untouched
  - per-worker sweep registries merged into models/sweep/registry.json

RAM budget: keep >= 4 GB free for the user (2 workers x ~1.4 GB each).
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
WORKERS = 2

# Weak pairs from the retrain registry (OOS sharpe <= 1.05 or degenerate 1m).
# BTC 1m/1h/4h were already swept (results in sweep_w1 registry; the merge
# step keeps/promotes them against the prod registry). Remaining pairs split
# round-robin across 2 workers; BTC 5m goes to worker 0 because its 27
# candidates are already trained (--resume finalizes them fast).
PAIRS = [
    ("BTCUSDT", "5m"),   # w0: resume + finalize (27 trained candidates)
    ("ETHUSDT", "1m"),   # w1: 5 candidates
    ("ETHUSDT", "1h"),   # w0: 7 candidates
    ("ETHUSDT", "4h"),   # w1: 7 candidates
    ("SOLUSDT", "5m"),   # w0: 9 candidates
    ("SOLUSDT", "1m"),   # w1: 5 candidates
    ("SOLUSDT", "1h"),   # w0: 7 candidates
    ("SOLUSDT", "4h"),   # w1: 7 candidates
]


def main():
    chunks = [PAIRS[i::WORKERS] for i in range(WORKERS)]
    procs = []
    t0 = time.time()
    for w, chunk in enumerate(chunks):
        if not chunk:
            continue
        pairs_csv = ",".join(f"{s}:{g}" for s, g in chunk)
        outdir = SWEEP_DIR.parent / f"sweep_w{w}"
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(PY), str(SWEEP),
            "--pairs", pairs_csv,
            "--impact", str(IMPACT),
            "--dd-floor", str(DD_FLOOR),
            "--outdir", str(outdir),
            "--skip-existing",
            "--resume",
        ]
        log = ROOT / "logs" / f"sweep_weak_w{w}.log"
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

    # ---- merge sweep registries into models/sweep/registry.json
    sweep_registry = []
    for w, p, log, outdir in procs:
        reg = outdir / "registry.json"
        if reg.exists():
            sweep_registry += json.loads(reg.read_text())
    if sweep_registry:
        SWEEP_DIR.mkdir(parents=True, exist_ok=True)
        dedup = {}
        for e in sweep_registry:
            dedup[(e["symbol"], e["granularity"])] = e
        (SWEEP_DIR / "registry.json").write_text(
            json.dumps(list(dedup.values()), indent=2))
        print(f"merged {len(dedup)} sweep registry entries", flush=True)

    # ---- promote winners to prod, preserving non-swept entries
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
            # Never promote a loser: require positive Sharpe AND better than
            # the incumbent (or no incumbent).
            if new_sharpe <= 0.0:
                print(f"  skip {key}: sweep S={new_sharpe:.2f} <= 0 (all noise)",
                      flush=True)
                if old is not None:
                    swept.append(old)
                continue
            if old is not None and new_sharpe < old_sharpe:
                print(f"  keep existing {key} (S={old_sharpe:.2f} >= sweep {new_sharpe:.2f})",
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
    # keep any prod entries not covered by this sweep
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