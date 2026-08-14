"""Sweep results dashboard – visualises the 74‑candidate sweep across 10 pairs.

Run:
    streamlit run scripts/sweep_dashboard.py

Sections:
  1. Overall summary – best OOS Sharpe per pair, count of positive/negative candidates.
  2. Per‑pair detail – all candidates with OOS Sharpe, return, trades, pre‑holdout Sharpe.
  3. Config‑wise summary – which config (legacy_lowcost, mid, etc.) performed best on which pair.
  4. Best‑model showcase – the promoted model (BTC 5m legacy_lowcost seed 123) with its metrics.
  5. Raw registry export – downloadable JSON.
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Paths ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SWEEP_REGISTRY = ROOT / "models" / "sweep" / "registry.json"
BEST_MODEL = ROOT / "models" / "prod_win" / "BTCUSDT_5m.zip"

# ── Helper ────────────────────────────────────────────────────────────
def load_registry():
    if not SWEEP_REGISTRY.exists():
        return []
    return json.loads(SWEEP_REGISTRY.read_text())

def cfg_name(cfg):
    return {
        "legacy_lowcost": "Legacy low‑cost",
        "legacy_lowcost_1M": "Legacy 1M",
        "mid": "Mid penalties",
        "legacy_noalign": "Legacy no‑align",
        "legacy_raw": "Legacy raw",
        "robust_1M": "Robust 1M",
        "full_feat_lowcost": "Full‑feat low‑cost",
    }.get(cfg, cfg)

# ── Page ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Sweep Results Dashboard", layout="wide")
st.title("📊 Sweep Results Dashboard")
st.caption(
    f"Generated from {SWEEP_REGISTRY.name} · {len(load_registry())} candidates across 10 pairs"
)

reg = load_registry()
if not reg:
    st.info("No registry found – run `scripts/sweep_prod.py` first.")
    st.stop()

# ── Helpers ───────────────────────────────────────────────────────────
def pair_key(r):
    return (r["symbol"], r["granularity"])

def make_summary(reg):
    df = pd.DataFrame(reg)
    df["oos_sharpe"] = pd.to_numeric(df["oos_sharpe"], errors="coerce")
    df["oos_return"] = pd.to_numeric(df["oos_return"], errors="coerce")
    df["oos_trades"] = pd.to_numeric(df["oos_trades"], errors="coerce")
    # best per pair
    best = df.loc[df.groupby("symbol")["oos_sharpe"].idxmax()]
    # config wise
    cfg_best = (
        df.groupby("selected.config")["oos_sharpe"]
        .mean()
        .sort_values(ascending=False)
        .reset_index()
    )
    return df, best, cfg_best

df, best_pair, cfg_wise = make_summary(reg)

# ── Title / KPI row ──────────────────────────────────────────────────
st.header("📈 Sweep Summary")
col1, col2, col3 = st.columns(3)
with col1:
    best_pair_str = f"{best_pair['symbol']} {best_pair['granularity']}"
    st.metric(
        "Best OOS Sharpe",
        f"{best_pair['oos_sharpe']:.3f}",
        f"{best_pair['oos_return']:+.3f} return",
    )
with col2:
    pos = (reg["oos_sharpe"] > 0).sum()
    st.metric("Positive OOS candidates", pos, f"{pos}/{len(reg)}")
with col3:
    st.metric("Promoted model", best_pair["model"],
              f"{best_pair['selected']['config']}/s{best_pair['selected']['seed']}")

# ── Pair‑wise detail ────────────────────────────────────────────────
st.header("📍 Per‑pair results")
pairs = sorted(df["symbol"].unique()) + ["All"]
pair_sel = st.selectbox(
    "Select pair", pairs, index=pairs.index("BTCUSDT") if "BTCUSDT" in pairs else 0
)

if pair_sel == "All":
    sub = df
else:
    sub = df[df["symbol"] == pair_sel]

st.dataframe(
    sub[
        [
            "granularity",
            "selected",
            "oos_sharpe",
            "oos_return",
            "oos_trades",
            "oos_pre_sharpe",
            "config",
            "selected.seed",
        ]
    ].assign(
        oos_sharpe=lambda d: d["oos_sharpe"].map(lambda x: f"{x:.3f}"),
        oos_return=lambda d: d["oos_return"].map(lambda x: f"{x:+.4f}"),
    ),
    use_container_width=True,
    column_config={
        "selected": st.column_config.SelectboxSelector(
            "Best config/seed",
            help="Config + seed that achieved this OOS Sharpe",
        ),
    },
)

# ── Config‑wise summary ─────────────────────────────────────────────
st.header("📊 Config‑wise performance")
cfg_wise_df = pd.DataFrame(cfg_wise).rename(
    columns={"index": "config", "oos_sharpe": "avg_oos_sharpe"}
)
cfg_cfg = cfg_wise_df["config"].map(cfg_name)
st.bar_chart(
    cfg_wise_df.set_index(cfg_cfg)["avg_oos_sharpe"],
    use_container_width=True,
    caption="Average OOS Sharpe per config (across all pairs & seeds)",
)

# ── Best‑model showcase ─────────────────────────────────────────────
st.header("🏆 Promoted model")
if BEST_MODEL.exists():
    st.success(f"Promoted model: **{BEST_MODEL.name}**")
    with st.expander("Metrics"):
        st.write(f"• OOS Sharpe: **{best_pair['oos_sharpe']:.3f}**")
        st.write(f"• OOS Return: **{best_pair['oos_return']:+.4f}**")
        st.write(f"• OOS Trades: **{best_pair['oos_trades']}**")
        st.write(
            f"• Pre‑holdout Sharpe: **{best_pair['oos_pre_sharpe']:.2f}**"
        )
        st.write(
            f"• Config: **{best_pair['selected']['config']}** "
            f"(seed **{best_pair['selected']['seed']}**)",
        )
        st.write(f"• Model path: **{BEST_MODEL}**")
    if st.button("Open model in viewer"):
        st.code(f"python -m stable_baselines3.common.policies -m {BEST_MODEL}", language="python")
else:
    st.warning("Promoted model not found at `models/prod_win/BTCUSDT_5m.zip`")

# ── Registry export ─────────────────────────────────────────────────
st.download_button(
    "Download full registry JSON",
    data=Path(SWEEP_REGISTRY).read_bytes(),
    file_name="sweep_registry.json",
    mime="application/json",
)

# ── Footer ────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    f"Sweep ran {len(reg)} candidates (7 configs × multi‑seed) · "
    f"Best model promoted to `models/prod_win/BTCUSDT_5m.zip`"
)