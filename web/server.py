"""TradingView-style prediction web app backend.

Frontend streams live candles straight from the Binance public WebSocket
(lowest possible latency); this server handles everything model/simulation
side and is served at http://localhost:8080

Endpoints:
  GET  /api/models                    - available (symbol, gran) -> models
  GET  /api/history?symbol=&gran=&n=  - OHLCV for initial chart (from cache)
  POST /api/predict                   - {symbol, gran, model} ->
                                          prediction + simulated account update
  POST /api/reset?symbol=&gran=       - reset simulated account for a pair
"""

import json
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.ai.regime_hmm import RegimeHMM  # noqa: E402
from bot.data.cache import DataCache  # noqa: E402
from bot.data.clients import make_data_client  # noqa: E402
from bot.data.features import FEATURE_COLUMNS, normalized_frame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
WEB = ROOT / "web"

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "PAXGUSDT",
           "^GSPC", "^IXIC", "^FTSE", "^GDAXI", "^N225",
           "EURUSD=X", "USDINR=X", "SLV")
# Bitget-spot instruments get the live WS feed + full granularity set;
# everything else comes from Yahoo Finance as daily candles (poll-refresh).
CRYPTO_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "PAXGUSDT"}
DISPLAY_NAMES = {
    "BTCUSDT": "BITCOIN", "ETHUSDT": "ETHEREUM", "SOLUSDT": "SOLANA",
    "PAXGUSDT": "GOLD · PAXG",
    "^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^FTSE": "FTSE 100",
    "^GDAXI": "DAX 40", "^N225": "NIKKEI 225",
    "EURUSD=X": "EUR / USD", "USDINR=X": "USD / INR", "SLV": "SILVER · SLV",
}
GRANS = ("1m", "5m", "15m", "1h", "4h", "1d")
CRYPTO_GRANS = ("1m", "5m", "15m", "1h", "4h", "1d")
YAHOO_GRANS = ("1d",)
MODEL_GRANS = ("1m", "5m", "1h", "4h", "1d")
POSITION_LEVELS = {-1: "SHORT", 0: "NEUTRAL", 1: "LONG"}
KNOWN_FEATURE_COUNTS = (44, 88, len(FEATURE_COLUMNS), 57)
START_BALANCE = 100_000.0
SPREAD = 0.0004
SLIPPAGE = 0.00005
# Execution research (Almgren-Chriss square-root impact, vol targeting):
#   cost = IMPACT_COEF * sigma_bar * sqrt(Q / ADV)          (sqrt impact)
#   size = min(1, VOL_TARGET_ANNUAL / sigma_annual)          (vol targeting)
#   floor: force-flat + block re-entry at MAX_DD_FLOOR        (prop risk rule)
IMPACT_COEF = 0.25
VOL_TARGET_ANNUAL = 0.15
VOL_TARGET_MIN_SIZE = 0.1
MAX_DD_FLOOR = 0.10

app = FastAPI(title="TradingView-style prediction")

# ---------------------------------------------------------------- registry
def _load_prod_registry():
    path = MODELS / "prod" / "registry.json"
    if not path.exists():
        return {}
    try:
        reg = json.loads(path.read_text())
    except Exception:
        return {}
    return {
        (r["symbol"], r["granularity"]): r
        for r in reg
        if r.get("symbol") and r.get("granularity")
    }


PROD_REG = _load_prod_registry()


# trend gate per granularity: |60-bar move| below this forces HOLD when flat —
# BUT only if the model's own confidence is weak. A confident pick (>=45%)
# passes even in quiet markets, so the UI shows real signals instead of
# perpetual HOLD. 1m/5m/1h/4h from the old flat 0.05 gate (5% over 60x5m
# bars is a rare move, which made every timeframe print HOLD).
GATES = {"1m": 0.002, "5m": 0.005, "1h": 0.01, "4h": 0.02, "1d": 0.05}
CONFIDENCE_FLOOR = 0.45


def models_for(symbol, gran):
    """Ordered candidate models: sweep winner first (OOS-selected), then prod."""
    out = []
    gate = GATES.get(gran, 0.01)
    reg = PROD_REG.get((symbol, gran), {})
    cross = reg.get("cross_assets") or []
    sweep = MODELS / "sweep" / f"{symbol}_{gran}.zip"
    if sweep.exists():
        out.append({"name": "sweep", "path": str(sweep), "gate": gate, "cross": []})
    prod = MODELS / "prod" / f"{symbol}_{gran}.zip"
    if prod.exists():
        out.append({
            "name": "prod", "path": str(prod), "gate": gate,
            "cross": [c for c in cross if c in SYMBOLS],
        })
    cdl = MODELS / "ppo_btc_cdl.zip"
    if (symbol, gran) == ("BTCUSDT", "5m") and cdl.exists():
        out.append({"name": "cdl", "path": str(cdl), "gate": gate, "cross": []})
    return out


