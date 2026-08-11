import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from bot.trading.journal import Journal  # noqa: E402

st.set_page_config(page_title="Trading Bot", layout="wide")
st.title("AI Forex Trading Bot")

journal = Journal()
equity = journal.equity_curve()
st.subheader("Equity curve")
if not equity.empty:
    st.line_chart(equity.set_index("ts")["equity"])
else:
    st.info("No equity rows yet.")

st.subheader("Recent signals")
st.dataframe(journal.recent_signals())
