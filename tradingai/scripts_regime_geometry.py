"""OUT-OF-SAMPLE validation of REGIME-AWARE TRADE GEOMETRY for secondentry.

Hypothesis (from the cost speed-limit finding): our M5 stops are so tight the spread eats the edge.
Scaling stop+TP up cuts cost drag — but ONLY helps in TRENDING regimes (big moves resolve wide targets);
in CHOP it hurts. So: detect trend-strength AT ENTRY (leak-free — ADX over the window UP TO the signal
bar, never using future data) and scale geometry m = M_TREND when ADX≥threshold else M_CHOP.

This harness runs three honest checks so we don't repeat the wide-stop-haircut mistake:
  A) Regime-aware vs baseline(m=1) vs naive(all m=2), per regime + pooled — net R, cost drag, WR.
  B) ROBUSTNESS grid (ADX threshold × M_TREND) — is the gain broad or a knife-edge overfit?
  C) DOLLAR-CURVE Monte-Carlo (block-bootstrap the compounded 2%-risk equity) — does regime-aware raise
     median terminal wealth WITHOUT raising the 95th-pct max drawdown? (R-sums are blind to this.)

Usage: python scripts_regime_geometry.py   (env: RG_STEP default 3, RG_COST 0.20)
"""
from __future__ import annotations
import glob, os
from dataclasses import replace
import numpy as np, pandas as pd

from analysis.signals import generate_signal
from analysis.indicators import to_dataframe, adx
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt

bt.COST_PRICE = float(os.getenv("RG_COST", "0.20"))
STEP = int(os.getenv("RG_STEP", "3"))
WINDOW = bt.WINDOW
INST = bt._INST
RISK_PCT = 0.02


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]),
                   volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def _resample_h1(window):
    """Approx H1 candles from an M5 window (12 M5 = 1 H1), full buckets only — leak-free."""
    h1 = []
    for j in range(0, len(window) - 11, 12):
        ch = window[j:j + 12]
        h1.append(Candle(dt=ch[0].dt, open=ch[0].open, high=max(c.high for c in ch),
                         low=min(c.low for c in ch), close=ch[-1].close,
                         volume=sum(c.volume for c in ch)))
    return h1


def collect(candles):
    """Per secondentry signal: (m5_adx, h1_adx, entry, sign, base_stop, base_tp_dists, dir, future).
    Both ADX values are leak-free (over the window UP TO the signal bar). Simulation is deferred so
    we can reuse across every (detector, threshold, m) combo without re-generating signals."""
    prof = replace(PROFILES["moderate"], strategy_mode="secondentry")
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
        try:
            m5_adx = float(adx(to_dataframe(window)).iloc[-1])        # M5 trend strength at entry
            h1 = _resample_h1(window)
            h1_adx = float(adx(to_dataframe(h1)).iloc[-1]) if len(h1) >= 15 else m5_adx  # H1 (less noisy)
        except Exception:
            i += 1; continue
        entry = candles[i].open
        sign = 1 if sig.direction == "long" else -1
        base_stop = max(abs(sig.entry - sig.stop_loss), INST.min_stop_price, 2.0 * bt.COST_PRICE)
        min_tp1 = max(INST.min_tp1_price, 3.0 * bt.COST_PRICE)
        dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if k == 0 else prev + 0.5 * min_tp1)
            dists.append(d); prev = d
        out.append((m5_adx, h1_adx, entry, sign, base_stop, dists, sig.direction, candles[i + 1:]))
        i += 5
    return out


def sim(rows, m_of):
    """Simulate each signal at geometry multiplier m_of(m5_adx, h1_adx). Returns (net_R, cost_R)."""
    netR, costR = [], []
    for m5_adx, h1_adx, entry, sign, base_stop, dists, direction, future in rows:
        m = m_of(m5_adx, h1_adx)
        stop = base_stop * m
        tps = [entry + sign * d * m for d in dists]
        netR.append(bt._simulate(entry, entry - sign * stop, tps, direction, future))
        costR.append(bt.COST_PRICE / stop)
    return np.array(netR, float), np.array(costR, float)


def stats(netR, costR):
    gr = (netR + costR).mean()
    return dict(n=len(netR), wr=(netR > 0).mean() * 100, sumR=netR.sum(), meanR=netR.mean(),
                drag=(costR.mean() / gr if gr > 0 else float("inf")))


