/* ============================================================
   Apex — app shell
   - hash router (#home / #auth / #trade)
   - local auth gate (simulation environment)
   - live trading dashboard: Bitget WebSocket candles, RL predictions,
     HMM regime state, simulated account
   ============================================================ */

"use strict";

/* ---------------- auth ---------------- */
const AUTH_KEY = "apex_session_v1";

function saveSession() { localStorage.setItem(AUTH_KEY, "1"); }
function clearSession() { localStorage.removeItem(AUTH_KEY); }
function hasSession() { return localStorage.getItem(AUTH_KEY) === "1"; }

/* ---------------- router ---------------- */
function showView(name) {
  ["landing", "auth", "dash"].forEach((v) => {
    $("view-" + v).classList.toggle("hidden", v !== name);
  });
  window.scrollTo(0, 0);
}

function route() {
  const h = (location.hash || "#home").replace("#", "");
  if (h === "trade" && !hasSession()) { location.hash = "auth"; return; }
  if (h === "trade") { showView("dash"); bootDash(); }
  else if (h === "auth") { showView("auth"); }
  else { showView("landing"); }
}

/* ---------------- trading state ---------------- */
let SYMBOLS = [];
let GRANS = [];
let CRYPTO_GRANS = [];
let MODELS_INFO = { symbols: [], granularities: [], crypto_grans: [], available: {} };

const WS_BASE = "wss://ws.bitget.com/v2/ws/public";
const GRAN_TO_CHANNEL = {
  "1m": "candle1m", "5m": "candle5m", "15m": "candle15m",
  "1h": "candle1H", "4h": "candle4H", "1d": "candle1D",
};
const GRAN_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
const POLL_MS = 60000;

const state = {
  symbol: "BTCUSDT",
  gran: "5m",
  model: "prod",
  models: {},
  ws: null,
  wsTimer: null,
  pollTimer: null,
  chart: null,
  candles: null,
  spark: null,
  _markers: [],
  lastPredBar: 0,
  lastPolledBar: 0,
  barTime: 0,
  lastClosedTime: 0,
  firstBar: true,
  predInFlight: false,
  countDown: null,
  booted: false,
};

function isCryptoSymbol(sym) {
  const info = (MODELS_INFO.symbols || []).find((s) => s.symbol === sym);
  return info ? !!info.crypto : false;
}
function gransForSymbol(sym) {
  const all = isCryptoSymbol(sym) ? (CRYPTO_GRANS.length ? CRYPTO_GRANS : GRANS) : ["1d"];
  const withModels = all.filter((g) => state.models[`${sym}|${g}`]);
  return withModels.length ? withModels : all;
}

const $ = (id) => document.getElementById(id);

