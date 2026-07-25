"""Property + known-value tests for the technical indicators that feed EVERY signal and the
ATR-based 2% position sizing. If ATR is wrong, sizing is wrong; if RSI/ADX escape [0,100],
downstream gates misfire. Pure pandas — no MT5, no network."""
import numpy as np
import pandas as pd
import pytest

from analysis.indicators import (ema, rsi, atr, adx, bollinger, macd,
                                  stochastic, _true_range, to_dataframe)
from core.models import Candle


def _ohlc(closes, spread=1.0):
    """Build a DataFrame from a close series with a constant high-low range = `spread`."""
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"high": c + spread / 2, "low": c - spread / 2, "close": c,
                         "open": c.shift(1).fillna(c.iloc[0])})


# ── EMA ────────────────────────────────────────────────────────────────────
def test_ema_of_constant_is_constant():
    s = pd.Series([5.0] * 50)
    assert ema(s, 10).iloc[-1] == pytest.approx(5.0)


def test_ema_lags_a_rising_series():
    s = pd.Series(np.arange(100, dtype=float))
    e = ema(s, 10)
    assert e.iloc[-1] < s.iloc[-1]           # EMA lags the latest value
    assert e.is_monotonic_increasing         # but still rises with the trend


# ── RSI ────────────────────────────────────────────────────────────────────
def test_rsi_bounded_0_100():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 500)))
    r = rsi(s)
    assert r.min() >= 0.0 and r.max() <= 100.0


def test_rsi_uptrend_gt_downtrend():
    up = pd.Series(100 + np.cumsum(np.abs(np.sin(np.arange(200))) + 0.5))   # mostly gains
    down = pd.Series(300 - np.cumsum(np.abs(np.sin(np.arange(200))) + 0.5))  # mostly losses
    assert rsi(up).iloc[-1] > rsi(down).iloc[-1]


# ── ATR (feeds position sizing — must never be negative) ─────────────────────
def test_atr_never_negative():
    rng = np.random.default_rng(1)
    df = _ohlc(100 + np.cumsum(rng.normal(0, 1, 300)), spread=2.0)
    assert (atr(df) >= 0).all()


def test_atr_converges_to_constant_range():
    # flat close, constant 3.0 high-low range, no gaps → TR=3 every bar → ATR→3
    df = _ohlc([100.0] * 200, spread=3.0)
    assert atr(df, 14).iloc[-1] == pytest.approx(3.0, abs=0.05)


def test_true_range_is_max_of_the_three_components():
    df = pd.DataFrame({"high": [10, 12], "low": [8, 9], "close": [9, 11],
                       "open": [9, 9]})
    # bar 2: hl=3, |h-prevc|=|12-9|=3, |l-prevc|=|9-9|=0 → TR=3
    assert _true_range(df).iloc[1] == pytest.approx(3.0)


# ── ADX ────────────────────────────────────────────────────────────────────
def test_adx_bounded_and_finite():
    rng = np.random.default_rng(2)
    df = _ohlc(100 + np.cumsum(rng.normal(0, 1, 400)), spread=1.5)
    a = adx(df)
    assert a.notna().all() and (a >= 0).all() and (a <= 100).all()


def test_adx_higher_in_a_clean_trend_than_in_chop():
    trend = _ohlc(np.arange(300, dtype=float) * 0.5 + 100, spread=1.0)   # steady uptrend
    chop = _ohlc(100 + 2 * np.sin(np.arange(300) / 2.0), spread=1.0)     # oscillation
    assert adx(trend).iloc[-1] > adx(chop).iloc[-1]


# ── Bollinger ────────────────────────────────────────────────────────────────
def test_bollinger_ordering_and_zero_width_on_constant():
    s = pd.Series([50.0] * 40)
    up, mid, lo = bollinger(s, 20, 2.0)
    assert up.iloc[-1] == pytest.approx(mid.iloc[-1]) == pytest.approx(lo.iloc[-1])  # std 0
    s2 = pd.Series(100 + np.random.default_rng(3).normal(0, 5, 100))
    up2, mid2, lo2 = bollinger(s2, 20, 2.0)
    assert (up2 >= mid2).all() and (mid2 >= lo2).all()


# ── MACD / Stochastic ────────────────────────────────────────────────────────
def test_macd_histogram_is_line_minus_signal():
    s = pd.Series(100 + np.cumsum(np.random.default_rng(4).normal(0, 1, 200)))
    line, sig, hist = macd(s)
    assert (hist - (line - sig)).abs().max() < 1e-9


def test_stochastic_bounded_0_100():
    rng = np.random.default_rng(5)
    df = _ohlc(100 + np.cumsum(rng.normal(0, 1, 300)), spread=2.0)
    k, d = stochastic(df)
    assert k.min() >= 0 and k.max() <= 100 and d.min() >= 0 and d.max() <= 100


def test_to_dataframe_roundtrip():
    candles = [Candle(dt="2026-07-09T00:00:00", open=1, high=2, low=0.5, close=1.5, volume=10)]
    df = to_dataframe(candles)
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert df["close"].iloc[0] == 1.5
