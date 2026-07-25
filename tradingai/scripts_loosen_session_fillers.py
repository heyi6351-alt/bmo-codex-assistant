"""THE REAL ALL-SESSION LEVER: can loosening range_fade (Asian mean-reversion) and squeeze
(NY vol-breakout) make them FIRE enough to cover their sessions — WITHOUT killing the edge?
The strategy x session matrix showed range_fade fired 16x and squeeze 42x across 4 whole regimes:
too rare to cover Asian/NY. Their filters are ~7 simultaneous ANDs. Sweep looseness levels; for
each, report per-session fire-count + net-R + per-regime robustness. A level is a CANDIDATE only if
it fires enough AND stays positive AND robust (>=3/4 regimes) in its target session.
Live cost, native signal geometry. Dukascopy ts = UTC.  Usage: python -u scripts_loosen_session_fillers.py
"""
import glob, os, sys
from dataclasses import replace
import numpy as np, pandas as pd
import analysis.signals as S
from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt
import edge_stats as es

bt.COST_PRICE = 0.20; STEP = int(os.getenv("LSF_STEP", "3")); WINDOW = bt.WINDOW; INST = bt._INST
SESS = ["Asian", "London", "NY"]

# looseness presets: patch the module constants the setups read at call-time
RF_LEVELS = {
    "current": dict(RF_ADX_MAX=18.0, RF_ADX_RISE_MAX=2.0, RF_BBW_Q=0.34, RF_MIN_TOUCHES=2, RF_MIN_RANGE_ATR=1.5, RF_POKE_ATR=0.10),
    "medium":  dict(RF_ADX_MAX=22.0, RF_ADX_RISE_MAX=3.0, RF_BBW_Q=0.50, RF_MIN_TOUCHES=2, RF_MIN_RANGE_ATR=1.2, RF_POKE_ATR=0.15),
    "loose":   dict(RF_ADX_MAX=26.0, RF_ADX_RISE_MAX=4.0, RF_BBW_Q=0.70, RF_MIN_TOUCHES=1, RF_MIN_RANGE_ATR=1.0, RF_POKE_ATR=0.25),
}
SQ_LEVELS = {
    "current": dict(SQ_VOL_MULT=1.5, SQ_KC_ATR=1.5, SQ_BB_STD=2.0),
    "medium":  dict(SQ_VOL_MULT=1.2, SQ_KC_ATR=1.75, SQ_BB_STD=2.0),
    "loose":   dict(SQ_VOL_MULT=1.0, SQ_KC_ATR=2.0, SQ_BB_STD=1.9),
}


def _sess(h):
    return "London" if 7 <= h < 13 else ("NY" if 13 <= h < 21 else "Asian")


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                   close=float(r["close"]), volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def _patch(level):
    for k, v in level.items():
        setattr(S, k, v)


def collect(candles, strat, regime):
    prof = replace(PROFILES["moderate"], strategy_mode=strat)
    rows = []; i, n = WINDOW, len(candles)
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
        rows.append((regime, _sess(int(candles[i].dt[11:13])), r)); i += 5
    return rows


files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
regimes = {os.path.basename(f).replace('duka_', '').replace('_m5.csv', ''): _load(f) for f in files}
REG = list(regimes.keys())


def run(strat, levels, target):
    print(f"\n### {strat}  (target session = {target})  ── loosen until it fires enough & stays +EV/robust", flush=True)
    print(f"  {'level':8} {'fires':>6} " + "".join(f"{s+'(sumR/n/t/rob)':>22}" for s in SESS), flush=True)
    for lvl, cfg in levels.items():
        _patch(cfg)
        allrows = []
        for name, cs in regimes.items():
            allrows += collect(cs, strat, name)
            print(f"     .. {strat}/{lvl}/{name}: {len(allrows)} cum", file=sys.stderr, flush=True)
        cells = []
        for s in SESS:
            R = np.array([r for rg, ss, r in allrows if ss == s], float)
            if len(R) < 8:
                cells.append(f"{'n='+str(len(R)):>22}"); continue
            _, mu, sd, sr, _, _ = es._sharpe_moments(R); t = sr * np.sqrt(len(R))
            rob = sum(1 for rg in REG if sum(r for g, ss, r in allrows if g == rg and ss == s) > 0)
            cells.append(f"{f'{R.sum():+.0f}/{len(R)}/t{t:+.1f}/{rob}·4':>22}")
        print(f"  {lvl:8} {len(allrows):>6} " + "".join(cells), flush=True)
    _patch({k: RF_LEVELS['current'].get(k, SQ_LEVELS['current'].get(k)) for k in cfg})  # restore


run("range_fade", RF_LEVELS, "Asian")
run("squeeze", SQ_LEVELS, "NY")
print("\nCANDIDATE = a looseness where the TARGET session has enough fires (n≥~40), sumR>0, t≥~1.5, robust ≥3/4.")
print("If even 'loose' can't clear that, our template can't cover the session — needs a NEW dedicated strategy, not a knob.")
