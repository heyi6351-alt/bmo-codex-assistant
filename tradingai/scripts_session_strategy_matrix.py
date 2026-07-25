"""ALL-SESSION COVERAGE research: which STRATEGY is +EV in which SESSION, and is it ROBUST?
Goal (owner): be powerful in EVERY session. One strategy can't — each session has a different
character (London=trend, Asian=range, NY=whipsaw/vol). Build the strategy x session edge matrix
across all 4 Dukascopy regimes, at live cost, using each strategy's NATIVE signal geometry
(sig.stop/TPs — fair cross-strategy comparison, like edge_walkforward). Report POOLED and
PER-REGIME robustness (the chop2026-overfit guard). Dukascopy ts = UTC.

Usage: python -u scripts_session_strategy_matrix.py   (env SSM_STEP default 3)
"""
import glob, os, sys
from dataclasses import replace
import numpy as np, pandas as pd
from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt
import edge_stats as es

bt.COST_PRICE = 0.20; STEP = int(os.getenv("SSM_STEP", "3")); WINDOW = bt.WINDOW; INST = bt._INST
# Only the ENABLED live strategies (disabled pullback/firstentry can't affect live sizing).
STRATS = ["secondentry", "trend", "breakout", "range_fade", "squeeze"]
SESS_ORDER = ["Asian", "London", "NY"]


def _sess(hour):
    if 7 <= hour < 13:  return "London"
    if 13 <= hour < 21: return "NY"
    return "Asian"


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                   close=float(r["close"]), volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def collect(candles, strat, regime):
    """Per signal: (regime, session, net_R). Native signal geometry (stop=|entry-sl|, TPs=sig tps)."""
    prof = replace(PROFILES["moderate"], strategy_mode=strat)
    rows = []
    i, n = WINDOW, len(candles)
    while i < n - 1:
        if (i - WINDOW) % STEP != 0:
            i += 1; continue
        try:
            sig = generate_signal(bt.SIG_SYMBOL, candles[i - WINDOW:i], prof, "5min")
        except Exception:
            i += 1; continue
        if sig.direction == "flat" or not sig.take_profits:
            i += 1; continue
        entry = candles[i].open; sign = 1 if sig.direction == "long" else -1
        stop = max(abs(sig.entry - sig.stop_loss), INST.min_stop_price, 2.0 * bt.COST_PRICE)
        min_tp1 = max(INST.min_tp1_price, 3.0 * bt.COST_PRICE)
        dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if k == 0 else prev + 0.5 * min_tp1)
            dists.append(d); prev = d
        tps = [entry + sign * d for d in dists]
        r = bt._simulate(entry, entry - sign * stop, tps, sig.direction, candles[i + 1:])
        rows.append((regime, _sess(int(candles[i].dt[11:13])), r))
        i += 5
    return rows


files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
regimes = {os.path.basename(f).replace('duka_', '').replace('_m5.csv', ''): _load(f) for f in files}
REG_NAMES = list(regimes.keys())
print(f"STRATEGY x SESSION edge matrix | 4 regimes | cost {bt.COST_PRICE}px | step {STEP}", flush=True)
print("cell = sumR / n / t ; robust column = #regimes with sumR>0 (of 4)\n", flush=True)

matrix = {}   # strat -> list[(regime, session, R)]
for strat in STRATS:
    allrows = []
    for name, cs in regimes.items():
        allrows += collect(cs, strat, name)
        print(f"  .. {strat} / {name}: {len(allrows)} cum signals", file=sys.stderr, flush=True)
    matrix[strat] = allrows

# Dump raw rows so the leave-one-regime-out validation reuses them (no 2nd long run).
dump = pd.DataFrame([(strat, rg, ss, r) for strat, rows in matrix.items() for rg, ss, r in rows],
                    columns=["strategy", "regime", "session", "R"])
dump.to_csv("scratchpad/ssm_rows.csv", index=False)
print(f"[dumped {len(dump)} rows -> scratchpad/ssm_rows.csv]\n", flush=True)

hdr = f"{'strategy':12}" + "".join(f"{s:>20}" for s in SESS_ORDER) + f"{'robust(+/4 per sess)':>26}"
print(hdr); print("-" * len(hdr))
for strat in STRATS:
    rows = matrix[strat]; cells = []; robust = []
    for s in SESS_ORDER:
        R = np.array([r for rg, ss, r in rows if ss == s], float)
        if len(R) < 10:
            cells.append(f"{'n='+str(len(R)):>20}"); robust.append("-"); continue
        _, mu, sd, sr, _, _ = es._sharpe_moments(R); t = sr * np.sqrt(len(R))
        tag = "**" if (R.sum() > 0 and t >= 1.5) else ("+" if R.sum() > 0 else "-")
        cells.append(f"{f'{R.sum():+.0f}/{len(R)}/t{t:+.1f}{tag}':>20}")
        pos = sum(1 for rg in REG_NAMES if sum(r for g, ss, r in rows if g == rg and ss == s) > 0)
        robust.append(str(pos))
    print(f"{strat:12}" + "".join(cells) + f"{'  '.join(f'{s[0]}:{r}' for s, r in zip(SESS_ORDER, robust)):>26}")

print("\n=== ALL-SESSION PORTFOLIO: which strategy OWNS each session (pooled + robustness) ===")
for s in SESS_ORDER:
    ranked = []
    for strat in STRATS:
        R = np.array([r for rg, ss, r in matrix[strat] if ss == s], float)
        if len(R) >= 20:
            _, mu, sd, sr, _, _ = es._sharpe_moments(R); t = sr * np.sqrt(len(R))
            pos = sum(1 for rg in REG_NAMES if sum(r for g, ss2, r in matrix[strat] if g == rg and ss2 == s) > 0)
            ranked.append((strat, R.sum(), len(R), t, pos))
    ranked.sort(key=lambda x: x[1], reverse=True)
    top = [f"{st}({sm:+.0f}R,t{t:+.1f},{pos}/4)" for st, sm, nn, t, pos in ranked[:3]]
    owner = next((st for st, sm, nn, t, pos in ranked if sm > 0 and t >= 1.0 and pos >= 3), None)
    verdict = f"OWNER = {owner} (robust +EV)" if owner else "GAP — no robust +EV strategy (research target)"
    print(f"  {s:7}: {'  '.join(top)}\n           -> {verdict}")
print("\nSESSION_WEIGHTS rule: weight UP a (session,strategy) only if pooled sumR>0, t>=1.0, AND positive in "
      ">=3/4 regimes. Weight DOWN the robustly-negative cells. Never zero (sizing, not gating). Validate OOS next.")