async function fetchJSON(url, opts = {}, ms = 20000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(url, { ...opts, signal: ctrl.signal });
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

function fmt(n, d = 2) {
  return Number(n).toLocaleString("en-US", {
    minimumFractionDigits: d, maximumFractionDigits: d,
  });
}
function setConn(text, cls) {
  const el = $("conn");
  el.textContent = text;
  el.className = "chip" + (cls ? " " + cls : "");
}

/* ---------------- Bitget feed ---------------- */
function parseBitgetCandle(arr) {
  return {
    time: Math.floor(Number(arr[0]) / 1000),
    open: +arr[1],
    high: +arr[2],
    low: +arr[3],
    close: +arr[4],
    volume: +arr[5],
  };
}

function stopCountdown() {
  if (state.countDown) { clearInterval(state.countDown); state.countDown = null; }
}
function startCountdown(closeEpochS) {
  const badge = $("nextBadge");
  stopCountdown();
  const tick = () => {
    const now = Date.now() / 1000;
    let left = Math.max(0, Math.round(closeEpochS - now));
    const m = Math.floor(left / 60);
    const s = left % 60;
    badge.textContent = `${m}:${s.toString().padStart(2, "0")} left`;
    badge.style.color = left <= 5 ? "var(--down)" : "var(--up)";
  };
  tick();
  state.countDown = setInterval(tick, 1000);
}

/* ---------------- chart ---------------- */
function initChart() {
  if (state.chart) { state.chart.remove(); state.chart = null; }
  state.chart = LightweightCharts.createChart($("chart"), {
    layout: {
      background: { type: "solid", color: "transparent" },
      textColor: "#98989d",
      fontSize: 12,
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.045)" },
      horzLines: { color: "rgba(255,255,255,0.045)" },
    },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.09)", scaleMargins: { top: 0.1, bottom: 0.25 } },
    timeScale: { borderColor: "rgba(255,255,255,0.09)", timeVisible: true, secondsVisible: false },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#5ac8fa", width: 1, style: 3, labelBackgroundColor: "#0071e3" },
      horzLine: { color: "#5ac8fa", width: 1, style: 3, labelBackgroundColor: "#0071e3" },
    },
    autoSize: true,
  });
  state.candles = state.chart.addCandlestickSeries({
    upColor: "#22c55e", downColor: "#ef4444",
    borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444",
    priceLineVisible: false,
  });
  const sparkWrap = $("equitySpark");
  if (state.spark) { state.spark.chart().remove(); state.spark = null; }
  state.spark = LightweightCharts.createChart(sparkWrap, {
    layout: { background: { type: "solid", color: "transparent" }, textColor: "transparent" },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    timeScale: { visible: false },
    rightPriceScale: { visible: false },
    autoSize: true,
  }).addAreaSeries({
    lineColor: "#0a84ff", topColor: "rgba(10,132,255,.3)", bottomColor: "rgba(10,132,255,0)",
    lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
}

/* ---------------- data ---------------- */
async function loadHistory() {
  const url = `/api/history?symbol=${state.symbol}&gran=${state.gran}&n=420`;
  const data = await fetchJSON(url, {}, 20000);
  if (!data.bars) throw new Error(data.error || "no history");
  state.candles.setData(data.bars);
  state.chart.timeScale().scrollToPosition(-5, false);
  state.firstBar = true;
  const last = data.bars[data.bars.length - 1];
  if (last) state.lastPolledBar = last.time;
  $("pairChip").textContent = `${state.symbol} · ${state.gran.toUpperCase()}`;
  if (last) $("kvPrice").textContent = fmt(last.close, priceDecimals(last.close));
  return data;
}

function priceDecimals(p) {
  return p >= 100 ? 2 : p >= 1 ? 4 : 6;
}

function onKline(bar) {
  state.candles.update(bar);
  if (bar.time > state.barTime) {
    if (state.barTime > 0) {
      state.lastClosedTime = state.barTime;
      runPrediction();
    }
    state.barTime = bar.time;
    startCountdown(bar.time + GRAN_SECONDS[state.gran]);
  }
  $("kvPrice").textContent = fmt(bar.close, priceDecimals(bar.close));
  state.candles.setCrosshair(null);
}

function onBitgetMsg(msg) {
  if (!msg || !msg.data) return;
  if (msg.action === "snapshot") {
    const bars = msg.data.map(parseBitgetCandle);
    bars.forEach((b) => state.candles.update(b));
    const last = bars[bars.length - 1];
    if (last) {
      state.barTime = last.time;
      startCountdown(last.time + GRAN_SECONDS[state.gran]);
    }
    setConn("REALTIME SIM ● live", "ok");
    return;
  }
  const bar = parseBitgetCandle(msg.data[0]);
  const closed = bar.time !== state.barTime;
  onKline(bar);
  state.chart.timeScale().scrollToRealTime();
  if (closed) {
    setConn("REALTIME SIM ● live", "ok");
    startCountdown(bar.time + GRAN_SECONDS[state.gran]);
  }
}

/* ---------------- prediction ---------------- */
async function runPrediction() {
  if (state.predInFlight) return;
  const models = state.models[`${state.symbol}|${state.gran}`] || [];
  if (!models.length) return;
  state.predInFlight = true;
  try {
    const p = await fetchJSON("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.symbol, gran: state.gran, model: state.model }),
    }, 60000);
    if (p.error) throw new Error(p.error);
    if (state.lastPredBar === p.bar_time && state.lastClosedTime > 0) {
      drawPrediction(p);
      return;
    }
    state.lastPredBar = p.bar_time;
    drawPrediction(p);
  } catch (e) {
    setConn(e.message || "predict err", "err");
  } finally {
    state.predInFlight = false;
  }
}

function drawRegime(r) {
  if (!r || !r.fitted) {
    const chip = $("regimeChip");
    chip.textContent = "fitting…";
    chip.className = "regime-chip regime-chop";
    $("regimeFactor").textContent = "size —";
    $("regProbs").textContent = "—";
    return;
  }
  const chip = $("regimeChip");
  const label = (r.label || "CHOP").toUpperCase();
  chip.textContent = label;
  chip.className = "regime-chip regime-" + label.toLowerCase();
  $("regimeFactor").textContent = `size ${(r.size_factor || 1).toFixed(2)}×`;
  const probs = r.probs || {};
  $("regProbs").textContent =
    Object.entries(probs).map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join("  ");
}

