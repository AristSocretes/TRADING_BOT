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
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.data.cache import DataCache  # noqa: E402
from bot.data.clients import make_data_client  # noqa: E402
from bot.data.features import FEATURE_COLUMNS, normalized_frame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
WEB = ROOT / "web"

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
GRANS = ("1m", "5m", "15m", "1h", "4h")
MODEL_GRANS = ("1m", "5m", "1h", "4h")
POSITION_LEVELS = {-1: "SHORT", 0: "NEUTRAL", 1: "LONG"}
KNOWN_FEATURE_COUNTS = (44, len(FEATURE_COLUMNS), 57)
START_BALANCE = 100_000.0
SPREAD = 0.0004
SLIPPAGE = 0.00005

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


def models_for(symbol, gran):
    """Ordered candidate models: sweep winner first (OOS-selected), then prod."""
    out = []
    gate = 0.05 if gran != "1m" else 0.005
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
_client = make_data_client()
_refresh_lock = threading.Lock()
_refresh_key_locks: dict = {}
_refresh_key_locks_guard = threading.Lock()
_last_refresh = {}
_snapshot: dict = {}
_INTERVAL_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


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
    due = force or (time.time() - _last_refresh.get(key, 0.0)) > interval * 0.8
    lk = _key_lock(key)
    with lk:
        last = _last_refresh.get(key, 0.0)
        due = force or (time.time() - last) > interval * 0.8
        if due:
            try:
                _cache.ensure_range(
                    _client, symbol, gran,
                    now - timedelta(days=1), now,
                )
                _last_refresh[key] = time.time()
            except Exception:
                pass
        df = _snapshot.get(key)
        if df is None:
            df = _cache.load(symbol, gran, limit=n)
            _snapshot[key] = df
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
        if self.gate > 0.0 and position == 0.0 and signal != 0.0 \
                and abs(trend) < self.gate:
            signal = 0.0
        return {"signal": signal, "label": label, "confidence": confidence, "trend": trend}


_model_lock = threading.Lock()
_model_cache: dict[str, PredictionModel] = {}


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
    acct["curve"].append({"t": time.time(), "e": acct["balance"]})
    return acct


def _apply_signal(acct, signal, close):
    target = float(signal)
    delta = abs(target - acct["position"])
    if delta > 1e-6:
        cost = (acct["spread"] / 2 + acct["slippage"]) * delta
        acct["balance"] *= 1 - cost
        acct["position"] = target
    return {
        "balance": round(acct["balance"], 2),
        "position": acct["position"],
        "position_label": POSITION_LEVELS.get(int(acct["position"]), "NEUTRAL"),
        "pnl": round(acct["balance"] - START_BALANCE, 2),
        "return": round(acct["balance"] / START_BALANCE - 1, 5),
        "curve": [{"t": p["t"], "e": round(p["e"], 2)} for p in acct["curve"]],
    }


# ---------------------------------------------------------------- routes
@app.get("/api/models")
def api_models():
    available = {}
    for s in SYMBOLS:
        for g in MODEL_GRANS:
            ms = models_for(s, g)
            if ms:
                available[f"{s}|{g}"] = [m["name"] for m in ms]
    return {"symbols": list(SYMBOLS), "granularities": list(GRANS),
            "available": available}


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

    if (symbol, gran) not in [ (s, g) for s in SYMBOLS for g in MODEL_GRANS ]:
        return JSONResponse({"error": f"no model for {symbol} {gran}"}, status_code=404)
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
        acct_view = _apply_signal(ticked, pred["signal"], float(df["close"].iloc[-1]))

    return {
        "symbol": symbol, "gran": gran, "model": chosen["name"],
        "price": float(df["close"].iloc[-1]),
        "bar_time": int(df.index[-1].timestamp()),
        "signal": pred["signal"], "label": pred["label"],
        "confidence": round(pred["confidence"], 4), "trend": round(pred["trend"], 6),
        "account": acct_view,
    }


@app.post("/api/reset")
async def api_reset(request: Request):
    body = await request.json()
    symbol = str(body.get("symbol", "BTCUSDT")).upper()
    gran = str(body.get("gran", "5m")).lower()
    with _state_lock:
        _accounts.pop((symbol, gran), None)
    return {"ok": True}


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
    threading.Thread(target=_burn, daemon=True).start()


def _warmup_pair(symbol, gran):
    threading.Thread(target=lambda: get_model(symbol, gran, None), daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
