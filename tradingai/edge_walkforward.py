"""AUTHORITATIVE edge read — runs edge_stats on the WALK-FORWARD OOS R-series.

secondentry is a FIXED rule (no fitted parameters) so every trade across the 4 Dukascopy regimes is
effectively out-of-sample — this is the ~500-600-trade series the playbook says to judge on, NOT the
small confounded live log. Produces the real PSR / MinTRL / drawdown envelope that the kill-trigger uses.

Usage:  python edge_walkforward.py [strategy ...]     (default: secondentry)
Env:    EWF_STEP (scan sampling, default 3), EWF_COST (px, default 0.20 live cost)
"""
from __future__ import annotations
import glob, os, sys
from dataclasses import replace
import numpy as np, pandas as pd

from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt
import edge_stats

bt.COST_PRICE = float(os.getenv("EWF_COST", "0.20"))
STEP = int(os.getenv("EWF_STEP", "3"))
WINDOW = bt.WINDOW
INST = bt._INST


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]),
                   volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def collect_R(candles, strategy):
    """Per-trade realised R for `strategy` on one regime (same geometry the live engine + backtest use)."""
    prof = replace(PROFILES["moderate"], strategy_mode=strategy)
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
        entry = candles[i].open
        sign = 1 if sig.direction == "long" else -1
        stop_dist = max(abs(sig.entry - sig.stop_loss), INST.min_stop_price, 2.0 * bt.COST_PRICE)
        sl_f = entry - sign * stop_dist
        min_tp1 = max(INST.min_tp1_price, 3.0 * bt.COST_PRICE)
        dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if k == 0 else prev + 0.5 * min_tp1)
            dists.append(d); prev = d
        tps_f = [entry + sign * d for d in dists]
        out.append(bt._simulate(entry, sl_f, tps_f, sig.direction, candles[i + 1:]))
        i += 5
    return out


def main():
    strats = sys.argv[1:] or ["secondentry"]
    files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
    print(f"WALK-FORWARD OOS EDGE READ | cost {bt.COST_PRICE}px | step {STEP} | regimes {len(files)}")
    for strat in strats:
        allR = []
        per = {}
        for csv in files:
            name = os.path.basename(csv).replace("duka_", "").replace("_m5.csv", "")
            R = collect_R(_load(csv), strat)
            per[name] = (len(R), float(np.sum(R)))
            allR += R
        print(f"\n### {strat}: per-regime (n, sumR): " + "  ".join(f"{k}={v[0]}/{v[1]:+.1f}R" for k, v in per.items()))
        # honest variant count: we've A/B'd several tweaks on secondentry (room gate, breakeven, wide-stop,
        # confluence, stop cap...) — count them so the Deflated-Sharpe bar is honest.
        edge_stats.edge_report(np.array(allR, float), f"{strat} (WALK-FORWARD OOS, all regimes)",
                               n_variants=int(os.getenv("EWF_VARIANTS", "8")), mc_window=300)


if __name__ == "__main__":
    main()
