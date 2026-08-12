"""Live progress dashboard for the two background training agents.

Run:
    streamlit run scripts/live_dashboard.py --server.port 8501

Panels:
    - Market tickers + cache freshness (source: Binance live)
    - Agent A: sweep_prod (seeds x configs) - log stream, process profile
    - Agent B: train_many (1h/4h prod models) - log stream, process profile
    - GPU / memory util (nvidia-smi + psutil)
    - Sweep registry: best OOS candidate per (symbol, granularity)

Open multiple browser windows/tabs against the same URL to get independent
live views of each agent.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.cache import DataCache  # noqa: E402
from bot.data.clients import make_data_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
SWEEP_REGISTRY = ROOT / "models" / "sweep" / "registry.json"
AGENTS = ("sweep_prod", "train_many")

st.set_page_config(
    page_title="Live Training Progress",
    page_icon="📈",
    layout="wide",
)

LOG_CANDIDATES = [
    LOGS / "sweep_prod.log",
    LOGS / "sweep_prod.out.log",
    ROOT / "sweep_prod.log",
    ROOT / "train_many.log",
    ROOT / "train2.log",
    ROOT / "train.log",
    LOGS / "train_many.log",
]


def find_log(agent: str):
    hits = [p for p in LOG_CANDIDATES if agent.replace("_", "") in p.stem.replace("_", "")]
    hits = [p for p in hits if p.exists()]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def tail(path: Path, n: int = 30):
    size = path.stat().st_size
    chunk = min(size, 64 * 1024)
    with path.open("rb") as f:
        f.seek(max(0, size - chunk))
        data = f.read().decode("utf-8", "replace")
    lines = [ln for ln in data.splitlines() if ln.strip()]
    return lines[-n:]


def agent_procs():
    found = {}
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.AccessDenied, psutil.ZombieProcess):
            continue
        for agent in AGENTS:
            if agent in cmd and proc.info["name"] and "python" in proc.info["name"]:
                found[agent] = proc
    return found


def gpu_info():
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        util, used, total = [x.strip() for x in out.stdout.split(",")]
        return f"{util}% | {used} / {total} MB"
    except Exception:
        return "n/a"


@st.fragment(run_every="3s")
def log_section(agent, procs):
    proc = procs.get(agent)
    log = find_log(agent)
    st.subheader(f"Agent: {agent}")
    if proc:
        pct = proc.cpu_percent(interval=None) or 0
        mem = psutil.Process(proc.pid).memory_info().rss / 1e9
        uptime = int(time.time() - (proc.info["create_time"] or time.time()))
        st.markdown(
            f"`pid {proc.pid}` · CPU `{pct:.0f}%` · RAM `{mem:.2f} GB` · "
            f"up `{uptime // 60 // 60}h {(uptime // 60) % 60}m`"
        )
    else:
        st.markdown("`not running`")
    if log:
        size_kb = log.stat().st_size / 1024
        st.caption(
            f"{log.name} · "
            f"{datetime.fromtimestamp(log.stat().st_mtime):%H:%M:%S} · {size_kb:.0f} KB"
        )
        last_header = ""
        for line in tail(log):
            if line.startswith("==="):
                last_header = line
        if last_header:
            st.markdown(f"**{last_header}**")
        st.code("\n".join(tail(log, 22)), language=None)
    else:
        st.info("no log found yet")


@st.fragment(run_every="5s")
def registry_section():
    st.subheader("Sweep registry — best OOS per pair")
    if not SWEEP_REGISTRY.exists():
        st.info("no model yet — still training")
        return
    import json

    reg = json.loads(SWEEP_REGISTRY.read_text())
    if not reg:
        st.info("empty registry")
        return
    rows = [
        {
            "symbol": r["symbol"],
            "gran": r["granularity"],
            "oos_sharpe": r["oos_sharpe"],
            "oos_ret": r["oos_return"],
            "trades": r["oos_trades"],
            "win_rate": r.get("oos_win_rate"),
            "pre_S": r["oos_pre_sharpe"],
            "config": r["selected"]["config"],
            "seed": r["selected"]["seed"],
            "created": r["created"][:16],
            "model": r["model"],
        }
        for r in reg
    ]
    st.dataframe(rows, use_container_width=True)


@st.fragment(run_every="10s")
def market_section():
    st.subheader("Market + cache freshness")
    try:
        client = make_data_client()
        prices = {s: client.latest_price(s) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
        st.markdown(" | ".join(f"**{s[:3]}** `{p:.2f}`" for s, p in prices.items()))
    except Exception as e:
        st.caption(f"price fetch failed: {e}")
    cache = DataCache()
    covs = [
        (s, g, cache.coverage(s, g))
        for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        for g in ("5m", "1h", "4h", "1m")
    ]
    st.dataframe(
        [
            {
                "symbol": s,
                "gran": g,
                "rows": c[2],
                "last_bar": c[1][:16] if c[1] else "-",
            }
            for s, g, c in covs
        ],
        use_container_width=True,
    )


st.title("Live Training Progress")
st.caption(
    f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC · "
    f"GPU: {gpu_info()} · auto-refresh"
)

tickers = st.columns(3)
with tickers[0]:
    log_section("sweep_prod", agent_procs())
with tickers[1]:
    log_section("train_many", agent_procs())
with tickers[2]:
    market_section()

registry_section()
