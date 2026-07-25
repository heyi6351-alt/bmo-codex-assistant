"""Technical indicators — pure pandas/numpy (no TA-Lib / pandas-ta dependency).

Computing these deterministically (instead of asking the LLM) is the key
anti-hallucination guard: the model reasons over numbers it cannot alter.
All functions use Wilder's smoothing where conventional (RSI, ATR, ADX).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.models import Candle, Indicators


def to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([{
        "datetime": c.dt, "open": c.open, "high": c.high,
        "low": c.low, "close": c.close, "volume": c.volume,
    } for c in candles])
    return df


def _safe(value, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if f == f else default   # NaN check
    except (TypeError, ValueError):
        return default


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0):
    mid = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std().fillna(0)
    return mid + mult * std, mid, mid - mult * std


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    tr_smooth = _true_range(df).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / tr_smooth.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3):
    low_min = df["low"].rolling(k, min_periods=1).min()
    high_max = df["high"].rolling(k, min_periods=1).max()
    denom = (high_max - low_min).replace(0, np.nan)
    pct_k = (100 * (df["close"] - low_min) / denom).fillna(50.0)
    pct_d = pct_k.rolling(d, min_periods=1).mean()
    return pct_k, pct_d


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def adl(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    mfv = mfm.fillna(0.0) * df["volume"].fillna(0.0)
    return mfv.cumsum()


def proc(close: pd.Series, period: int = 12) -> pd.Series:
    return (close.diff(period) / close.shift(period).replace(0, np.nan) * 100).fillna(0.0)


def _slope(series: pd.Series, n: int = 10) -> float:
    if len(series) < 2:
        return 0.0
    tail = series.tail(n)
    return _safe(tail.iloc[-1] - tail.iloc[0])


def support_resistance(df: pd.DataFrame, lookback: int = 60, span: int = 3):
    """Nearest swing support below / resistance above the current close."""
    highs, lows = df["high"], df["low"]
    price = float(df["close"].iloc[-1])
    res_levels, sup_levels = [], []
    n = len(df)
    for i in range(span, n - span):
        window_h = highs.iloc[i - span:i + span + 1]
        window_l = lows.iloc[i - span:i + span + 1]
        if highs.iloc[i] == window_h.max():
            res_levels.append(float(highs.iloc[i]))
        if lows.iloc[i] == window_l.min():
            sup_levels.append(float(lows.iloc[i]))
    res = min([r for r in res_levels if r > price], default=float(highs.tail(lookback).max()))
    sup = max([s for s in sup_levels if s < price], default=float(lows.tail(lookback).min()))
    return sup, res


def compute_indicators(df: pd.DataFrame) -> Indicators:
    """Latest indicator snapshot from an OHLC dataframe."""
    close = df["close"]
    ema_fast = ema(close, 20)
    ema_slow = ema(close, 50)
    ema_200 = ema(close, 200)
    rsi14 = rsi(close, 14)
    macd_line, signal_line, hist = macd(close)
    bb_u, _bb_m, bb_l = bollinger(close)
    atr14 = atr(df, 14)
    adx14 = adx(df, 14)
    sup, res = support_resistance(df)
    pct_k, pct_d = stochastic(df)
    obv_s = obv(close, df["volume"])
    adl_s = adl(df)
    proc_s = proc(close)

    return Indicators(
        ema_fast=_safe(ema_fast.iloc[-1]),
        ema_slow=_safe(ema_slow.iloc[-1]),
        ema200=_safe(ema_200.iloc[-1]),
        rsi14=_safe(rsi14.iloc[-1], 50.0),
        macd=_safe(macd_line.iloc[-1]),
        macd_signal=_safe(signal_line.iloc[-1]),
        macd_hist=_safe(hist.iloc[-1]),
        adx14=_safe(adx14.iloc[-1]),
        atr14=_safe(atr14.iloc[-1]),
        bb_upper=_safe(bb_u.iloc[-1]),
        bb_lower=_safe(bb_l.iloc[-1]),
        support=_safe(sup),
        resistance=_safe(res),
        stoch_k=_safe(pct_k.iloc[-1], 50.0),
        stoch_d=_safe(pct_d.iloc[-1], 50.0),
        proc=_safe(proc_s.iloc[-1]),
        obv=_safe(obv_s.iloc[-1]),
        obv_slope=_slope(obv_s),
        adl=_safe(adl_s.iloc[-1]),
        adl_slope=_slope(adl_s),
    )