# ---------------------------------------------------------------- data
_cache = DataCache()
_clients: dict[str, object] = {}
_refresh_lock = threading.Lock()
_refresh_key_locks: dict = {}
_refresh_key_locks_guard = threading.Lock()
_last_refresh = {}
_snapshot: dict = {}
_INTERVAL_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_HISTORY_DAYS = {"1m": 7, "5m": 7, "15m": 7, "1h": 7, "4h": 7, "1d": 4000}


def _client_for(symbol):
    if symbol not in _clients:
        if symbol in CRYPTO_SYMBOLS:
            _clients[symbol] = make_data_client()
        else:
            from bot.data.yahoo_client import YahooClient

            _clients[symbol] = YahooClient()
    return _clients[symbol]


def _key_lock(key):
    with _refresh_key_locks_guard:
        lk = _refresh_key_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _refresh_key_locks[key] = lk
        return lk


def _recent_data(symbol, gran, n=420, force=False):
    now = datetime.now(timezone.utc)
    key = (symbol, gran)
    interval = _INTERVAL_S.get(gran, 300)
    lk = _key_lock(key)
    with lk:
        last = _last_refresh.get(key, 0.0)
        # Yahoo daily pairs are polled by the frontend every 60s: never refetch
        # them more often than every 5 min (rate limits). force() still respects
        # the interval-based due time for everything else.
        min_gap = 300 if symbol not in CRYPTO_SYMBOLS else interval * 0.1
        due = (time.time() - last) > min_gap and (
            force or (time.time() - last) > interval * 0.8)
        if due:
            try:
                cov = _cache.coverage(symbol, gran)
                start = now - timedelta(days=_HISTORY_DAYS.get(gran, 7))
                if cov and cov[2]:
                    last = pd.Timestamp(cov[1])
                    start = max(start, last - timedelta(
                        seconds=_INTERVAL_S.get(gran, 300)))
                _cache.ensure_range(
                    _client_for(symbol), symbol, gran, start, now,
                )
                _last_refresh[key] = time.time()
                for k in [k for k in _snapshot if k[0] == key]:
                    del _snapshot[k]
            except Exception:
                pass
        snap_key = (key, n)
        df = _snapshot.get(snap_key)
        if df is None:
            df = _cache.load(symbol, gran, limit=n)
            df.attrs["symbol"] = symbol
            df.attrs["gran"] = gran
            _snapshot[snap_key] = df
    return df


def _cross_dfs(symbol, gran, crosses):
    dfs = {}
    for c in crosses:
        try:
            dfs[c] = _recent_data(c, gran, n=420)
        except Exception:
            dfs[c] = None
    return {k: v for k, v in dfs.items() if v is not None and not v.empty}


