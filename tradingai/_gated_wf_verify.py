"""Gated walk-forward for pullback: cohort (a) ALL raw signals vs (b) live-style H1-gated.

The live engine (mt5_bot _passes_gate_impl, HTF_SOFT branch) BLOCKS a pullback ONLY when
the H1 trend DIRECTLY OPPOSES the signal direction; H1-flat is ALLOWED. H1 trend is the EMA
stack from _tf_trend("1h"): long if price>EMA200 & EMA50>EMA200 & slope>0 (mirror short),
else flat; fallback to price/EMA50+slope when <200 H1 bars.

We reproduce that EXACTLY by resampling the M5 history up to bar i into H1 candles (capped at
320 like live outputsize) and applying the same rule. Cohort (b) = signals where htf is flat
OR agrees (i.e. NOT direct-opposite) = the live-gated population.
"""
from __future__ import annotations
import glob, os, sys
from dataclasses import replace
import numpy as np, pandas as pd

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt
import edge_stats

bt.COST_PRICE = float(os.getenv("EWF_COST", "0.20"))
STEP = int(os.getenv("EWF_STEP", "3"))
WINDOW = bt.WINDOW
INST = bt._INST
STRAT = "pullback"


def _load_df(csv):
    df = pd.read_csv(csv)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.reset_index(drop=True)


def _candles(df):
    return [Candle(dt=str(r.ts)[:19], open=float(r.open), high=float(r.high),
                   low=float(r.low), close=float(r.close),
                   volume=float(getattr(r, "volume", 0) or 0))
            for r in df.itertuples(index=False)]


def _h1_trend(closes_h1: pd.Series) -> str:
    """EXACT copy of mt5_bot._tf_trend logic given an H1 close series."""
    if len(closes_h1) < 60:
        return "flat"
    ema50 = closes_h1.ewm(span=50, adjust=False).mean()
    ema200 = closes_h1.ewm(span=200, adjust=False).mean()
    price = float(closes_h1.iloc[-1])
    e50, e200 = float(ema50.iloc[-1]), float(ema200.iloc[-1])
    slope = float(ema50.iloc[-1] - ema50.iloc[-6]) if len(closes_h1) >= 6 else 0.0
    if len(closes_h1) < 200:
        if price > e50 and slope > 0:
            return "long"
        if price < e50 and slope < 0:
            return "short"
        return "flat"
    if price > e200 and e50 > e200 and slope > 0:
        return "long"
    if price < e200 and e50 < e200 and slope < 0:
        return "short"
    return "flat"


def collect(df, candles):
    """Return list of (R, direction, htf) for every pullback signal in the walk."""
    prof = replace(PROFILES["moderate"], strategy_mode=STRAT)
    ts = df["ts"].values
    close = df["close"].values
    # Precompute an H1 close series aligned to df index via resample on a rolling basis is slow;
    # instead resample the WHOLE df once to H1 and, at each i, take H1 bars whose end <= ts[i].
    dfx = df.set_index("ts")
    h1 = dfx["close"].resample("1h").last().dropna()
    h1_idx = h1.index.values          # H1 bar timestamps (left edge)
    h1_vals = h1.values
    out = []
    i, n = WINDOW, len(candles)
    while i < n - 1:
        if (i - WINDOW) % STEP != 0:
            i += 1; continue
        window = candles[i - WINDOW:i]
        try:
            sig = generate_signal(bt.SIG_SYMBOL, window, prof, "5min")
        except Exception:
            i += 1; continue
        if sig.direction == "flat" or not sig.take_profits:
            i += 1; continue
        # live-faithful H1 trend: all H1 bars completed at/before the DECISION time ts[i-1],
        # capped to the last 320 (live outputsize).
        cutoff = ts[i]                       # signal acts on candles[i].open; H1 known up to here
        k = np.searchsorted(h1_idx, cutoff, side="right")
        h1_closes = pd.Series(h1_vals[max(0, k - 320):k])
        htf = _h1_trend(h1_closes)
        entry = candles[i].open
        sign = 1 if sig.direction == "long" else -1
        stop_dist = max(abs(sig.entry - sig.stop_loss), INST.min_stop_price, 2.0 * bt.COST_PRICE)
        sl_f = entry - sign * stop_dist
        min_tp1 = max(INST.min_tp1_price, 3.0 * bt.COST_PRICE)
        dists, prev = [], 0.0
        for j, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if j == 0 else prev + 0.5 * min_tp1)
            dists.append(d); prev = d
        tps_f = [entry + sign * d for d in dists]
        R = bt._simulate(entry, sl_f, tps_f, sig.direction, candles[i + 1:])
        out.append((R, sig.direction, htf))
        i += 5
    return out


def stats(rows):
    if not rows:
        return (0, float("nan"), 0.0, float("nan"))
    R = np.array([r[0] for r in rows], float)
    wr = float((R > 0).mean()) * 100
    return (len(R), wr, float(R.sum()), float(R.mean()))


def main():
    files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
    print(f"GATED WALK-FORWARD | pullback | cost {bt.COST_PRICE}px | step {STEP} | H1 live-soft gate\n")
    pooled_all, pooled_gated = [], []
    header = f"{'regime':<12} {'cohort':<14} {'n':>4} {'WR%':>6} {'sumR':>8} {'meanR':>7}"
    print(header); print("-" * len(header))
    for csv in files:
        name = os.path.basename(csv).replace("duka_", "").replace("_m5.csv", "")
        print(f"[..] scanning {name}", flush=True)
        df = _load_df(csv)
        rows = collect(df, _candles(df))
        print(f"[ok] {name}: {len(rows)} signals", flush=True)
        allrows = rows
        # live gate: block ONLY direct-opposite; flat & agree are kept
        gated = [r for r in rows if not (r[2] != "flat" and r[2] != r[1])]
        pooled_all += allrows; pooled_gated += gated
        for label, rr in (("a-ALL(raw)", allrows), ("b-H1gated", gated)):
            n, wr, s, m = stats(rr)
            print(f"{name:<12} {label:<14} {n:>4} {wr:>6.1f} {s:>+8.1f} {m:>+7.3f}")
        print()
    print("=" * len(header))
    for label, rr in (("a-ALL(raw)", pooled_all), ("b-H1gated", pooled_gated)):
        n, wr, s, m = stats(rr)
        print(f"{'POOLED':<12} {label:<14} {n:>4} {wr:>6.1f} {s:>+8.1f} {m:>+7.3f}")
    # how many raw signals did the H1 gate REMOVE, and what was their R?
    removed = [r for r in pooled_all if (r[2] != "flat" and r[2] != r[1])]
    n, wr, s, m = stats(removed)
    print(f"\nH1-gate REMOVED (direct counter-trend dip): n={n} WR={wr:.1f}% sumR={s:+.1f} meanR={m:+.3f}")
    # flat-H1 subset (kept by gate but not with-trend confirmed)
    flatrows = [r for r in pooled_all if r[2] == "flat"]
    agree = [r for r in pooled_all if r[2] != "flat" and r[2] == r[1]]
    for lbl, rr in (("H1-flat (kept)", flatrows), ("H1-agree (with-trend)", agree)):
        n, wr, s, m = stats(rr)
        print(f"{lbl:<24} n={n} WR={wr:.1f}% sumR={s:+.1f} meanR={m:+.3f}")


if __name__ == "__main__":
    main()