function drawPrediction(p) {
  const conf = p.confidence;
  const badge = $("predBadge");
  const cls = p.label === "LONG" ? "long" : p.label === "SHORT" ? "short" : "neutral";
  badge.className = "pred-badge " + cls;
  const lean = (p.bias && p.bias !== "NEUTRAL" && p.signal === 0)
    ? ` · lean ${p.bias}` : "";
  badge.textContent = `NEXT BAR: ${p.label} ${p.signal === 0 ? "" : p.signal > 0 ? "▲" : "▼"} ${(conf * 100).toFixed(1)}%${lean}`;
  badge.classList.remove("hidden");

  $("kvSignal").textContent = (p.bias && p.bias !== "NEUTRAL" && p.signal === 0)
    ? `HOLD · lean ${p.bias}` : p.label;
  $("kvSignal").style.color = p.signal > 0 ? "var(--up)" : p.signal < 0 ? "var(--down)" : "var(--dim)";
  $("kvConf").textContent = (conf * 100).toFixed(1) + "%";
  $("kvTrend").textContent = (p.trend * 100).toFixed(2) + "%";
  $("kvModel").textContent = p.model;
  $("kvBar").textContent = new Date(p.bar_time * 1000).toUTCString().slice(0, 22);
  $("kvVolTarget").textContent = (p.vol_target || 0).toFixed(2);

  drawRegime(p.regime);

  const shape = p.signal > 0 ? "arrowUp" : p.signal < 0 ? "arrowDown" : "circle";
  const color = p.signal > 0 ? "#22c55e" : p.signal < 0 ? "#ef4444" : "#6e6e73";
  const position = p.signal > 0 ? "belowBar" : "aboveBar";
  const text = p.label === "NEUTRAL" ? "HOLD" : `${p.label} ${(conf * 100).toFixed(0)}%`;
  const markers = state._markers || [];
  const exists = markers.findIndex((m) => m.time === p.bar_time);
  const entry = { time: p.bar_time, position, color, shape, text, size: 1 };
  if (exists >= 0) markers[exists] = entry; else markers.push(entry);
  if (markers.length > 40) markers.shift();
  state.candles.setMarkers(markers);
  state._markers = markers;

  const a = p.account || {};
  $("acctBal").textContent = fmt(a.balance, 2);
  $("acctPos").textContent = a.position_label || "NEUTRAL";
  $("acctPos").style.color = a.position > 0 ? "var(--up)" : a.position < 0 ? "var(--down)" : "var(--dim)";
  $("acctPnl").textContent = (a.pnl >= 0 ? "+" : "") + fmt(a.pnl, 2);
  $("acctPnl").style.color = a.pnl >= 0 ? "var(--up)" : "var(--down)";
  $("acctRet").textContent = (a.return * 100).toFixed(2) + "%";
  $("acctRet").style.color = a.return >= 0 ? "var(--up)" : "var(--down)";

  const ddRow = $("ddRow");
  const ddBadge = $("ddBadge");
  ddRow.classList.remove("hidden");
  if (a.dd_locked) {
    ddBadge.textContent = "LOCKED";
    ddBadge.className = "dd-locked";
    ddRow.style.borderColor = "rgba(239,68,68,0.35)";
  } else {
    ddBadge.textContent = `OK · ${(a.drawdown * 100).toFixed(1)}%`;
    ddBadge.className = "dd-ok";
    ddRow.style.borderColor = "";
  }

  const pts = (a.curve || []).map((pt) => ({ time: pt.t, value: pt.e })).slice(-120);
  if (pts.length > 1 && state.spark) state.spark.setData(pts);

  const log = $("signalLog");
  const row = document.createElement("div");
  row.className = "row";
  const now = new Date().toLocaleTimeString("en-GB");
  row.innerHTML =
    `<span class="t">${now}</span>` +
    `<span class="sig ${cls}">${p.label} ${(conf * 100).toFixed(1)}% · ${fmt(p.price, 2)}</span>`;
  log.prepend(row);
  while (log.children.length > 40) log.removeChild(log.lastChild);
}

/* ---------------- feed plumbing ---------------- */
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}
function startPolling() {
  stopPolling();
  setConn("LIVE ● poll 60s", "ok");
  state.pollTimer = setInterval(async () => {
    try {
      const data = await loadHistory();
      const bars = data.bars || [];
      const lastBar = bars.length ? bars[bars.length - 1].time : 0;
      if (lastBar > state.lastPolledBar) {
        state.lastPolledBar = lastBar;
        runPrediction();
      }
    } catch (e) { /* keep polling */ }
  }, POLL_MS);
}

function connectWS() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
  }
  stopCountdown();
  if (!isCryptoSymbol(state.symbol)) { startPolling(); return; }
  setConn("REALTIME SIM ● connecting…");
  const chan = GRAN_TO_CHANNEL[state.gran];
  const ws = new WebSocket(WS_BASE);
  state.ws = ws;
  ws.onopen = () => {
    ws.send(JSON.stringify({
      op: "subscribe",
      args: [{ instType: "SPOT", channel: chan, instId: state.symbol }],
    }));
  };
  ws.onmessage = (ev) => {
    try { onBitgetMsg(JSON.parse(ev.data)); } catch (e) { /* skip */ }
  };
  ws.onerror = () => setConn("feed error", "err");
  ws.onclose = () => {
    setConn("REALTIME SIM ● reconnecting…", "");
    clearTimeout(state.wsTimer);
    state.wsTimer = setTimeout(connectWS, 3000);
  };
}