def mc_dollar(netR, paths=3000, window=None, block=10, seed=7):
    """Block-bootstrap the compounded 2%-risk equity curve. Returns median terminal return% and
    the 95th-pct max drawdown% (the number to budget against)."""
    rng = np.random.default_rng(seed)
    m = len(netR); w = window or m
    nb = int(np.ceil(w / block)); terms, dds = [], []
    for _ in range(paths):
        starts = rng.integers(0, m, size=nb)
        path = np.concatenate([np.take(netR, range(s, s + block), mode="wrap") for s in starts])[:w]
        eq = np.cumprod(1.0 + RISK_PCT * path)
        terms.append((eq[-1] - 1) * 100)
        peak = np.maximum.accumulate(eq)
        dds.append(float((1 - eq / peak).max()) * 100)
    return dict(median_term=float(np.median(terms)), p95_dd=float(np.percentile(dds, 95)),
                calmar=float(np.median(terms) / max(1e-9, np.percentile(dds, 95))))


def main():
    files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
    print(f"REGIME-AWARE GEOMETRY validation | secondentry | cost {bt.COST_PRICE}px | step {STEP}")
    per = {os.path.basename(f).replace('duka_', '').replace('_m5.csv', ''): collect(_load(f)) for f in files}
    allrows = [r for rows in per.values() for r in rows]

    # a-priori rule: trend (ADX≥TH) → wide, chop → tight. Two detectors: M5 ADX vs (less-noisy) H1 ADX.
    TH, MT, MC = 24.0, 2.0, 1.0
    rule_m5 = lambda m5, h1: MT if m5 >= TH else MC
    rule_h1 = lambda m5, h1: MT if h1 >= TH else MC

    print(f"\n=== A) REGIME-AWARE (ADX≥{TH:g}→m={MT}, else m={MC}) vs baselines, per regime + pooled ===")
    print(f"{'regime':10} {'variant':16} {'n':>4} {'WR%':>5} {'sumR':>7} {'meanR':>7} {'drag':>6}")
    def row(tag, variant, rows_):
        for name, mof in variant:
            s = stats(*sim(rows_, mof))
            dr = f"{s['drag']*100:.0f}%" if np.isfinite(s['drag']) else "inf"
            print(f"{tag:10} {name:16} {s['n']:>4} {s['wr']:>5.0f} {s['sumR']:>+7.1f} {s['meanR']:>+7.3f} {dr:>6}")
    variants = [("baseline m=1", lambda m5, h1: 1.0), ("naive m=2", lambda m5, h1: 2.0),
                ("REGIME m5-adx", rule_m5), ("REGIME h1-adx", rule_h1)]
    for name, rows_ in per.items():
        row(name, variants, rows_)
    print("-" * 56)
    row("POOLED", variants, allrows)

    print(f"\n=== B) ROBUSTNESS grid (pooled net sumR), H1-ADX detector — broad or knife-edge? ===")
    print(f"  {'ADX_TH':>7} " + " ".join(f"m_tr={mt:<4}" for mt in (1.5, 2.0, 2.5)))
    base_sum = stats(*sim(allrows, lambda m5, h1: 1.0))["sumR"]
    print(f"  (baseline all-m=1 pooled sumR = {base_sum:+.1f})")
    for th in (20, 22, 24, 26, 28):
        cells = []
        for mt in (1.5, 2.0, 2.5):
            s = stats(*sim(allrows, (lambda m5, h1, _th=th, _mt=mt: _mt if h1 >= _th else 1.0)))
            cells.append(f"{s['sumR']:>+7.1f}")
        print(f"  {th:>7} " + " ".join(cells))

    print(f"\n=== C) DOLLAR-CURVE MONTE-CARLO (block-bootstrap, 2% compounding) ===")
    for name, mof in (("baseline m=1", lambda m5, h1: 1.0), ("REGIME h1-adx", rule_h1)):
        netR, _ = sim(allrows, mof)
        mc = mc_dollar(netR, window=300)
        print(f"  {name:14}: median terminal {mc['median_term']:>+6.1f}%  | 95th-pct maxDD {mc['p95_dd']:>5.1f}%  "
              f"| Calmar {mc['calmar']:.2f}")
    print("\nSHIP iff: REGIME-AWARE beats baseline in pooled+per-regime net R, the gain is BROAD across the grid "
          "(not one cell), AND the MC shows higher median wealth WITHOUT higher 95th-pct maxDD. Else DROP.")


if __name__ == "__main__":
    main()
