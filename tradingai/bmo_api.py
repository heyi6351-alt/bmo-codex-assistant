"""BMO signal API ！ ARM-safe HTTP wrapper around the deterministic signal engine.

Exposes the trading bot's PURE-PYTHON analysis (analysis/signals.py) over HTTP so
BMO and other agents on the hackathon device can query trade signals without any
MetaTrader5 / Windows dependency. Candles come from the cached OHLC CSVs in
storage/ (offline, deterministic) or ！ if ?live=1 and yfinance is installed ！ from
a live download.

    GET /                     -> service info + endpoint list
    GET /health               -> {"ok": true, ...}
    GET /symbols              -> tradeable symbols discovered from storage/*.csv
    GET /signal?symbol=XAUUSD&profile=aggressive&tf=15min[&mode=pullback][&live=1]
    GET /analysis?symbol=XAUUSD&tf=1h   -> indicator snapshot + signal + confluence
    GET /candles?symbol=XAUUSD&tf=15min&n=48 -> raw OHLC candles (oldest->newest)

Bind: 0.0.0.0:8100   (run:  python bmo_api.py)
NOTE: analysis / paper only. No broker credentials, no live orders.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request

from analysis.indicators import compute_indicators, to_dataframe
from analysis.signals import confluence, generate_signal
from core.models import Candle
from risk.profiles import PROFILES, get_profile

ROOT = Path(__file__).resolve().parent
STORAGE = ROOT / "storage"

# M5 base CSVs -> pandas resample rule for higher timeframes.
_RESAMPLE = {"5min": None, "15min": "15min", "30min": "30min",
             "1h": "1h", "4h": "4h", "1day": "1D"}

app = Flask(__name__)


def _discover() -> dict[str, Path]:
    """Map SYMBOL -> newest cached M5 csv. Gold (XAUUSD) comes from the duka_* set."""
    out: dict[str, Path] = {}
    inst = sorted(glob.glob(str(STORAGE / "inst_*_m5.csv")))
    for p in inst:
        sym = Path(p).stem.replace("inst_", "").replace("_m5", "").upper()
        out[sym] = Path(p)
    # Gold: prefer a full duka regime file, else the test week.
    duka = sorted(glob.glob(str(STORAGE / "duka_*_m5.csv")))
    duka = [d for d in duka if "test_week" not in d] or duka
    if duka:
        out.setdefault("XAUUSD", Path(duka[-1]))
        out.setdefault("GOLD", Path(duka[-1]))
    return out


SYMBOLS = _discover()


def _load_candles(symbol: str, tf: str, limit: int, live: bool) -> tuple[list[Candle], str]:
    """Return (candles, source). CSV base is M5; resample up when tf != 5min."""
    symbol = symbol.upper().replace("/", "")
    if live:
        try:
            from data.market import MARKET  # noqa: WPS433 ！ optional live path
            sig_sym = symbol if len(symbol) <= 4 else f"{symbol[:3]}/{symbol[3:]}"
            candles = MARKET.get_candles(sig_sym, tf if tf != "5min" else "5min", outputsize=limit)
            if candles:
                return candles, "live:market.py"
        except Exception as e:  # noqa: BLE001 ！ fall back to cache
            app.logger.warning("live fetch failed (%s); using cache", e)

    path = SYMBOLS.get(symbol)
    if not path:
        raise KeyError(symbol)
    df = pd.read_csv(path)
    tcol = df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df = df.dropna(subset=[tcol]).set_index(tcol)
    rule = _RESAMPLE.get(tf)
    if rule:
        df = df.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                    "close": "last", "volume": "sum"}).dropna()
    df = df.tail(limit)
    candles = [Candle(dt=str(idx), open=float(r["open"]), high=float(r["high"]),
                      low=float(r["low"]), close=float(r["close"]),
                      volume=float(r.get("volume", 0) or 0))
               for idx, r in df.iterrows()]
    return candles, f"cache:{path.name}"


@app.get("/")
def index():
    return jsonify({
        "service": "bmo-trading-signal-api",
        "asset_class": "forex/metals (XAU/USD gold default)",
        "mode": "analysis/paper-only ！ no broker credentials, no live orders",
        "engine": "deterministic signal engine (analysis/signals.py)",
        "endpoints": {
            "/health": "liveness",
            "/symbols": "tradeable symbols from cached data",
            "/signal": "?symbol=XAUUSD&profile=aggressive&tf=15min[&mode=pullback][&live=1]",
            "/analysis": "?symbol=XAUUSD&tf=1h ！ indicators + signal + confluence",
            "/candles": "?symbol=XAUUSD&tf=15min&n=48 ！ raw OHLC candles (oldest->newest)",
        },
        "profiles": list(PROFILES),
        "timeframes": list(_RESAMPLE),
    })


@app.get("/health")
def health():
    return jsonify({"ok": True, "symbols": sorted(SYMBOLS), "storage": str(STORAGE)})


@app.get("/symbols")
def symbols():
    return jsonify({"symbols": sorted(SYMBOLS),
                    "files": {k: v.name for k, v in sorted(SYMBOLS.items())}})


def _signal_payload(symbol: str, profile_name: str, tf: str, mode: str | None,
                    live: bool, limit: int):
    candles, source = _load_candles(symbol, tf, limit, live)
    if len(candles) < 60:
        return {"error": "not enough candles", "have": len(candles)}, 422
    profile = get_profile(profile_name)
    if mode:
        from dataclasses import replace
        profile = replace(profile, strategy_mode=mode)
    sig = generate_signal(symbol.upper().replace("/", ""), candles, profile, interval=tf)
    d = sig.to_dict()
    # Indicators dataclass -> already dict via asdict; keep the payload lean + explicit.
    d["source"] = source
    d["profile"] = profile.name
    d["strategy_mode"] = profile.strategy_mode
    d["candles_used"] = len(candles)
    d["last_price"] = candles[-1].close
    d["last_bar"] = candles[-1].dt
    return d, 200


@app.get("/signal")
def signal():
    symbol = request.args.get("symbol", "XAUUSD")
    profile = request.args.get("profile", "aggressive")
    tf = request.args.get("tf", "15min")
    mode = request.args.get("mode")
    live = request.args.get("live", "0") in ("1", "true", "yes")
    limit = min(int(request.args.get("limit", "400")), 5000)
    if tf not in _RESAMPLE:
        return jsonify({"error": f"bad tf; use {list(_RESAMPLE)}"}), 400
    try:
        payload, code = _signal_payload(symbol, profile, tf, mode, live, limit)
    except KeyError:
        return jsonify({"error": f"unknown symbol '{symbol}'", "known": sorted(SYMBOLS)}), 404
    return jsonify(payload), code


@app.get("/candles")
def candles_ep():
    symbol = request.args.get("symbol", "XAUUSD")
    tf = request.args.get("tf", "15min")
    n = max(1, min(int(request.args.get("n", "48")), 64))
    if tf not in _RESAMPLE:
        return jsonify({"error": f"bad tf; use {list(_RESAMPLE)}"}), 400
    try:
        candles, _ = _load_candles(symbol, tf, n, live=False)
    except KeyError:
        return jsonify({"error": "unknown symbol"}), 404
    return jsonify({
        "symbol": symbol.upper().replace("/", ""),
        "tf": tf,
        "c": [{"t": c.dt, "o": c.open, "h": c.high, "l": c.low, "c": c.close}
              for c in candles],
    })


@app.get("/analysis")
def analysis():
    symbol = request.args.get("symbol", "XAUUSD")
    tf = request.args.get("tf", "1h")
    live = request.args.get("live", "0") in ("1", "true", "yes")
    limit = min(int(request.args.get("limit", "400")), 5000)
    if tf not in _RESAMPLE:
        return jsonify({"error": f"bad tf; use {list(_RESAMPLE)}"}), 400
    try:
        candles, source = _load_candles(symbol, tf, limit, live)
    except KeyError:
        return jsonify({"error": f"unknown symbol '{symbol}'", "known": sorted(SYMBOLS)}), 404
    if len(candles) < 60:
        return jsonify({"error": "not enough candles", "have": len(candles)}), 422
    from dataclasses import asdict
    df = to_dataframe(candles)
    ind = compute_indicators(df)
    sig = generate_signal(symbol.upper().replace("/", ""), candles,
                          get_profile("aggressive"), interval=tf)
    conf_score, conf_tags = confluence(df, ind, sig.direction)
    return jsonify({
        "symbol": symbol.upper().replace("/", ""),
        "timeframe": tf,
        "source": source,
        "last_price": candles[-1].close,
        "last_bar": candles[-1].dt,
        "indicators": asdict(ind),
        "signal": {"direction": sig.direction, "confidence": sig.confidence,
                   "entry": sig.entry, "stop_loss": sig.stop_loss,
                   "risk_reward": sig.risk_reward,
                   "take_profits": [asdict(tp) for tp in sig.take_profits],
                   "reason": sig.invalidation_reason},
        "confluence": {"score": conf_score, "tags": conf_tags},
    })


if __name__ == "__main__":
    port = int(os.getenv("BMO_API_PORT", "8100"))
    app.run(host="0.0.0.0", port=port, threaded=True)
