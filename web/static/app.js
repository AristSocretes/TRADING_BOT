/* TradingView-style live chart + predictions.
   Real-time candlesticks (forming + closed) stream live from the Bitget public
   WebSocket (Binance hosts are DNS-blocked in this region) — this IS the live
   "simulated candlestick" view the chart is drawn from. All model inference +
   the simulated paper account come from the local FastAPI server. */

"use strict";

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const GRANS = ["1m", "5m", "15m", "1h", "4h"];
const WS_BASE = "wss://ws.bitget.com/v2/ws/public";
// Bitget V2 spot candle channel names per timeframe
const GRAN_TO_CHANNEL = {
  "1m": "candle1m", "5m": "candle5m", "15m": "candle15m",
  "1h": "candle1H", "4h": "candle4H",
};
const GRAN_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400 };

const state = {
  symbol: "BTCUSDT",
  gran: "5m",
  model: "prod",
  models: {},            // "SYM|GRAN" -> [names]
  ws: null,
  wsTimer: null,
  chart: null,
  candles: null,
  markers: null,
  spark: null,
  _markers: [],
  barTime: 0,            // open-time (s) of the currently-forming candle
  lastClosedTime: 0,     // open-time (s) of the last bar the model was run on
  firstBar: true,
  predInFlight: false,
  countDown: null,       // setInterval id for the next-bar countdown
};

const $ = (id) => document.getElementById(id);

/* ---------------- helpers ---------------- */
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

