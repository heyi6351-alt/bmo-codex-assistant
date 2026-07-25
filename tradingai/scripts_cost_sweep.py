"""COST-HYPOTHESIS test: does scaling the WHOLE trade geometry up (bigger stop AND targets, SAME R:R)
cut the cost drag enough to flip secondentry's NET edge clearly positive OOS?

Mechanism (Carver cost speed-limit): cost_R = spread/stop. Our M5 stops are so tight (~2.6px) that the
0.20px spread eats ~149% of the gross edge. Scaling stop+TP by m keeps the R:R identical but shrinks
cost_R by 1/m — at the cost of needing bigger price moves to resolve (fewer TP/stop hits in the window).
Net effect is empirical. We generate each signal ONCE, then re-simulate at each geometry multiplier.

Usage: python scripts_cost_sweep.py            (secondentry, all 4 regimes, m in 1..3)
Env: CS_STEP (scan step, default 3), CS_COST (px, default 0.20), CS_STRAT (default secondentry)
"""
from __future__ import annotations
import glob, os
from dataclasses import replace
import numpy as np, pandas as pd

from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt

bt.COST_PRICE = float(os.getenv("CS_COST", "0.20"))
STEP = int(os.getenv("CS_STEP", "3"))
STRAT = os.getenv("CS_STRAT", "secondentry")
WINDOW = bt.WINDOW
INST = bt._INST
MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]),
                   volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def collect(candles):
    """Per signal: base stop + tp distances, then simulate NET R at each geometry multiplier m."""
    prof = replace(PROFILES["moderate"], strategy_mode=STRAT)
    rows = {m: [] for m in MULTS}          # m -> list of (net_R, cost_R)
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
        base_stop = max(abs(sig.entry - sig.stop_loss), INST.min_stop_price, 2.0 * bt.COST_PRICE)
        min_tp1 = max(INST.min_tp1_price, 3.0 * bt.COST_PRICE)
        base_dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if k == 0 else prev + 0.5 * min_tp1)
            base_dists.append(d); prev = d
        future = candles[i + 1:]
        for m in MULTS:
            stop = base_stop * m
            tps = [entry + sign * d * m for d in base_dists]
            r = bt._simulate(entry, entry - sign * stop, tps, sig.direction, future)
            rows[m].append((r, bt.COST_PRICE / stop))
        i += 5
    return rows


def main():
    files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
    print(f"COST-GEOMETRY SWEEP | {STRAT} | cost {bt.COST_PRICE}px | step {STEP} | scaling stop+TP by m (R:R held)")
    agg = {m: [] for m in MULTS}
    per_regime = {}
    for csv in files:
        name = os.path.basename(csv).replace("duka_", "").replace("_m5.csv", "")
        rows = collect(_load(csv))
        per_regime[name] = rows
        for m in MULTS:
            agg[m] += rows[m]
    def line(tag, rows_by_m):
        print(f"\n### {tag}")
        print(f"  {'m(geom)':>7} {'n':>4} {'WR%':>5} {'netSumR':>8} {'meanNetR':>9} {'meanCostR':>9} {'grossR':>7} {'costDrag':>8}")
        for m in MULTS:
            a = np.array(rows_by_m[m], float)
            if not len(a):
                continue
            net, cost = a[:, 0], a[:, 1]
            gr = (net + cost).mean()
            drag = cost.mean() / gr if gr > 0 else float("inf")
            wr = (net > 0).mean() * 100
            flag = "  <-- cost OK" if drag < 1/3 else ""
            print(f"  {m:>7.1f} {len(a):>4} {wr:>5.0f} {net.sum():>+8.1f} {net.mean():>+9.3f} "
                  f"{cost.mean():>9.3f} {gr:>+7.3f} {drag*100:>7.0f}%{flag}")
    for name, rows in per_regime.items():
        line(name, rows)
    line("ALL REGIMES POOLED", agg)
    print("\nRead: if meanNetR rises with m and cost drag falls below 33% while WR/sumR stay positive, "
          "the cost hypothesis holds → a bigger-geometry (less-scalpy) secondentry is the profit lever.\n"
          "If meanNetR falls (bigger moves don't resolve in-window), scaling up does NOT help — costs are "
          "structural to M5 scalping and the honest move is fewer/higher-conviction trades, not wider ones.")


if __name__ == "__main__":
    main()
