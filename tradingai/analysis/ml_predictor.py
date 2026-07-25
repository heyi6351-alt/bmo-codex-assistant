"""Optional ML direction predictor (extra analyst vote).

Trains a gradient-boosted classifier (XGBoost if installed, else scikit-learn's
HistGradientBoosting) to predict whether price will be higher ``horizon`` bars
ahead, using the same technical indicators the rule engine computes. Validated
with a walk-forward (TimeSeriesSplit) accuracy estimate so we never claim more
than the data supports. Results are cached per (symbol, interval) for a while.

Gracefully disables itself if scikit-learn isn't installed — the rest of the
bot is unaffected.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from data.market import MARKET

from .indicators import adx, atr, bollinger, ema, macd, obv, proc, rsi, stochastic, to_dataframe

log = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL = 1200          # seconds; predictions are cached this long
_MODEL_NAME = ""


def available() -> bool:
    try:
        import sklearn  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _make_model():
    global _MODEL_NAME
    try:
        from xgboost import XGBClassifier
        _MODEL_NAME = "XGBoost"
        return XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                             subsample=0.9, colsample_bytree=0.9,
                             eval_metric="logloss", n_jobs=2)
    except Exception:  # noqa: BLE001
        from sklearn.ensemble import HistGradientBoostingClassifier
        _MODEL_NAME = "HistGradientBoosting"
        return HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300)


def _features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    f = pd.DataFrame(index=df.index)
    f["rsi"] = rsi(close)
    _, _, hist = macd(close)
    f["macd_hist"] = hist
    f["adx"] = adx(df)
    pk, pdl = stochastic(df)
    f["stoch_k"] = pk
    f["stoch_kd"] = pk - pdl
    f["proc"] = proc(close)
    f["ema_ratio"] = ema(close, 20) / ema(close, 50) - 1
    f["atr_pct"] = atr(df, 14) / close.replace(0, np.nan)
    bb_u, bb_m, bb_l = bollinger(close)
    width = (bb_u - bb_l).replace(0, np.nan)
    f["bb_pos"] = ((close - bb_m) / width).fillna(0.0)
    f["ret1"] = close.pct_change()
    f["ret3"] = close.pct_change(3)
    f["ret5"] = close.pct_change(5)
    f["obv_slope"] = obv(close, df["volume"]).diff(5)
    # ── richer FreqAI-style features (2026-07-08): trend momentum, volume expansion,
    # position-in-range, and the RATE-OF-CHANGE of momentum/trend (2nd-order signals). ──
    f["ema_slope"] = ema(close, 20).pct_change(3)
    vol = df["volume"]
    f["vol_ratio"] = (vol / vol.rolling(20).mean().replace(0, np.nan)).fillna(1.0)
    f["rsi_chg"] = rsi(close).diff(3)
    f["adx_chg"] = adx(df).diff(3)
    hi20, lo20 = df["high"].rolling(20).max(), df["low"].rolling(20).min()
    f["range_pos"] = ((close - lo20) / (hi20 - lo20).replace(0, np.nan)).fillna(0.5)
    return f


def _train_predict(symbol: str, interval: str, horizon: int, lookback: int) -> dict:
    candles = MARKET.get_candles(symbol, interval, outputsize=lookback)
    if len(candles) < 200:
        return {"available": True, "direction": "", "prob_up": -1.0,
                "note": f"Not enough history for ML ({len(candles)} bars)."}

    df = to_dataframe(candles)
    feats = _features(df)
    cols = list(feats.columns)
    close = df["close"]
    y = (close.shift(-horizon) > close).astype(int)

    data = feats.assign(_y=y).replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < 150 or data["_y"].nunique() < 2:
        return {"available": True, "direction": "", "prob_up": -1.0,
                "note": "Not enough clean samples for ML."}

    X = data[cols].values
    Y = data["_y"].values.astype(int)

    try:
        from sklearn.metrics import accuracy_score
        from sklearn.model_selection import TimeSeriesSplit
        model = _make_model()
        accs = []
        for tr, te in TimeSeriesSplit(n_splits=4).split(X):
            model.fit(X[tr], Y[tr])
            accs.append(accuracy_score(Y[te], model.predict(X[te])))
        cv = float(np.mean(accs))
        model.fit(X, Y)
    except Exception as e:  # noqa: BLE001
        log.warning("ML training failed: %s", e)
        return {"available": True, "direction": "", "prob_up": -1.0, "note": f"ML error: {e}"}

    latest = feats[cols].iloc[[-1]].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    classes = list(model.classes_)
    proba = model.predict_proba(latest)[0]
    prob_up = float(proba[classes.index(1)]) if 1 in classes else 0.5
    direction = "long" if prob_up >= 0.5 else "short"
    return {
        "available": True,
        "direction": direction,
        "prob_up": round(prob_up, 3),
        "confidence": round(abs(prob_up - 0.5) * 2, 2),
        "cv_accuracy": round(cv, 3),
        "model": _MODEL_NAME,
        "horizon": horizon,
        "note": "",
    }


def predict(symbol: str, interval: str = "1h", horizon: int = 5, lookback: int = 600) -> dict:
    """Predict short-horizon direction. Cached per (symbol, interval, horizon)."""
    if not available():
        return {"available": False, "note": "ML disabled — install scikit-learn (+ optionally xgboost)."}
    key = (symbol, interval, horizon)
    cached = _CACHE.get(key)
    if cached and (time.time() - cached["ts"]) < _TTL:
        return cached["result"]
    try:
        result = _train_predict(symbol, interval, horizon, lookback)
    except Exception as e:  # noqa: BLE001
        log.warning("ML predict failed: %s", e)
        result = {"available": True, "direction": "", "prob_up": -1.0, "note": f"ML error: {e}"}
    _CACHE[key] = {"ts": time.time(), "result": result}
    return result
