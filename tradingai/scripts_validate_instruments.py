"""CROSS-INSTRUMENT validation of the PROVEN trend edge (secondentry) — the disciplined path to
'more trades' the owner approved: apply the ONE edge we trust to more markets, each validated on
its OWN fresh data at its OWN real cost, before anything is proposed for live.

For each instrument (M5 CSV pulled read-only from MT5), run secondentry across the history and report:
  pooled net-R / t / Deflated-Sharpe / MinTRL / Carver cost-drag, PLUS an IS(first 60%) vs
  OOS(most-recent 40%) split — an instrument only earns a proposal if the edge holds in the RECENT
  out-of-sample half (not just pooled). Native signal geometry, per-instrument cost from symbol_info.
Usage: python -u scripts_validate_instruments.py
"""
import json, os
from dataclasses import replace
import numpy as np, pandas as pd
from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt
import edge_stats as es

STEP = int(os.getenv("VI_STEP", "3")); WINDOW = bt.WINDOW
PARAMS = json.load(open("storage/inst_params.json"))   # {sym: {cost, min_stop, min_tp1, sig, csv}}


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    df = df.rename(columns={t: "ts"})
    return [Candle(dt=str(r["ts"])[:19], open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                   close=float(r["close"]), volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def collect(candles, cost, min_stop, min_tp1, sig_symbol):
    bt.COST_PRICE = cost
    prof = replace(PROFILES["moderate"], strategy_mode="secondentry")
    Rs = []; i, n = WINDOW, len(candles)
    while i < n - 1:
        if (i - WINDOW) % STEP != 0:
            i += 1; continue
        try:
            sig = generate_signal(sig_symbol, candles[i - WINDOW:i], prof, "5min")
        except Exception:
            i += 1; continue
        if sig.direction == "flat" or not sig.take_profits:
            i += 1; continue
        entry = candles[i].open; sign = 1 if sig.direction == "long" else -1
        stop = max(abs(sig.entry - sig.stop_loss), min_stop, 2.0 * cost)
        mt1 = max(min_tp1, 3.0 * cost); dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), mt1 if k == 0 else prev + 0.5 * mt1); dists.append(d); prev = d
        tps = [entry + sign * d for d in dists]
        r = bt._simulate(entry, entry - sign * stop, tps, sig.direction, candles[i + 1:])
        Rs.append((r, cost / stop)); i += 5
    return Rs


def _stats(rows):
    if len(rows) < 5:
        return None
    R = np.array([r for r, c in rows], float); C = np.array([c for r, c in rows], float)
    _, mu, sd, sr, _, _ = es._sharpe_moments(R); t = sr * np.sqrt(len(R))
    drag = C.mean() / (R + C).mean() if (R + C).mean() > 0 else float('inf')
    return dict(n=len(R), wr=(R > 0).mean() * 100, sumR=R.sum(), mu=mu, t=t, sr=sr,
                mtrl=es.min_trl(R, 0, 0.95), drag=drag)


print(f"CROSS-INSTRUMENT secondentry validation | native geometry | per-instrument cost | step {STEP}\n")
print(f"{'symbol':9} {'cost':>8} {'n':>5} {'WR%':>5} {'sumR':>7} {'meanR':>7} {'t':>6} {'drag':>6} "
      f"{'MinTRL':>7} | {'OOS(recent40%) meanR/t':>22}  verdict")
print("-" * 108)
summary = []
for sym, p in PARAMS.items():
    cds = _load(p["csv"])
    rows = collect(cds, p["cost"], p["min_stop"], p["min_tp1"], p["sig"])
    st = _stats(rows)
    if not st:
        print(f"{sym:9} {'—':>8} {len(rows):>5}  (too few signals)"); continue
    cut = int(len(rows) * 0.6)                                   # time-ordered IS/OOS split
    oos = _stats(rows[cut:])
    passes = (st["sumR"] > 0 and st["t"] >= 2.0 and st["drag"] < 0.33 and oos and oos["mu"] > 0)
    ostr = f"{oos['mu']:+.3f}/t{oos['t']:+.1f}" if oos else "n/a"
    dstr = f"{st['drag']*100:.0f}%" if np.isfinite(st['drag']) else "inf"
    print(f"{sym:9} {p['cost']:>8.5f} {st['n']:>5} {st['wr']:>5.0f} {st['sumR']:>+7.1f} {st['mu']:>+7.3f} "
          f"{st['t']:>+6.1f} {dstr:>6} {st['mtrl']:>7.0f} | {ostr:>22}  {'*** PASS' if passes else 'no'}")
    summary.append((sym, passes, st, oos))

winners = [s for s, ok, st, o in summary if ok]
print("\n" + "=" * 70)
print(f"PASS (positive, t>=2, cost-drag<33%, OOS-recent still +): {winners if winners else 'NONE'}")
print("A PASS = a candidate to PROPOSE adding (owner approves before any live change). "
      "Gold XAUUSD is the benchmark row. Same fresh-OOS discipline that killed the range edge.")
