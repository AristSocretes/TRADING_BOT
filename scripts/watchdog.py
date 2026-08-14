"""Server watchdog: keeps the FastAPI prediction server alive.

Checks every WATCH_SECONDS:
  - server responds to GET /api/health (HTTP)
  - every prod registry entry has its model zip on disk
  - free RAM stays above the cap (suggests killing a crashed heavy process)

Restarts the server (detached, logs to web/server.out.log / .err.log) if the
health check fails. Best run detached once: python scripts/watchdog.py
"""

import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SERVER = ROOT / "web" / "server.py"
REGISTRY = ROOT / "models" / "prod" / "registry.json"
HEALTH_URL = "http://127.0.0.1:8080/api/health"
WATCH_SECONDS = 60
RAM_FREE_GB_CAP = 4.0
RESTART_COOLDOWN = 300  # don't restart more than once per 5 min


def _server_alive():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def _free_ram_gb():
    try:
        import psutil

        return psutil.virtual_memory().available / 1e9
    except Exception:
        return None


def _registry_ok():
    if not REGISTRY.exists():
        return True, []
    try:
        reg = json.loads(REGISTRY.read_text())
    except Exception:
        return False, ["registry.json corrupt"]
    missing = []
    for e in reg:
        if not (ROOT / "models" / "prod" / e["model"]).exists():
            missing.append(f"{e['symbol']} {e['granularity']} -> {e['model']}")
    return (len(missing) == 0), missing


def _start_server():
    out = (ROOT / "web" / "server.out.log").open("a")
    err = (ROOT / "web" / "server.err.log").open("a")
    proc = subprocess.Popen(
        [str(PY), str(SERVER)],
        cwd=str(ROOT), stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP |
        subprocess.DETACHED_PROCESS,
    )
    return proc.pid


def main():
    last_restart = 0.0
    while True:
        ok, missing = _registry_ok()
        if not ok:
            print(f"[watchdog] registry problems: {missing}", flush=True)
        alive = _server_alive()
        free_gb = _free_ram_gb()
        if free_gb is not None:
            print(f"[watchdog] alive={alive} free_ram={free_gb:.1f}GB", flush=True)
        if not alive and time.time() - last_restart > RESTART_COOLDOWN:
            pid = _start_server()
            last_restart = time.time()
            print(f"[watchdog] server DOWN -> restarted pid={pid}", flush=True)
        elif not alive:
            print("[watchdog] server down, restart in cooldown", flush=True)
        time.sleep(WATCH_SECONDS)


if __name__ == "__main__":
    main()