"""Deterministic signal engine.

Turns OHLC candles + indicators + the active risk profile into a structured
``Signal``. All numbers (entry, ATR stop, TP ladder, R:R) are computed here —
the LLM layer only annotates and risk-checks the result.
"""

from __future__ import annotations

import os

import numpy as np

from core.models import Indicators, Signal, TakeProfit
from risk.profiles import RiskProfile

from .indicators import adx, compute_indicators, macd, rsi, to_dataframe

# Interval → (display label, trading style)
TF_META = {
    "1min": ("M1", "scalp"),
    "5min": ("M5", "scalp"),
    "15min": ("M15", "scalp"),
    "30min": ("M30", "intraday"),
    "1h": ("H1", "intraday"),
    "4h": ("H4", "swing"),
    "1day": ("D1", "swing"),
}


def _decimals(price: float) -> int:
    if price < 10:
        return 5
    if price < 1000:
        return 3
    return 2


def _round(price: float, ref: float) -> float:
    return round(price, _decimals(ref))


def _trend_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """Selective TREND-CONTINUATION decision (replaces the old majority vote).

    Research-backed: the only robust gold edge is trend-following. So we trade
    ONLY in the direction of the higher-EMA trend, ONLY on a pullback-to-EMA20
    that reclaims, and we stand aside (flat) by default. We use MA *slope* as the
    regime gate — NOT a hard ADX filter (ADX gating measurably hurts gold) — and
    we do NOT take counter-trend / RSI-mean-reversion entries (a documented loser).
    Returns (direction, confidence, reason).
    """
    close = df["close"]
    price = float(close.iloc[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)
    ema20, ema50, ema200 = ind.ema_fast, ind.ema_slow, ind.ema200
    if not ema200:
        return "flat", 0.0, "not enough history for the EMA200 trend filter"

    # 1) Trend bias: EMA stack + EMA50 slope (the regime gate).
    ema50s = close.ewm(span=50, adjust=False).mean()
    slope = float(ema50s.iloc[-1] - ema50s.iloc[-6]) if len(close) >= 6 else 0.0
    long_bias = price > ema200 and ema50 > ema200 and slope > 0
    short_bias = price < ema200 and ema50 < ema200 and slope < 0
    if not (long_bias or short_bias):
        return "flat", 0.0, "no clean trend — EMA stack/slope not aligned (stand aside)"
    direction = "long" if long_bias else "short"

    # 2) Pullback-to-EMA20 + reclaim trigger (entry timing in the trend).
    recent = df.tail(8)
    if direction == "long":
        pulled = bool((recent["low"] <= ema20 + 1.0 * atr).any())  # dipped toward EMA20
        reclaim = price > ema20                                    # back in trend
        # 2.5×ATR VALIDATED by A/B backtest (2026-07-02): tightening to 1.2 cut trades
        # 43→24 and PF 1.19→1.03 (over-filtering). Keep the original bound.
        not_extreme = ind.rsi14 < 72 and (price - ema20) < 2.5 * atr  # not chasing an extended leg
    else:
        pulled = bool((recent["high"] >= ema20 - 1.0 * atr).any())
        reclaim = price < ema20
        not_extreme = ind.rsi14 > 28 and (ema20 - price) < 2.5 * atr
    if not (pulled and reclaim):
        return "flat", 0.0, f"{direction} trend but no pullback-to-EMA20 reclaim yet"
    if not not_extreme:
        return "flat", 0.0, f"{direction} trend but RSI extreme — not chasing"

    # 3) Structure room: need ≥ 1.5R to the nearest opposing level.
    risk_dist = profile.atr_mult * atr
    if direction == "long" and ind.resistance and ind.resistance > price and \
            (ind.resistance - price) < 1.5 * risk_dist:
        return "flat", 0.0, "no room — resistance < 1.5R away"
    if direction == "short" and ind.support and 0 < ind.support < price and \
            (price - ind.support) < 1.5 * risk_dist:
        return "flat", 0.0, "no room — support < 1.5R away"

    # Confidence from trend strength (ADX as SOFT input only) + slope + RSI room.
    adx_factor = max(0.0, min(1.0, (ind.adx14 - 15) / 25))
    slope_factor = max(0.0, min(1.0, abs(slope) / (0.5 * atr)))
    rsi_room = ((72 - ind.rsi14) if direction == "long" else (ind.rsi14 - 28)) / 40
    rsi_room = max(0.0, min(1.0, rsi_room))
    conf = round(max(0.0, min(1.0, 0.45 + 0.25 * adx_factor + 0.15 * slope_factor + 0.15 * rsi_room)), 2)
    return direction, conf, f"{direction} trend-continuation: EMA200 bias + pullback-to-EMA20 reclaim"


def _intraday_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """ACTIVE intraday trend-pullback (v2). Fires several times/day, not per year.

    Relaxations vs the strict _trend_setup: a 2-of-3 trend bias (not triple-strict),
    and an entry ZONE (pullback-tag OR shallow-pullback-with-RSI-turn OR momentum
    resume) instead of a knife-edge reclaim — so it stops saying NO TRADE constantly.
    Kept: trade only WITH the trend, RSI anti-chase, no over-extension.
    """
    close = df["close"]
    price = float(close.iloc[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)
    ema20, ema50, ema200 = ind.ema_fast, ind.ema_slow, ind.ema200
    ema50s = close.ewm(span=50, adjust=False).mean()
    slope = float(ema50s.iloc[-1] - ema50s.iloc[-6]) if len(close) >= 6 else 0.0

    # 1) Trend bias — 2 of 3 (price>EMA50, EMA50>EMA200, slope up). Mirror for short.
    ema_hi = ema200 if ema200 else ema20
    long_score = (price > ema50) + (ema50 > ema_hi) + (slope > 0)
    short_score = (price < ema50) + (ema50 < ema_hi) + (slope < 0)
    if long_score >= 2 and long_score > short_score:
        direction = "long"
    elif short_score >= 2 and short_score > long_score:
        direction = "short"
    else:
        return "flat", 0.0, "no intraday trend bias (need 2 of 3: price/EMA50, EMA50/EMA200, slope)"

    # 2) Anti-chase: don't buy an exhausted/over-extended leg.
    if direction == "long" and (ind.rsi14 > 70 or (price - ema20) > 2.0 * atr):
        return "flat", 0.0, "long bias but overbought/extended — waiting for a pullback"
    if direction == "short" and (ind.rsi14 < 30 or (ema20 - price) > 2.0 * atr):
        return "flat", 0.0, "short bias but oversold/extended — waiting for a pullback"

    # NOTE: a hard EMA200 side filter + reclaim-only entry was TESTED and made pullback
    # WORSE (82→70 trades, PF 0.95→0.84) — reverted. Pullback's weakness is the setup
    # itself, not this filter; don't force it.

    # 3) Entry trigger — A (clean reclaim) OR B (rest in MA band + RSI turn) OR C (MACD resume).
    recent = df.tail(3)
    rs = rsi(close, 14)
    _, _, hist = macd(close)
    if direction == "long":
        A = bool((recent["low"] <= ema20 + 0.25 * atr).any()) and price > ema20
        B = ((ema20 - 0.3 * atr) <= price <= (ema50 + 0.5 * atr)
             and len(rs) >= 3 and rs.iloc[-1] >= 45 and rs.iloc[-3:-1].min() < 45)
        C = (len(hist) >= 2 and hist.iloc[-1] >= 0 and hist.iloc[-2] < 0 and price > ema50)
    else:
        A = bool((recent["high"] >= ema20 - 0.25 * atr).any()) and price < ema20
        B = ((ema50 - 0.5 * atr) <= price <= (ema20 + 0.3 * atr)
             and len(rs) >= 3 and rs.iloc[-1] <= 55 and rs.iloc[-3:-1].max() > 55)
        C = (len(hist) >= 2 and hist.iloc[-1] <= 0 and hist.iloc[-2] > 0 and price < ema50)
    if not (A or B or C):
        return "flat", 0.0, f"{direction} bias but no pullback/momentum trigger yet"

    # 4) Room to the nearest opposing structure (relaxed to 1R for active mode).
    risk_dist = profile.atr_mult * atr
    room = profile.relax_room_r * risk_dist
    if direction == "long" and ind.resistance and ind.resistance > price and (ind.resistance - price) < room:
        return "flat", 0.0, "no room — resistance < 1R away"
    if direction == "short" and ind.support and 0 < ind.support < price and (price - ind.support) < room:
        return "flat", 0.0, "no room — support < 1R away"

    adx_factor = max(0.0, min(1.0, (ind.adx14 - 15) / 25))
    slope_factor = max(0.0, min(1.0, abs(slope) / (0.5 * atr)))
    conf = 0.50 + 0.10 * adx_factor + 0.08 * slope_factor + (0.05 if A else 0.0)
    conf = round(max(0.0, min(1.0, conf)), 2)
    grade = "A" if A else ("B" if B else "C")
    return direction, conf, f"{direction} intraday pullback (case {grade}) — 2/3 trend + trigger"


# Minimum impulse-leg strength (× ATR) for a valid second entry — filters weak,
# rangebound "legs" that are really just noise. (Research: hardened leg-strength gate.)
SE_LEG_ATR = 1.0

# ── FIRST ENTRY (Al Brooks H1/L1) — the FIRST resumption attempt in a strong trend ──
# Non-redundant with H2/L2: the second-entry detector HARD-requires a failed first
# attempt, so one-legged runaway trends are structurally missed. First entries fail
# more (Brooks 40/60), so this demands an ESTABLISHED trend + a STRONG signal bar.
FE_LEG_ATR   = 1.0    # impulse leg strength (same bar-count basis as second-entry)
FE_BODY_FRAC = 0.5    # signal bar body must be >= this fraction of its range (strong bar)


def _h2l2_second_entry(H, L, atr: float, direction: str,
                       leg_atr: float = SE_LEG_ATR, look: int = 10) -> tuple[bool, str]:
    """Genuine Al Brooks High-2 (long) / Low-2 (short) detector.

    Requires ALL of: (1) a real impulse leg >= leg_atr×ATR, (2) a pullback that does
    NOT erase the impulse (15–95% retrace), and (3) TWO with-trend attempts — a FAILED
    first attempt (H1/L1) followed by THIS bar's second attempt (H2/L2). Replaces the
    old crude "one break + any prior lower-low" counter that fired on noise.
    Operates on the last bar as the signal bar. Returns (ok, reason).
    """
    H = np.asarray(H, float)
    L = np.asarray(L, float)
    n = len(H)
    look = min(look, n - 2)
    if look < 4:
        return False, "not enough bars"

    if direction == "long":
        if not (H[-1] > H[-2]):
            return False, "no up-break trigger this bar"
        segH, segL = H[-look - 1:-1], L[-look - 1:-1]              # bars before the current one
        hi_idx = int(segH.argmax())                                # impulse top
        swing_hi = segH[hi_idx]
        if hi_idx == 0:
            return False, "no impulse leg before swing high"
        impulse_low = segL[:hi_idx + 1].min()
        impulse = swing_hi - impulse_low
        if impulse < leg_atr * atr:
            return False, f"impulse leg too weak ({impulse:.2f} < {leg_atr:g}×ATR)"
        pull_L = segL[hi_idx + 1:]
        if len(pull_L) < 1:
            return False, "no pullback after impulse"
        pull_low = min(pull_L.min(), L[-1])
        if pull_low <= impulse_low:
            return False, "pullback erased the impulse (not a continuation)"
        retr = (swing_hi - pull_low) / impulse if impulse > 0 else 0.0
        if not (0.15 <= retr <= 0.95):
            return False, f"pullback depth {retr:.0%} out of 15–95% range"
        first = False                                              # a failed H1 before this H2
        for k in range(hi_idx + 1, len(segH)):
            if segH[k] > segH[k - 1]:
                after = min(segL[k + 1:].min() if k + 1 < len(segL) else segL[k], L[-1])
                if after < segL[k]:
                    first = True
                    break
        if not first:
            return False, "only one up-attempt (need a failed H1 then this H2)"
        return True, f"H2 (impulse {impulse:.2f}, pullback {retr:.0%})"

    else:  # short — mirror (Low-2)
        if not (L[-1] < L[-2]):
            return False, "no down-break trigger this bar"
        segH, segL = H[-look - 1:-1], L[-look - 1:-1]
        lo_idx = int(segL.argmin())                                # impulse bottom
        swing_lo = segL[lo_idx]
        if lo_idx == 0:
            return False, "no impulse leg before swing low"
        impulse_high = segH[:lo_idx + 1].max()
        impulse = impulse_high - swing_lo
        if impulse < leg_atr * atr:
            return False, f"impulse leg too weak ({impulse:.2f} < {leg_atr:g}×ATR)"
        pull_H = segH[lo_idx + 1:]
        if len(pull_H) < 1:
            return False, "no pullback after impulse"
        pull_high = max(pull_H.max(), H[-1])
        if pull_high >= impulse_high:
            return False, "pullback erased the impulse (not a continuation)"
        retr = (pull_high - swing_lo) / impulse if impulse > 0 else 0.0
        if not (0.15 <= retr <= 0.95):
            return False, f"pullback depth {retr:.0%} out of 15–95% range"
        first = False
        for k in range(lo_idx + 1, len(segH)):
            if segL[k] < segL[k - 1]:
                after = max(segH[k + 1:].max() if k + 1 < len(segH) else segH[k], H[-1])
                if after > segH[k]:
                    first = True
                    break
        if not first:
            return False, "only one down-attempt (need a failed L1 then this L2)"
        return True, f"L2 (impulse {impulse:.2f}, pullback {retr:.0%})"


_SE_ROOM_ATR = float(os.getenv("SE_ROOM_ATR", "0") or 0)   # secondentry ROOM gate; 0 = OFF (default)


def _second_entry_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """Al Brooks-style 'Second Entry' (H2/L2) price-action scalping.

    M5, EMA21 trend filter, enter on a GENUINE second push after a pullback — validated
    by _h2l2_second_entry (real impulse leg + non-erasing pullback + failed-first-attempt).
    Skips news-size candles, doji/inside signal bars, and over-extended moves. Stop =
    signal bar extreme (handled in generate_signal).
    """
    close, high, low, op = df["close"], df["high"], df["low"], df["open"]
    if len(df) < 8:
        return "flat", 0.0, "not enough bars for second-entry"
    price = float(close.iloc[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)
    ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])

    direction = "long" if price > ema21 else "short" if price < ema21 else "flat"
    if direction == "flat":
        return "flat", 0.0, "price on EMA21 — no trend"

    H, L, C, O = high.values, low.values, close.values, op.values
    rng = H[-1] - L[-1]
    if rng > 3.0 * atr:
        return "flat", 0.0, "skip — news-size candle"
    if rng > 0 and abs(C[-1] - O[-1]) < 0.1 * rng:
        return "flat", 0.0, "signal bar is a doji (indecision)"
    if H[-1] <= H[-2] and L[-1] >= L[-2]:
        return "flat", 0.0, "signal bar is an inside bar"

    not_extreme = ind.rsi14 < 75 if direction == "long" else ind.rsi14 > 25
    if not not_extreme:
        return "flat", 0.0, f"{direction} but over-extended (RSI) — skip"

    ok, why = _h2l2_second_entry(H, L, atr, direction)
    if not ok:
        return "flat", 0.0, f"{direction} trend but {why}"

    # ROOM gate (env SE_ROOM_ATR, default 0 = OFF): don't take a second entry that runs
    # STRAIGHT INTO an opposing level with < SE_ROOM_ATR×ATR of clear air — TP1 sits on the
    # level, so it tends to just-miss TP1, bounce, and stop out (the −$22.80 short at 4147.7
    # into support 4144). Brooks "trade FROM a level, not INTO one". Same idea trend/pullback
    # already use. A/B-tested on Dukascopy before enabling live; 0 keeps behaviour unchanged.
    _room = _SE_ROOM_ATR * atr
    if _room > 0:
        if direction == "long" and ind.resistance and price < ind.resistance and (ind.resistance - price) < _room:
            return "flat", 0.0, f"no room — resistance {ind.resistance:.2f} < {_SE_ROOM_ATR:g}×ATR away"
        if direction == "short" and ind.support and 0 < ind.support < price and (price - ind.support) < _room:
            return "flat", 0.0, f"no room — support {ind.support:.2f} < {_SE_ROOM_ATR:g}×ATR away"

    near_ema = bool(min(L[-4:]) <= ema21 <= max(H[-4:]))          # pullback tagged EMA21 = confluence
    conf = 0.50 + (0.12 if near_ema else 0.0) + min(0.15, max(0.0, (ind.adx14 - 15) / 40))
    conf = round(min(1.0, conf), 2)
    return direction, conf, f"{direction} second-entry (M5 {why}){' @EMA21' if near_ema else ''}"


def _h1l1_first_entry(H, L, C, O, atr: float, direction: str,
                      leg_atr: float = FE_LEG_ATR, look: int = 10) -> tuple[bool, str]:
    """Al Brooks First entry (H1 long / L1 short): the FIRST attempt to resume a trend
    after a pullback — NO failed-first-attempt required (unlike H2/L2). Because a first
    entry fails more often, it demands a STRONG signal bar (body ≥ FE_BODY_FRAC of range,
    closing in the trend-direction third). Same impulse+pullback structure otherwise.
    """
    H = np.asarray(H, float); L = np.asarray(L, float)
    C = np.asarray(C, float); O = np.asarray(O, float)
    n = len(H)
    look = min(look, n - 2)
    if look < 4:
        return False, "not enough bars"
    rng = H[-1] - L[-1]
    if rng <= 0:
        return False, "zero-range signal bar"
    if abs(C[-1] - O[-1]) < FE_BODY_FRAC * rng:
        return False, f"signal bar too weak (body <{FE_BODY_FRAC:.0%} of range)"

    if direction == "long":
        if not (H[-1] > H[-2]):
            return False, "no up-break trigger this bar"
        if C[-1] < H[-1] - rng / 3.0:
            return False, "bull signal bar didn't close strong (top third)"
        segH, segL = H[-look - 1:-1], L[-look - 1:-1]
        hi_idx = int(segH.argmax()); swing_hi = segH[hi_idx]
        if hi_idx == 0:
            return False, "no impulse leg before swing high"
        impulse_low = segL[:hi_idx + 1].min(); impulse = swing_hi - impulse_low
        if impulse < leg_atr * atr:
            return False, f"impulse leg too weak ({impulse:.2f} < {leg_atr:g}×ATR)"
        pull_L = segL[hi_idx + 1:]
        if len(pull_L) < 1:
            return False, "no pullback after impulse"
        pull_low = min(pull_L.min(), L[-1])
        if pull_low <= impulse_low:
            return False, "pullback erased the impulse (not a continuation)"
        retr = (swing_hi - pull_low) / impulse if impulse > 0 else 0.0
        if not (0.15 <= retr <= 0.95):
            return False, f"pullback depth {retr:.0%} out of 15–95% range"
        return True, f"H1 (impulse {impulse:.2f}, pullback {retr:.0%})"

    else:  # short — mirror (Low-1)
        if not (L[-1] < L[-2]):
            return False, "no down-break trigger this bar"
        if C[-1] > L[-1] + rng / 3.0:
            return False, "bear signal bar didn't close strong (bottom third)"
        segH, segL = H[-look - 1:-1], L[-look - 1:-1]
        lo_idx = int(segL.argmin()); swing_lo = segL[lo_idx]
        if lo_idx == 0:
            return False, "no impulse leg before swing low"
        impulse_high = segH[:lo_idx + 1].max(); impulse = impulse_high - swing_lo
        if impulse < leg_atr * atr:
            return False, f"impulse leg too weak ({impulse:.2f} < {leg_atr:g}×ATR)"
        pull_H = segH[lo_idx + 1:]
        if len(pull_H) < 1:
            return False, "no pullback after impulse"
        pull_high = max(pull_H.max(), H[-1])
        if pull_high >= impulse_high:
            return False, "pullback erased the impulse (not a continuation)"
        retr = (pull_high - swing_lo) / impulse if impulse > 0 else 0.0
        if not (0.15 <= retr <= 0.95):
            return False, f"pullback depth {retr:.0%} out of 15–95% range"
        return True, f"L1 (impulse {impulse:.2f}, pullback {retr:.0%})"


def _first_entry_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """Brooks FIRST-entry (H1/L1) — strong-trend continuation, WITH-TREND ONLY.

    Requires an ESTABLISHED trend (price above a RISING EMA21 for longs / below a FALLING
    EMA21 for shorts, judged by the 6-bar EMA slope) and fires on the FIRST pullback
    resumption — catching the one-legged runaway trends the second-entry misses. Gated
    harder downstream (passes_gate: FE_CONF_FLOOR, ADX≥FE_ADX_MIN, RR≥FE_MIN_RR).
    """
    close, high, low, op = df["close"], df["high"], df["low"], df["open"]
    if len(df) < 10:
        return "flat", 0.0, "not enough bars for first-entry"
    price = float(close.iloc[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)
    ema_series = close.ewm(span=21, adjust=False).mean()
    ema21 = float(ema_series.iloc[-1])
    slope = ema21 - float(ema_series.iloc[-7])          # 6-bar EMA21 slope = established trend

    if price > ema21 and slope > 0:
        direction = "long"
    elif price < ema21 and slope < 0:
        direction = "short"
    else:
        return "flat", 0.0, "no established EMA21 trend (first-entry needs a strong trend)"

    H, L, C, O = high.values, low.values, close.values, op.values
    rng = H[-1] - L[-1]
    if rng > 3.0 * atr:
        return "flat", 0.0, "skip — news-size candle"
    not_extreme = ind.rsi14 < 78 if direction == "long" else ind.rsi14 > 22
    if not not_extreme:
        return "flat", 0.0, f"{direction} but over-extended (RSI) — skip"

    ok, why = _h1l1_first_entry(H, L, C, O, atr, direction)
    if not ok:
        return "flat", 0.0, f"{direction} trend but {why}"

    near_ema = bool(min(L[-4:]) <= ema21 <= max(H[-4:]))
    slope_bonus = min(0.10, abs(slope) / atr * 0.10)
    conf = 0.54 + slope_bonus + min(0.12, max(0.0, (ind.adx14 - 15) / 40)) + (0.10 if near_ema else 0.0)
    conf = round(min(1.0, conf), 2)
    return direction, conf, f"{direction} first-entry (M5 {why}){' @EMA21' if near_ema else ''}"


# ── Breakout + Retest (research: the one genuinely-new, gold-aligned regime setup) ──
# Gold FOLLOWS THROUGH on breakouts >66% of the time, so we trade WITH the break (never
# fade it): compressed range → close beyond it → a retest that holds → enter.
BR_BASE_N     = 10       # bars forming the consolidation base
BR_WIN        = 5        # look this many recent bars for the breakout close
BR_RANGE_ATR  = 1.5      # base is "compressed" only if its height ≤ this × ATR
BR_BREAK_ATR  = 0.3      # a breakout must CLOSE beyond the range by ≥ this × ATR
BR_RETEST_ATR = 0.4      # the retest bar must come back within this × ATR of the level
BR_TP_MM      = (0.5, 1.0, 1.5)   # take-profit ladder as fractions of the MEASURED MOVE (range height)


def _breakout_retest(df, atr: float):
    """Detect a breakout-and-retest-hold on the last bar.

    Returns (direction, reason, range_hi, range_lo, height). 'long'/'short' only when
    the current bar retests a broken compressed-range level and closes back through it.
    """
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    O = df["open"].values
    n = len(C)
    if n < BR_BASE_N + BR_WIN + 1:
        return "flat", "not enough bars for breakout", 0.0, 0.0, 0.0
    base_H = H[-(BR_BASE_N + BR_WIN):-BR_WIN]
    base_L = L[-(BR_BASE_N + BR_WIN):-BR_WIN]
    rh = float(base_H.max())
    rl = float(base_L.min())
    height = rh - rl
    if height <= 0:
        return "flat", "degenerate range", rh, rl, height
    if height > BR_RANGE_ATR * atr:
        return "flat", f"no compression ({height:.2f} > {BR_RANGE_ATR:g}×ATR)", rh, rl, height
    winC = C[-BR_WIN:]
    up = bool((winC > rh + BR_BREAK_ATR * atr).any())
    dn = bool((winC < rl - BR_BREAK_ATR * atr).any())
    # NOTE: a multi-bar retest window (BR_RETEST_WIN>1) was tested and made breakout WORSE
    # (18 trades @ PF 1.13 → 28 @ PF 0.93) — firing more brought in losers; the STRICT
    # single-bar retest-hold is what carries the 61% win rate. Kept strict on purpose.
    if up and not dn:
        retest = L[-1] <= rh + BR_RETEST_ATR * atr
        hold = C[-1] > rh and C[-1] > O[-1]
        if retest and hold:
            return "long", f"breakout+retest of {rh:.2f} (range {height:.2f})", rh, rl, height
        return "flat", "bull breakout, waiting for retest-hold", rh, rl, height
    if dn and not up:
        retest = H[-1] >= rl - BR_RETEST_ATR * atr
        hold = C[-1] < rl and C[-1] < O[-1]
        if retest and hold:
            return "short", f"breakdown+retest of {rl:.2f} (range {height:.2f})", rh, rl, height
        return "flat", "bear breakout, waiting for retest-hold", rh, rl, height
    return "flat", "no clean single-direction breakout", rh, rl, height


def _breakout_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """Breakout+Retest entry. Trades WITH the breakout only (gold follow-through bias)."""
    price = float(df["close"].iloc[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)
    direction, reason, rh, rl, height = _breakout_retest(df, atr)
    if direction == "flat":
        return "flat", 0.0, reason
    # Confidence: tighter compression + a real breakout thrust + some trendiness = better.
    compression = 1.0 - min(1.0, height / (BR_RANGE_ATR * atr))    # 0..1 (tighter → higher)
    adx_factor = max(0.0, min(1.0, (ind.adx14 - 15) / 25))
    conf = 0.52 + 0.10 * compression + 0.10 * adx_factor
    conf = round(max(0.0, min(1.0, conf)), 2)
    return direction, conf, f"{direction} {reason}"


# ── RANGE-FADE (experimental, research 2026-07-03): fade the extremes of a CLEAN,
# PROVEN range — sell a failed poke above resistance / buy a failed poke below support,
# target the midpoint then the far boundary. Evidence: Brooks (range breakouts fail
# ~80%, fade clean edges, avoid barbwire); BUT one gold-specific backtest found naive
# mean-reversion NET-NEGATIVE on XAUUSD — so the REGIME FILTER is unusually strict and
# the strategy runs small (conservative tier) + separately tracked until proven. ──────
RF_LOOKBACK      = 40    # bars defining the range
RF_MIN_RANGE_ATR = 1.5   # range height must be ≥ this × ATR (reject barbwire/micro-chop)
RF_TOUCH_TOL_ATR = 0.25  # a bar within this of a boundary counts as a "touch"
RF_MIN_TOUCHES   = 2     # each boundary must be respected ≥ this many times
RF_ADX_MAX       = 18.0  # regime: no trend strength...
RF_ADX_RISE_MAX  = 2.0   # ...and ADX not RISING (low-but-rising = a trend being born)
RF_BBW_Q         = 0.34  # Bollinger width must sit in the bottom third of its 100-bar range
RF_POKE_ATR      = 0.10  # the entry bar must poke within/through this of the boundary


def _range_geometry(df, atr: float):
    """The last RF_LOOKBACK-bar range (excluding the live bar): (hi, lo, height, touches_hi,
    touches_lo, clean). clean = boundaries respected, no decisive close outside."""
    seg = df.iloc[-(RF_LOOKBACK + 1):-1]
    if len(seg) < RF_LOOKBACK // 2:
        return 0.0, 0.0, 0.0, 0, 0, False
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    height = hi - lo
    tol = RF_TOUCH_TOL_ATR * atr
    touches_hi = int((seg["high"] >= hi - tol).sum())
    touches_lo = int((seg["low"] <= lo + tol).sum())
    # Cleanliness = both boundaries RESPECTED repeatedly (hi/lo are the window's own
    # extremes, so "no close outside" is true by construction — touches are the test).
    clean = touches_hi >= RF_MIN_TOUCHES and touches_lo >= RF_MIN_TOUCHES
    return hi, lo, height, touches_hi, touches_lo, clean


def _range_fade_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """Fade a failed poke at the boundary of a clean range — only when the regime
    filter says this chop is SAFE to fade (see module comment for the evidence)."""
    close = df["close"]
    price = float(close.iloc[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)

    # ── REGIME FILTER (all must pass; this is the load-bearing part) ──
    if ind.adx14 >= RF_ADX_MAX:
        return "flat", 0.0, f"ADX {ind.adx14:.0f}≥{RF_ADX_MAX:.0f} — trending, not a fade regime"
    try:  # ADX must not be RISING from low (a trend being born — the classic trap)
        from analysis.indicators import compute_indicators as _ci
        adx_prev = _ci(df.iloc[:-5].copy()).adx14
        if ind.adx14 - adx_prev > RF_ADX_RISE_MAX:
            return "flat", 0.0, "ADX rising from low — possible trend birth, no fade"
    except Exception:  # noqa: BLE001 — regime check best-effort
        pass
    if len(close) >= 120:   # Bollinger-width squeeze: bottom third of its own 100-bar range
        mid = close.rolling(20).mean()
        sd = close.rolling(20).std()
        bbw = (4.0 * sd / mid).dropna()
        if len(bbw) >= 100 and float(bbw.iloc[-1]) > float(bbw.iloc[-100:].quantile(RF_BBW_Q)):
            return "flat", 0.0, "volatility not compressed — range not stable enough to fade"

    hi, lo, height, t_hi, t_lo, clean = _range_geometry(df, atr)
    if height < RF_MIN_RANGE_ATR * atr:
        return "flat", 0.0, f"range too tight ({height:.2f} < {RF_MIN_RANGE_ATR:g}×ATR) — barbwire, stand aside"
    if not clean:
        return "flat", 0.0, f"range not clean (touches {t_hi}/{t_lo}, or closes outside) — no proven edges"

    # ── ENTRY TRIGGER: failed-breakout reversal bar AT the boundary (Brooks) ──
    bar_h, bar_l = float(df["high"].iloc[-1]), float(df["low"].iloc[-1])
    bar_o = float(df["open"].iloc[-1])
    poke = RF_POKE_ATR * atr
    if bar_h >= hi - poke and price < hi and price < bar_o:      # poked resistance, closing back inside, bearish bar
        direction = "short"
    elif bar_l <= lo + poke and price > lo and price > bar_o:    # poked support, closing back inside, bullish bar
        direction = "long"
    else:
        return "flat", 0.0, "range is clean but price is mid-range — wait for a boundary poke"

    # Confidence: proven edges + RSI extremity at the boundary add conviction.
    rsi_x = (max(0.0, ind.rsi14 - 60) if direction == "short" else max(0.0, 40 - ind.rsi14)) / 30.0
    conf = 0.55 + 0.03 * min(t_hi + t_lo - 2 * RF_MIN_TOUCHES, 4) + 0.10 * min(rsi_x, 1.0)
    conf = round(max(0.0, min(1.0, conf)), 2)
    return direction, conf, (f"{direction} range-fade: failed poke at "
                             f"{'resistance %.2f' % hi if direction == 'short' else 'support %.2f' % lo} "
                             f"(range {height:.2f}, touches {t_hi}/{t_lo})")


# ── SQUEEZE BREAKOUT (volatility-compression breakout for consolidation regimes) ──
# Research + our OWN 4-regime Dukascopy backtest (2026-07-07): a TTM-style squeeze
# (Bollinger INSIDE Keltner = volatility compression) traded on RELEASE, in the momentum
# direction, ONLY with a decisive close outside the bands AND a volume expansion, is
# NET-POSITIVE and strong in the CHOP/consolidation regime (chop2026 PF ~1.8 at live cost,
# robust across R 2.5-3.5 & vol 1.3-1.8). The volume filter is LOAD-BEARING (without it PF
# 0.81 — a false-breakout machine). Direction-AGNOSTIC: it follows the break, so it works in
# an H1/D1 tug-of-war that paralyses the directional strategies. Marginal overall / weak in a
# grinding bear → runs small + monitored (like range_fade). Env flag SQUEEZE_ENABLED.
SQ_VOL_MULT = 1.5    # break-bar volume must be >= this × its 20-bar average (cuts false breaks)
SQ_BB_STD   = 2.0
SQ_KC_ATR   = 1.5


def _squeeze_setup(df, ind: Indicators, profile: RiskProfile) -> tuple[str, float, str]:
    """Volatility-squeeze breakout: enter on the bar a Bollinger-inside-Keltner squeeze
    RELEASES, in the momentum direction, ONLY on a decisive close outside the bands with a
    volume expansion. Direction-agnostic (rides whichever way the compression snaps)."""
    close = df["close"]
    if len(close) < 40:
        return "flat", 0.0, "not enough bars for squeeze"
    price = float(close.iloc[-1])
    sma = close.rolling(20).mean()
    sd = close.rolling(20).std()
    bb_up, bb_lo = sma + SQ_BB_STD * sd, sma - SQ_BB_STD * sd
    ema = close.ewm(span=20, adjust=False).mean()
    h, l, c = df["high"], df["low"], close
    pc = c.shift(1)
    tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
    atr20 = tr.rolling(20).mean()
    kc_up, kc_lo = ema + SQ_KC_ATR * atr20, ema - SQ_KC_ATR * atr20
    sq = (bb_up < kc_up) & (bb_lo > kc_lo)                       # TTM squeeze ON (compression)
    if not (bool(sq.iloc[-2]) and not bool(sq.iloc[-1])):        # fire only on the RELEASE bar
        return "flat", 0.0, "no squeeze release on this bar"
    mid = ((df["high"].rolling(20).max() + df["low"].rolling(20).min()) / 2.0 + sma) / 2.0
    mom = price - float(mid.iloc[-1])
    direction = "long" if mom > 0 else "short"
    if direction == "long" and price <= float(bb_up.iloc[-1]):
        return "flat", 0.0, "released up but no decisive close above the upper band"
    if direction == "short" and price >= float(bb_lo.iloc[-1]):
        return "flat", 0.0, "released down but no decisive close below the lower band"
    vol = df["volume"]
    vavg = float(vol.rolling(20).mean().iloc[-1] or 0.0)
    vnow = float(vol.iloc[-1])
    if vavg > 0 and vnow < SQ_VOL_MULT * vavg:                   # LOAD-BEARING false-break filter
        return "flat", 0.0, f"break volume {vnow:.0f} < {SQ_VOL_MULT:g}× avg — likely false break"
    vratio = (vnow / vavg) if vavg > 0 else 1.0
    conf = (0.60 + 0.06 * min(max(vratio - SQ_VOL_MULT, 0.0), 1.5)
            + 0.05 * min(abs(mom) / (float(atr20.iloc[-1]) or 1e-9), 1.0))
    conf = round(max(0.0, min(1.0, conf)), 2)
    return direction, conf, (f"{direction} squeeze breakout: BB/KC release, decisive close "
                             f"{'above upper' if direction == 'long' else 'below lower'} band, "
                             f"vol {vratio:.1f}× avg")


# ── CONFLUENCE BOOSTERS (research: the real edge is stacking confirmations) ─────
# Each is a Tier-2 booster: demoted SMC concepts (Order Block, FVG, liquidity sweep)
# plus classic confluence (EMA stack, ADX rising, room). They NEVER trade alone —
# they add to a confluence score on top of a primary (Tier-1) strategy trigger.
def _has_fvg(H, L, direction: str, look: int = 6) -> bool:
    """A 3-candle Fair Value Gap (imbalance) in the trade direction, recently."""
    n = len(H)
    for i in range(max(2, n - look), n):
        if direction == "long" and L[i] > H[i - 2]:      # gap up
            return True
        if direction == "short" and H[i] < L[i - 2]:     # gap down
            return True
    return False


def _has_order_block(H, L, C, O, atr: float, direction: str, look: int = 10) -> bool:
    """An opposite-colour candle immediately before a strong with-trend impulse."""
    n = len(C)
    for i in range(max(1, n - look), n - 1):
        body = abs(C[i + 1] - O[i + 1])
        if direction == "long" and C[i] < O[i] and C[i + 1] > O[i + 1] and body >= 1.0 * atr:
            return True
        if direction == "short" and C[i] > O[i] and C[i + 1] < O[i + 1] and body >= 1.0 * atr:
            return True
    return False


def _has_sweep(H, L, C, atr: float, direction: str, look: int = 10) -> bool:
    """A liquidity sweep of a prior swing that closed back through (follow-through)."""
    n = len(C)
    if n < look + 3:
        return False
    if direction == "long":
        prior_low = min(L[-look - 1:-3])
        return any(L[i] < prior_low and C[i] > prior_low for i in range(n - 3, n))
    prior_high = max(H[-look - 1:-3])
    return any(H[i] > prior_high and C[i] < prior_high for i in range(n - 3, n))


def confluence(df, ind: Indicators, direction: str) -> tuple[int, list[str]]:
    """Count Tier-2 confluence boosters present for a trade in `direction`.
    Returns (score, tags). Higher = more independent confirmations stacked."""
    if direction not in ("long", "short"):
        return 0, []
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    O = df["open"].values
    price = float(C[-1])
    atr = ind.atr14 or max(price * 0.005, 1e-6)
    tags: list[str] = []

    # EMA stack aligned (trend confluence)
    if direction == "long" and price > ind.ema_fast > ind.ema_slow:
        tags.append("EMA-stack")
    elif direction == "short" and price < ind.ema_fast < ind.ema_slow:
        tags.append("EMA-stack")

    # ADX trending and rising
    adx_s = adx(df, 14)
    if len(adx_s) >= 5 and ind.adx14 >= 20 and float(adx_s.iloc[-1]) > float(adx_s.iloc[-4]):
        tags.append("ADX-rising")

    # Room to the next opposing structure (≥ 1.5×ATR to run)
    if direction == "long":
        room = (ind.resistance - price) if ind.resistance > price else 99 * atr
    else:
        room = (price - ind.support) if 0 < ind.support < price else 99 * atr
    if room >= 1.5 * atr:
        tags.append("room")

    if _has_fvg(H, L, direction):
        tags.append("FVG")
    if _has_order_block(H, L, C, O, atr, direction):
        tags.append("OB")
    if _has_sweep(H, L, C, atr, direction):
        tags.append("sweep")

    return len(tags), tags


def generate_signal(symbol: str, candles, profile: RiskProfile, interval: str = "1h") -> Signal:
    df = to_dataframe(candles)
    label, style = TF_META.get(interval, ("H1", "intraday"))
    ind = compute_indicators(df)
    price = float(df["close"].iloc[-1])

    atr_val = ind.atr14 or max(price * 0.005, 0.01)
    mode = getattr(profile, "strategy_mode", "pullback")
    if mode == "trend":
        direction, confidence, reason = _trend_setup(df, ind, profile)
        floor = profile.confidence_floor
    elif mode == "secondentry":
        direction, confidence, reason = _second_entry_setup(df, ind, profile)
        floor = profile.intraday_floor
    elif mode == "firstentry":
        direction, confidence, reason = _first_entry_setup(df, ind, profile)
        floor = profile.intraday_floor
    elif mode == "breakout":
        direction, confidence, reason = _breakout_setup(df, ind, profile)
        floor = profile.intraday_floor
    elif mode == "range_fade":
        direction, confidence, reason = _range_fade_setup(df, ind, profile)
        floor = profile.intraday_floor
    elif mode == "squeeze":
        direction, confidence, reason = _squeeze_setup(df, ind, profile)
        floor = profile.intraday_floor
    else:  # active intraday pullback (default)
        direction, confidence, reason = _intraday_setup(df, ind, profile)
        floor = profile.intraday_floor
    regime_ok = direction != "flat"
    if direction != "flat" and confidence < floor:
        direction, reason = "flat", f"{reason} (confidence {confidence:.2f} < floor {floor:.2f})"

    if direction == "flat":
        return Signal(
            symbol=symbol, timeframe=label, direction="flat",
            entry=_round(price, price), stop_loss=0.0, take_profits=[],
            risk_reward=0.0, confidence=confidence, atr=round(atr_val, _decimals(price)),
            indicators=ind, invalidation_price=0.0,
            invalidation_reason=f"No trade: {reason}.",
            position_size={"risk_pct": profile.risk_pct,
                           "formula": "n/a — no trade", "note": "Stand aside; wait for a cleaner setup."},
            regime_ok=regime_ok, style=style,
        )

    entry = price
    if mode in ("secondentry", "firstentry"):
        # Stop just beyond the signal bar's extreme (tight, scalping) — per the video.
        sig_low = float(df["low"].iloc[-1])
        sig_high = float(df["high"].iloc[-1])
        if direction == "long":
            stop_dist = max(entry - (sig_low - 0.1 * atr_val), 0.3 * atr_val)
        else:
            stop_dist = max((sig_high + 0.1 * atr_val) - entry, 0.3 * atr_val)
    elif mode == "breakout":
        # Structure stop just beyond the retest bar (the breakout fails if price closes
        # back through the level); floored so a tiny retest bar can't give a razor stop.
        sig_low = float(df["low"].iloc[-1])
        sig_high = float(df["high"].iloc[-1])
        if direction == "long":
            stop_dist = max(entry - (sig_low - 0.1 * atr_val), 0.6 * atr_val)
        else:
            stop_dist = max((sig_high + 0.1 * atr_val) - entry, 0.6 * atr_val)
    elif mode == "range_fade":
        # Stop beyond the failed-poke extreme + buffer (if price re-breaks the boundary,
        # the range broke and the fade thesis is dead — fast structural invalidation).
        sig_low = float(df["low"].iloc[-1])
        sig_high = float(df["high"].iloc[-1])
        if direction == "long":
            stop_dist = max(entry - (sig_low - 0.3 * atr_val), 0.5 * atr_val)
        else:
            stop_dist = max((sig_high + 0.3 * atr_val) - entry, 0.5 * atr_val)
    elif mode == "squeeze":
        stop_dist = 1.5 * atr_val   # matches the backtested stop (1.5×ATR); a break that
        #                             reverses this far = the squeeze failed → out.
    else:
        stop_dist = profile.atr_mult * atr_val

    # TP ladder: breakout targets a MEASURED MOVE (project the range height from entry);
    # everyone else uses the profile's R-multiples. r_multiple is stored so the live
    # trader (mt5_bot) reproduces the same price via r_multiple × stop_distance.
    if mode == "breakout":
        _, _, _, _, height = _breakout_retest(df, atr_val)
        mm = height if height > 0 else 2.0 * stop_dist
        tp_specs = [(round(f * mm / stop_dist, 3), cp) for f, cp in zip(BR_TP_MM, profile.tp_close_pct)]
    elif mode == "range_fade":
        # STRUCTURAL targets: the range midpoint first (the mean-reversion objective),
        # then the far boundary less a small buffer. Expressed as r-multiples of the stop.
        rf_hi, rf_lo, rf_h, _, _, _ = _range_geometry(df, atr_val)
        if direction == "long":
            d1 = max((rf_lo + rf_h / 2.0) - entry, 0.8 * stop_dist)
            d2 = max((rf_hi - 0.3 * atr_val) - entry, d1 + 0.5 * stop_dist)
        else:
            d1 = max(entry - (rf_lo + rf_h / 2.0), 0.8 * stop_dist)
            d2 = max(entry - (rf_lo + 0.3 * atr_val), d1 + 0.5 * stop_dist)
        tp_specs = [(round(d1 / stop_dist, 3), 60.0), (round(d2 / stop_dist, 3), 40.0)]
    elif mode == "squeeze":
        # Ride the expansion: bank part at 1.5R (locks the trade risk-free via the ladder),
        # let the rest run to the backtested 3R target. mt5_bot's _ladder re-splits to 3R.
        tp_specs = [(1.5, 40.0), (3.0, 60.0)]
    else:
        tp_specs = list(zip(profile.tp_r_multiples, profile.tp_close_pct))

    if direction == "long":
        stop = entry - stop_dist
        tps = [TakeProfit(_round(entry + r * stop_dist, price), r, cp) for r, cp in tp_specs]
    else:
        stop = entry + stop_dist
        tps = [TakeProfit(_round(entry - r * stop_dist, price), r, cp) for r, cp in tp_specs]

    rr = profile.primary_rr
    position_size = {
        "risk_pct": profile.risk_pct,
        "formula": "lots = (equity × risk% ) / (stop_distance_in_price × contract_value_per_point)",
        "note": (f"Stop distance ≈ {round(stop_dist, _decimals(price))} {symbol.split('/')[-1]}. "
                 f"Size depends on YOUR equity, leverage and broker contract size."),
    }

    return Signal(
        symbol=symbol, timeframe=label, direction=direction,
        entry=_round(entry, price), stop_loss=_round(stop, price), take_profits=tps,
        risk_reward=rr, confidence=confidence, atr=round(atr_val, _decimals(price)),
        indicators=ind,
        invalidation_price=_round(stop, price),
        invalidation_reason=(f"{label} close beyond the {profile.atr_mult:g}×ATR stop "
                             f"({_round(stop, price)}) invalidates the {direction} thesis. "
                             f"Setup: {reason}."),
        position_size=position_size, regime_ok=regime_ok, style=style,
    )