# ---------------------------------------------------------------- model
class PredictionModel:
    def __init__(self, path, gate):
        from stable_baselines3 import PPO

        self.model = PPO.load(path, device="cpu")
        self.gate = gate
        obs_dim = int(self.model.observation_space.shape[0])
        base = obs_dim - 3
        matches = [
            n for n in KNOWN_FEATURE_COUNTS
            if base % n == 0 and base // n > 0
        ]
        if matches:
            self.n_feat = min(matches, key=lambda n: abs(base // n - 60))
        else:
            self.n_feat = len(FEATURE_COLUMNS)
        self.window = base // self.n_feat
        self.path = str(path)

    def predict(self, df, cross_dfs, position=0.0, equity_ratio=1.0, pnl=0.0):
        import torch
        from stable_baselines3.common.utils import obs_as_tensor

        feats = normalized_frame(df, cross_asset_dfs=cross_dfs)
        if self.n_feat != len(FEATURE_COLUMNS):
            feats = feats.iloc[:, : self.n_feat]
        arr = feats.replace([np.inf, -np.inf], 0.0).fillna(0.0) \
            .to_numpy(dtype=np.float32)
        if len(arr) < self.window + 1:
            raise ValueError("not enough bars")
        i = len(arr) - 1
        window_feat = arr[i - self.window:i].reshape(-1)
        account = np.array([equity_ratio, position, pnl], dtype=np.float32)
        obs = np.concatenate([window_feat, account]).astype(np.float32)
        with torch.no_grad():
            obs_t = obs_as_tensor(obs, self.model.policy.device)
            if obs_t.ndim == 1:
                obs_t = obs_t.unsqueeze(0)
            dist = self.model.policy.get_distribution(obs_t)
            probs = dist.distribution.probs
            action, _ = self.model.predict(obs, deterministic=True)
            confidence = float(probs[0, int(action)].cpu().numpy())
        action_idx = int(action)
        signal = float([-1.0, 0.0, 1.0][action_idx])  # POSITION_LEVELS layout
        label = ["SHORT", "NEUTRAL", "LONG"][action_idx]
        if not np.isfinite(confidence):
            confidence = 0.0
            signal = 0.0
        trend = float(
            df["close"].iloc[-1] / df["close"].iloc[-1 - self.window] - 1
        )
        raw_label = label
        if self.gate > 0.0 and position == 0.0 and signal != 0.0 \
                and abs(trend) < self.gate and confidence < CONFIDENCE_FLOOR:
            signal = 0.0
            label = "NEUTRAL"
        return {"signal": signal, "label": label, "confidence": confidence,
                "trend": trend, "bias": raw_label}


_model_lock = threading.Lock()
_model_cache: dict[str, PredictionModel] = {}
_MODEL_CACHE_MAX = 4  # keep at most 4 models in memory (each ~0.5 GB unpacked)


def get_model(symbol, gran, model_name):
    found = models_for(symbol, gran)
    if not found:
        return None
    chosen = next((m for m in found if m["name"] == model_name), found[0])
    key = chosen["path"]
    print(f"[warmup/normal] get_model {symbol} {gran} {chosen['name']}",
          flush=True)
    with _model_lock:
        model = _model_cache.get(key)
        if model is None:
            print(f"  loading model {key}", flush=True)
            model = PredictionModel(chosen["path"], chosen["gate"])
            _model_cache[key] = model
            while len(_model_cache) > _MODEL_CACHE_MAX:
                _model_cache.pop(next(iter(_model_cache)))
            print("  model ready", flush=True)
    return model, chosen


# ---------------------------------------------------------------- sim
_accounts = {}
_state_lock = threading.RLock()


def _account(symbol, gran):
    key = (symbol, gran)
    with _state_lock:
        acct = _accounts.get(key)
        if acct is None:
            acct = {
                "balance": START_BALANCE, "position": 0.0, "mark": None,
                "spread": SPREAD, "slippage": SLIPPAGE,
                "peak": START_BALANCE, "dd_locked": False,
                "curve": deque(maxlen=240),
            }
            _accounts[key] = acct
        return acct


def _tick(symbol, gran, close):
    acct = _account(symbol, gran)
    mark = acct["mark"]
    if mark is not None and acct["position"] != 0.0:
        ret = close / mark - 1
        acct["balance"] *= 1 + ret * acct["position"]
    acct["mark"] = close
    # Max drawdown floor (prop rule): force-flat and lock re-entry while
    # equity sits below (1 - MAX_DD_FLOOR) * peak; unlock on partial recovery.
    if MAX_DD_FLOOR > 0.0:
        acct["peak"] = max(acct["peak"], acct["balance"])
        floor = acct["peak"] * (1 - MAX_DD_FLOOR)
        if acct["dd_locked"]:
            if acct["balance"] >= floor + 0.5 * acct["peak"] * MAX_DD_FLOOR:
                acct["dd_locked"] = False
        elif acct["balance"] < floor:
            acct["dd_locked"] = True
        if acct["dd_locked"] and acct["position"] != 0.0:
            acct["position"] = 0.0
            acct["mark"] = close
    acct["curve"].append({"t": time.time(), "e": acct["balance"]})
    return acct


def _vol_ratio(df):
    """Vol-target sizing ratio = min(1, target_vol / forecast_vol).

    Forecast vol = per-bar sigma from recent log returns, annualized. No
    lookahead (uses only bars up to the current one).
    """
    closes = df["close"].to_numpy(dtype=np.float64)
    if len(closes) < 30:
        return 1.0
    lr = np.log(closes[1:] / closes[:-1])
    sigma_bar = float(lr[-min(20, len(lr)):].std())
    if not np.isfinite(sigma_bar) or sigma_bar <= 0.0:
        return 1.0
    # bars per year inferred from median spacing
    diff = df.index.to_series().diff().dropna()
    if diff.empty:
        return 1.0
    med = float(diff.median().total_seconds())
    ppy = 365.0 * 86400.0 / med if med > 0 else 105120
    sigma_ann = sigma_bar * np.sqrt(ppy)
    ratio = VOL_TARGET_ANNUAL / sigma_ann if sigma_ann > 0 else 1.0
    return float(np.clip(ratio, VOL_TARGET_MIN_SIZE, 1.0))


def _regime_factor(df):
    """HMM regime overlay: cut size in high-volatility regimes, full size in
    calm/trending ones. Fit cached per (symbol, gran) on cached history; the
    per-bar filter is causal (no lookahead)."""
    try:
        key = (str(df.attrs.get("symbol", "")), str(df.attrs.get("gran", "")))
    except Exception:
        key = ("", "")
    if key not in _regime_cache:
        return 1.0
    try:
        return float(_regime_cache[key].regime(df)["size_factor"])
    except Exception:
        return 1.0


_regime_cache = {}


def _warm_regimes():
    """Fit HMM regime models for all prod pairs in a background thread."""
    def _burn():
        import threading as _t

        def _one(symbol, gran):
            try:
                df = _recent_data(symbol, gran, n=8000, force=True)
                if df.empty or len(df) < 500:
                    return
                df.attrs["symbol"] = symbol
                df.attrs["gran"] = gran
                hmm = RegimeHMM().fit(df)
                _regime_cache[(symbol, gran)] = hmm
                print(f"[regime] {symbol} {gran} fitted "
                      f"({hmm.regime(df)['label']})", flush=True)
            except Exception as exc:
                print(f"[regime] {symbol} {gran} failed: {exc}", flush=True)

        threads = []
        for s in SYMBOLS:
            for g in (CRYPTO_GRANS if s in CRYPTO_SYMBOLS else YAHOO_GRANS):
                if g not in MODEL_GRANS:
                    continue
                threads.append(_t.Thread(target=_one, args=(s, g), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    threading.Thread(target=_burn, daemon=True).start()


def _apply_signal(acct, signal, close, df=None):
    target = float(signal)
    if target != 0.0:
        target *= _vol_ratio(df) if df is not None else 1.0
        target *= _regime_factor(df) if df is not None else 1.0
    if acct["dd_locked"] and target != 0.0:
        target = 0.0
    delta = abs(target - acct["position"])
    if delta > 1e-6:
        cost = (acct["spread"] / 2 + acct["slippage"]) * delta
        # Square-root market impact: coef * sigma_bar * sqrt(Q/ADV)
        if df is not None and "volume" in df.columns and IMPACT_COEF > 0.0:
            closes = df["close"].to_numpy(dtype=np.float64)
            vols = df["volume"].to_numpy(dtype=np.float64)
            if len(closes) > 30 and vols[vols > 0].size:
                lr = np.log(closes[1:] / closes[:-1])
                sigma_bar = float(lr[-min(20, len(lr)):].std())
                if np.isfinite(sigma_bar) and sigma_bar > 0.0:
                    adv = float(np.mean(vols[-48:] * closes[-48:]))
                    q = delta * acct["balance"]
                    if adv > 0.0:
                        impact = IMPACT_COEF * sigma_bar * np.sqrt(q / adv)
                        cost += float(min(impact, 0.02))
        acct["balance"] *= 1 - cost
        acct["position"] = target
    drawdown = acct["balance"] / acct["peak"] - 1 if acct["peak"] > 0 else 0.0
    return {
        "balance": round(acct["balance"], 2),
        "position": acct["position"],
        "position_label": POSITION_LEVELS.get(int(acct["position"]), "NEUTRAL"),
        "pnl": round(acct["balance"] - START_BALANCE, 2),
        "return": round(acct["balance"] / START_BALANCE - 1, 5),
        "drawdown": round(drawdown, 5),
        "dd_locked": acct["dd_locked"],
        "curve": [{"t": p["t"], "e": round(p["e"], 2)} for p in acct["curve"]],
    }


# ---------------------------------------------------------------- routes
@app.get("/api/models")
def api_models():
    available = {}
    for s in SYMBOLS:
        grans = CRYPTO_GRANS if s in CRYPTO_SYMBOLS else YAHOO_GRANS
        for g in grans:
            if g in MODEL_GRANS:
                ms = models_for(s, g)
                if ms:
                    available[f"{s}|{g}"] = [m["name"] for m in ms]
    return {
        "symbols": [
            {"symbol": s, "label": DISPLAY_NAMES.get(s, s), "crypto": s in CRYPTO_SYMBOLS}
            for s in SYMBOLS
        ],
        "granularities": list(GRANS),
        "crypto_grans": list(CRYPTO_GRANS),
        "available": available,
    }


@app.get("/api/history")
def api_history(symbol: str = "BTCUSDT", gran: str = "5m", n: int = 420):
    df = _recent_data(symbol, gran, n=n, force=True)
    if df.empty:
        return JSONResponse({"error": "no data"}, status_code=404)
    rows = [
        {
            "time": int(t.timestamp()),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
        }
        for t, r in df.iterrows()
    ]
    return {"symbol": symbol, "gran": gran, "bars": rows}


@app.post("/api/predict")
async def api_predict(request: Request):
    body = await request.json()
    symbol = str(body.get("symbol", "BTCUSDT")).upper()
    gran = str(body.get("gran", "5m")).lower()
    model_name = str(body.get("model", "")) or None

    if symbol not in SYMBOLS or gran not in MODEL_GRANS:
        return JSONResponse({"error": f"no model for {symbol} {gran}"}, status_code=404)
    ok_grans = CRYPTO_GRANS if symbol in CRYPTO_SYMBOLS else YAHOO_GRANS
    if gran not in ok_grans:
        return JSONResponse({"error": f"no {gran} data for {symbol}"}, status_code=404)
    hit = get_model(symbol, gran, model_name)
    if hit is None:
        return JSONResponse({"error": "no model files"}, status_code=404)
    model, chosen = hit
    print(f"[api] predict {symbol} {gran} {chosen['name']}", flush=True)
    df = _recent_data(symbol, gran, n=420)
    print(f"  data ok rows={len(df)}", flush=True)
    if len(df) < model.window + 2:
        return JSONResponse({"error": "not enough bars"}, status_code=422)
    crosses = _cross_dfs(symbol, gran, chosen["cross"])
    with _state_lock:
        ticked = _tick(symbol, gran, float(df["close"].iloc[-1]))
        print("  feats+sim ok", flush=True)
        pred = model.predict(
            df, crosses,
            position=ticked["position"],
            equity_ratio=ticked["balance"] / START_BALANCE,
            pnl=0.0,
        )
        print(f"  predict ok {pred['label']}", flush=True)
        acct_view = _apply_signal(ticked, pred["signal"], float(df["close"].iloc[-1]), df)

    return {
        "symbol": symbol, "gran": gran, "model": chosen["name"],
        "price": float(df["close"].iloc[-1]),
        "bar_time": int(df.index[-1].timestamp()),
        "signal": pred["signal"], "label": pred["label"],
        "bias": pred.get("bias", "NEUTRAL"),
        "confidence": round(pred["confidence"], 4), "trend": round(pred["trend"], 6),
        "vol_target": round(_vol_ratio(df), 4),
        "regime": _regime_view(symbol, gran, df),
        "account": acct_view,
    }


def _regime_view(symbol, gran, df):
    hmm = _regime_cache.get((symbol, gran))
    if hmm is None:
        return {"label": "CALM", "size_factor": 1.0, "probs": {}, "fitted": False}
    try:
        r = hmm.regime(df)
        return {**r, "fitted": True}
    except Exception:
        return {"label": "CALM", "size_factor": 1.0, "probs": {}, "fitted": False}


@app.post("/api/reset")
async def api_reset(request: Request):
    body = await request.json()
    symbol = str(body.get("symbol", "BTCUSDT")).upper()
    gran = str(body.get("gran", "5m")).lower()
    with _state_lock:
        _accounts.pop((symbol, gran), None)
    return {"ok": True}


@app.get("/api/health")
def api_health():
    with _state_lock:
        accounts = {f"{s}|{g}": {
            "balance": round(a["balance"], 2),
            "position": a["position"],
            "dd_locked": a["dd_locked"],
        } for (s, g), a in _accounts.items()}
    return {
        "ok": True,
        "time": int(time.time()),
        "models_cached": len(_model_cache),
        "regimes_fitted": len(_regime_cache),
        "accounts": accounts,
    }


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

@app.on_event("startup")
def _startup_warmup():
    # Preload every (symbol, 1m/5m/1h/4h) prod model in the background so the
    # first prediction on any pair is instant. Runs detached; never blocks serving.
    def _burn():
        # Memory-constrained host: prewarm ONLY the primary pair (BTC 5m prod)
        # so the default view is instant. Other pairs lazy-load on first request.
        try:
            get_model("BTCUSDT", "5m", "prod")
        except Exception as exc:
            print(f"[warmup] BTCUSDT 5m failed: {exc}", flush=True)
        _warm_regimes()
    threading.Thread(target=_burn, daemon=True).start()


def _warmup_pair(symbol, gran):
    threading.Thread(target=lambda: get_model(symbol, gran, None), daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