/* ---------------- models ---------------- */
async function loadModels() {
  const data = await fetchJSON("/api/models", {}, 15000);
  MODELS_INFO = data;
  SYMBOLS = data.symbols || [];
  GRANS = data.granularities || [];
  CRYPTO_GRANS = data.crypto_grans || [];
  state.models = data.available || {};
  renderSymbolTabs();
  renderGranTabs();
  syncModelSelect();
}

function renderSymbolTabs() {
  const wrap = $("symbolTabs");
  wrap.innerHTML = "";
  (SYMBOLS || []).forEach((s) => {
    const b = document.createElement("button");
    b.dataset.symbol = s.symbol;
    b.className = "seg-btn" + (s.symbol === state.symbol ? " active" : "");
    b.innerHTML = s.label.replace(/\s+/g, "&nbsp;");
    b.addEventListener("click", () => switchSymbol(s.symbol));
    wrap.appendChild(b);
  });
}

function renderGranTabs() {
  const wrap = $("granTabs");
  wrap.innerHTML = "";
  const grans = gransForSymbol(state.symbol);
  if (!grans.includes(state.gran)) state.gran = grans[0];
  grans.forEach((g) => {
    const b = document.createElement("button");
    b.dataset.gran = g;
    b.className = "seg-btn" + (g === state.gran ? " active" : "");
    b.textContent = g;
    b.addEventListener("click", () => switchGran(g));
    wrap.appendChild(b);
  });
}

function syncModelSelect() {
  const sel = $("modelSelect");
  const names = state.models[`${state.symbol}|${state.gran}`] || [];
  sel.innerHTML = "";
  if (!names.length) {
    const o = document.createElement("option");
    o.textContent = "no model for this pair";
    o.value = "";
    sel.appendChild(o);
    sel.disabled = true;
    $("nextBadge").textContent = "—";
    $("predBadge").classList.add("hidden");
    return;
  }
  sel.disabled = false;
  names.forEach((n) => {
    const o = document.createElement("option");
    o.value = n;
    o.textContent = n;
    sel.appendChild(o);
  });
  if (!names.includes(state.model)) state.model = names[0];
  sel.value = state.model;
}

/* ---------------- switching ---------------- */
function switchSymbol(sym) {
  state.symbol = sym;
  renderSymbolTabs();
  renderGranTabs();
  syncModelSelect();
  restart();
}
function switchGran(gran) {
  state.gran = gran;
  renderGranTabs();
  syncModelSelect();
  restart();
}
function restart() {
  if (state.ws) { state.ws.onclose = null; state.ws.close(); }
  stopPolling();
  clearTimeout(state.wsTimer);
  stopCountdown();
  state._markers = [];
  state.lastPredBar = 0;
  state.lastPolledBar = 0;
  state.barTime = 0;
  state.lastClosedTime = 0;
  state.firstBar = true;
  $("signalLog").innerHTML = "";
  $("nextBadge").textContent = "—";
  $("predBadge").classList.add("hidden");
  initChart();
  loadHistory().catch(() => setConn("history err", "err"));
  connectWS();
}

/* ---------------- boot ---------------- */
function bootDash() {
  if (state.booted) return;
  state.booted = true;
  initChart();
  loadModels();
  loadHistory()
    .then(() => { setConn("REALTIME SIM ● starting…"); runPrediction(); connectWS(); })
    .catch(() => { setConn("history err", "err"); connectWS(); });
  $("modelSelect").addEventListener("change", (e) => { state.model = e.target.value; });
  $("resetBtn").addEventListener("click", async () => {
    await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.symbol, gran: state.gran }),
    });
    $("acctBal").textContent = "100,000.00";
    $("acctPos").textContent = "NEUTRAL";
    $("acctPos").style.color = "var(--dim)";
    $("acctPnl").textContent = "0.00";
    $("acctPnl").style.color = "var(--text)";
    $("acctRet").textContent = "0.00%";
    $("acctRet").style.color = "var(--text)";
  });
}

/* ---------------- auth wiring ---------------- */
$("authForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const email = $("authEmail").value.trim();
  const pass = $("authPass").value;
  const valid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) && pass.length >= 6;
  if (!valid) {
    $("authErr").classList.remove("hidden");
    return;
  }
  $("authErr").classList.add("hidden");
  saveSession();
  location.hash = "trade";
});

$("authEmail").addEventListener("input", () => $("authErr").classList.add("hidden"));
$("authPass").addEventListener("input", () => $("authErr").classList.add("hidden"));

window.addEventListener("hashchange", route);
route();