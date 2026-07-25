"""TIMEFRAME COST-EFFICIENCY test: does the second-entry edge SURVIVE COSTS better on a higher timeframe?

Root finding: on M5 the 0.20 spread eats ~149% of secondentry's gross edge because scalp stops are ~5px.
On H1 the SAME strategy has ~5-10x wider stops (ATR-based), so cost_R = spread/stop is ~1/5 — the edge
might survive net-of-cost where it drowns on M5. This is the principled version of the cost lever (trade
BIGGER, not tighter) and it's aligned with the research (fewer, higher-quality, cost-respecting trades).

We resample the M5 Dukascopy regimes to M15/H1 and run the SAME secondentry engine on each, at the SAME
0.20 spread, and compare NET R, cost drag, per-trade Sharpe, and the cost-speed-limit verdict.

Usage: python scripts_timeframe_edge.py     (env: TFE_STEP default 2, TFE_COST 0.20)
"""
from __future__ import annotations
import glob, os
from dataclasses import replace
import numpy as np, pandas as pd

from analysis.signals import generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt
import edge_stats as es

bt.COST_PRICE = float(os.getenv("TFE_COST", "0.20"))
STEP = int(os.getenv("TFE_STEP", "2"))
WINDOW = bt.WINDOW
INST = bt._INST
TFS = [("M5", 1, "5min"), ("M15", 3, "15min"), ("H1", 12, "1h")]


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]),
                   volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def _resample(m5, factor):
    if factor == 1:
        return m5
    out = []
    for j in range(0, len(m5) - factor + 1, factor):
        ch = m5[j:j + factor]
        out.append(Candle(dt=ch[0].dt, open=ch[0].open, high=max(c.high for c in ch),
                          low=min(c.low for c in ch), close=ch[-1].close,
                          volume=sum(c.volume for c in ch)))
    return out


def collect(candles, interval):
    """secondentry on `candles`: per trade (net_R, cost_R). Same geometry the live engine + backtest use."""
    prof = replace(PROFILES["moderate"], strategy_mode="secondentry")
    netR, costR = [], []
    i, n = WINDOW, len(candles)
    while i < n - 1:
        if (i - WINDOW) % STEP != 0:
            i += 1; continue
        window = candles[i - WINDOW:i]
        try:
            sig = generate_signal(bt.SIG_SYMBOL, window, prof, interval)
        except Exception:
            i += 1; continue
        if sig.direction == "flat" or not sig.take_profits:
            i += 1; continue
        entry = candles[i].open
        sign = 1 if sig.direction == "long" else -1
        stop = max(abs(sig.entry - sig.stop_loss), INST.min_stop_price, 2.0 * bt.COST_PRICE)
        min_tp1 = max(INST.min_tp1_price, 3.0 * bt.COST_PRICE)
        dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if k == 0 else prev + 0.5 * min_tp1)
            dists.append(d); prev = d
        tps = [entry + sign * d for d in dists]
        netR.append(bt._simulate(entry, entry - sign * stop, tps, sig.direction, candles[i + 1:]))
        costR.append(bt.COST_PRICE / stop)
        i += 5
    return np.array(netR, float), np.array(costR, float)


def main():
    files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
    print(f"TIMEFRAME COST-EFFICIENCY | secondentry | spread {bt.COST_PRICE}px | step {STEP}")
    print(f"\n{'TF':>4} {'n':>4} {'WR%':>5} {'netSumR':>8} {'meanNetR':>9} {'Sharpe':>7} {'grossR':>7} {'costDrag':>8} {'PSR':>5} {'MinTRL':>7}")
    for tf, factor, interval in TFS:
        allnet, allcost = [], []
        for f in files:
            m5 = _load(f)
            net, cost = collect(_resample(m5, factor), interval)
            allnet.append(net); allcost.append(cost)
        net = np.concatenate(allnet); cost = np.concatenate(allcost)
        if len(net) < 20:
            print(f"{tf:>4} {len(net):>4}  (too few signals to judge)"); continue
        gross = net + cost
        drag = cost.mean() / gross.mean() if gross.mean() > 0 else float("inf")
        _, mu, sd, sr, _, _ = es._sharpe_moments(net)
        psr = es.psr(net); trl = es.min_trl(net)
        dstr = f"{drag*100:.0f}%" if np.isfinite(drag) else "inf"
        tstr = f"{trl:.0f}" if np.isfinite(trl) else "inf"
        flag = "  <-- cost OK" if np.isfinite(drag) and drag < 1/3 else ""
        print(f"{tf:>4} {len(net):>4} {(net>0).mean()*100:>5.0f} {net.sum():>+8.1f} {mu:>+9.3f} "
              f"{sr:>+7.3f} {gross.mean():>+7.3f} {dstr:>8} {psr*100:>4.0f}% {tstr:>7}{flag}")
    print("\nRead: if a higher TF shows LOWER cost drag AND positive net R / Sharpe / PSR, the second-entry "
          "edge survives costs better there → develop an M15/H1 secondentry (validate OOS before shipping).\n"
          "If net R stays flat/negative on higher TFs, the edge is genuinely M5-specific and this isn't the lever.")


if __name__ == "__main__":
    main()