/* Bitget message: {action, arg:{channel}, data:[[timeMs,o,h,l,c,v,quoteVol,...]]}
   - snapshot: full history tail (we already have history from server, so we
     just seed the forming candle from the snapshot's last entry).
   - update: forming candle for the current period (re-pushes on every trade). */
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
      background: { type: "solid", color: "#131722" },
      textColor: "#d1d4dc",
      fontSize: 12,
    },
    grid: {
      vertLines: { color: "#1e222d" },
      horzLines: { color: "#1e222d" },
    },
    rightPriceScale: { borderColor: "#2a2e39", scaleMargins: { top: 0.1, bottom: 0.25 } },
    timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#758696", width: 1, style: 3, labelBackgroundColor: "#2962ff" },
      horzLine: { color: "#758696", width: 1, style: 3, labelBackgroundColor: "#2962ff" },
    },
    autoSize: true,
  });
  state.candles = state.chart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350",
    borderVisible: false, wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    priceLineVisible: false,
  });
  const sparkWrap = $("equitySpark");
  if (state.spark) { state.spark.chart().remove(); state.spark = null; }
  state.spark = LightweightCharts.createChart(sparkWrap, {
    layout: { background: { type: "solid", color: "#1e222d" }, textColor: "transparent" },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    timeScale: { visible: false },
    rightPriceScale: { visible: false },
    autoSize: true,
  }).addAreaSeries({
    lineColor: "#2962ff", topColor: "rgba(41,98,255,.35)", bottomColor: "rgba(41,98,255,0)",
    lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
}

/* ---------------- data load ---------------- */
async function loadHistory() {
  const url = `/api/history?symbol=${state.symbol}&gran=${state.gran}&n=420`;
  const res = await fetch(url);
  const data = await res.json();
  if (!data.bars) throw new Error(data.error || "no history");
  state.candles.setData(data.bars);
  state.chart.timeScale().scrollToPosition(-5, false);
  state.firstBar = true;
  const last = data.bars[data.bars.length - 1];
  $("pairChip").textContent = `${state.symbol} · ${state.gran.toUpperCase()}`;
  if (last) $("kvPrice").textContent = fmt(last.close, priceDecimals(last.close));
  return data;
}

function priceDecimals(p) {
  return p >= 100 ? 2 : p >= 1 ? 4 : 6;
}

function onKline(bar, closed) {
  // bar.time is the open-time (s) of the candle. lightweight-charts.update()
  // merges by time, so the in-progress candle is drawn live (realtime sim).
  state.candles.update(bar);
  if (bar.time > state.barTime) {
    // a NEW candle opened -> the previous one just closed
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
  const action = msg.action;
  if (action === "snapshot") {
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
  // action === "update" -> live forming candle (the simulated candlestick)
  const bar = parseBitgetCandle(msg.data[0]);
  const closed = bar.time !== state.barTime; // new period arrived == previous closed
  onKline(bar, closed);
  if (closed) {
    state.chart.timeScale().scrollToRealTime();
    setConn("REALTIME SIM ● live", "ok");
    startCountdown(bar.time + GRAN_SECONDS[state.gran]);
  } else {
    state.chart.timeScale().scrollToRealTime();
  }
}

/* ---------------- prediction ---------------- */
async function runPrediction() {
  const now = Date.now() / 1000;
  if (state.predInFlight) return;
  // server computes the prediction on `bar_time` = last closed bar in its cache;
  // if it has not advanced past the last bar we predicted on, skip the duplicate
  const models = state.models[`${state.symbol}|${state.gran}`] || [];
  const hasModel = !!models.length;
  if (!hasModel) return;
  state.predInFlight = true;
  try {
    const res = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.symbol, gran: state.gran, model: state.model }),
    });
    const p = await res.json();
    if (p.error) throw new Error(p.error);
    if (p.bar_time <= state.lastClosedTime - 1 && state.lastPredBar === p.bar_time) {
      // already saw this bar's prediction
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

function drawPrediction(p) {
  const conf = p.confidence;
  const badge = $("predBadge");
  const cls = p.label === "LONG" ? "long" : p.label === "SHORT" ? "short" : "neutral";
  badge.className = "pred-badge " + cls;
  badge.textContent = `NEXT BAR: ${p.label} ${p.signal === 0 ? "" : p.signal > 0 ? "▲" : "▼"} ${(conf * 100).toFixed(1)}%`;
  badge.classList.remove("hidden");

  $("kvSignal").textContent = p.label;
  $("kvSignal").style.color = p.signal > 0 ? "var(--up)" : p.signal < 0 ? "var(--down)" : "var(--dim)";
  $("kvConf").textContent = (conf * 100).toFixed(1) + "%";
  $("kvTrend").textContent = (p.trend * 100).toFixed(2) + "%";
  $("kvModel").textContent = p.model;
  $("kvBar").textContent = new Date(p.bar_time * 1000).toUTCString().slice(0, 22);

  // marker on the bar the signal was formed on
  const shape = p.signal > 0 ? "arrowUp" : p.signal < 0 ? "arrowDown" : "circle";
  const color = p.signal > 0 ? "#26a69a" : p.signal < 0 ? "#ef5350" : "#787b86";
  const position = p.signal > 0 ? "belowBar" : "aboveBar";
  const text = p.label === "NEUTRAL" ? "HOLD" : `${p.label} ${(conf * 100).toFixed(0)}%`;
  const markers = state._markers || [];
  const exists = markers.findIndex((m) => m.time === p.bar_time);
  const entry = { time: p.bar_time, position, color, shape, text, size: 1 };
  if (exists >= 0) markers[exists] = entry; else markers.push(entry);
  if (markers.length > 40) markers.shift();
  state.candles.setMarkers(markers);
  state._markers = markers;

  // account
  const a = p.account;
  $("acctBal").textContent = fmt(a.balance, 2);
  $("acctPos").textContent = a.position_label;
  $("acctPos").style.color = a.position > 0 ? "var(--up)" : a.position < 0 ? "var(--down)" : "var(--dim)";
  $("acctPnl").textContent = (a.pnl >= 0 ? "+" : "") + fmt(a.pnl, 2);
  $("acctPnl").style.color = a.pnl >= 0 ? "var(--up)" : "var(--down)";
  $("acctRet").textContent = (a.return * 100).toFixed(2) + "%";
  $("acctRet").style.color = a.return >= 0 ? "var(--up)" : "var(--down)";
  const pts = a.curve.map((pt, i) => ({ time: pt.t, value: pt.e })).slice(-120);
  if (pts.length > 1 && state.spark) {
    state.spark.setData(pts);
  }

  // log
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

/* ---------------- websocket (Bitget live feed) ---------------- */
function connectWS() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
  }
  stopCountdown();
  setConn("REALTIME SIM ● connecting…");
  const chan = GRAN_TO_CHANNEL[state.gran];
  const ws = new WebSocket(WS_BASE);
  state.ws = ws;
  ws.onopen = () => {
    const payload = JSON.stringify({
      op: "subscribe",
      args: [{ instType: "SPOT", channel: chan, instId: state.symbol }],
    });
    ws.send(payload);
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

/* ---------------- model availability ---------------- */
async function loadModels() {
  const res = await fetch("/api/models");
  const data = await res.json();
  state.models = data.available || {};
  syncModelSelect();
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
  document.querySelectorAll("#symbolTabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.symbol === sym));
  syncModelSelect();
  restart();
}
function switchGran(gran) {
  state.gran = gran;
  document.querySelectorAll("#granTabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.gran === gran));
  syncModelSelect();
  restart();
}
function restart() {
  if (state.ws) { state.ws.onclose = null; state.ws.close(); }
  clearTimeout(state.wsTimer);
  stopCountdown();
  state._markers = [];
  state.lastPredBar = 0;
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
initChart();
loadModels();
loadHistory()
  .then(() => { setConn("REALTIME SIM ● starting…"); runPrediction(); connectWS(); })
  .catch(() => { setConn("history err", "err"); connectWS(); });

document.querySelectorAll("#symbolTabs button").forEach((b) =>
  b.addEventListener("click", () => switchSymbol(b.dataset.symbol)));
document.querySelectorAll("#granTabs button").forEach((b) =>
  b.addEventListener("click", () => switchGran(b.dataset.gran)));
$("modelSelect").addEventListener("change", (e) => {
  state.model = e.target.value;
});
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