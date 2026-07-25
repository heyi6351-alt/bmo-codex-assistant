"""VALIDATE the M15 Bollinger-reversion range lead (the probe's one promising result: +29.7R, drag
44%, 3/4 regimes). Is it a REAL range edge or noise? Full edge_report (MinTRL/PSR/DSR) + leave-one-
regime-out robustness + paired-vs-nothing block-bootstrap CI on pooled meanR. Same discipline that
killed session-weights. FIXED config (stop 1.0×ATR, tp=mean-band, ADX<20) — declared, not tuned.
Usage: python -u scripts_validate_range15.py
"""
import glob, os
import numpy as np, pandas as pd
from core.models import Candle
import mt5_backtest as bt
import edge_stats as es

bt.COST_PRICE = 0.20; COST = bt.COST_PRICE; INST = bt._INST
ADX_MAX, STOP_ATR = 20.0, 1.0


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    return df.rename(columns={t: "ts"}).assign(ts=lambda d: d["ts"].astype(str))


def _m15(df, k=3):
    out = []
    for j in range(0, len(df) - k, k):
        ch = df.iloc[j:j + k]
        out.append((ch["ts"].iloc[0], ch["open"].iloc[0], ch["high"].max(), ch["low"].min(), ch["close"].iloc[-1]))
    return pd.DataFrame(out, columns=["ts", "open", "high", "low", "close"])


def _atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)


def collect(df):
    close = df["close"]; mid = close.rolling(20).mean(); sd = close.rolling(20).std()
    up, lo = (mid + 2 * sd).to_numpy(), (mid - 2 * sd).to_numpy()
    atr = _atr(df).to_numpy(); adx = _adx(df).to_numpy(); midv = mid.to_numpy()
    o = df["open"].to_numpy(); c = close.to_numpy()
    cds = [Candle(dt=str(df["ts"].iloc[i]), open=float(df["open"].iloc[i]), high=float(df["high"].iloc[i]),
                  low=float(df["low"].iloc[i]), close=float(df["close"].iloc[i]), volume=0.0) for i in range(len(df))]
    Rs = []
    for i in range(25, len(df) - 2):
        if not np.isfinite(adx[i]) or adx[i] >= ADX_MAX or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        d = "long" if (c[i] < lo[i] and c[i - 1] >= lo[i - 1]) else ("short" if (c[i] > up[i] and c[i - 1] <= up[i - 1]) else None)
        if not d:
            continue
        sign = 1 if d == "long" else -1
        entry = o[i + 1]; stop_dist = max(STOP_ATR * atr[i], INST.min_stop_price, 2 * COST)
        sl = entry - sign * stop_dist
        tp = midv[i] if np.isfinite(midv[i]) and ((sign == 1 and midv[i] > entry) or (sign == -1 and midv[i] < entry)) else entry + sign * stop_dist
        tp = entry + sign * max(abs(tp - entry), max(INST.min_tp1_price, 3 * COST))
        Rs.append(bt._simulate(entry, sl, [tp], d, cds[i + 2:]))
    return np.array(Rs, float)


def _sharpe(R):
    R = np.asarray(R, float); sd = R.std(ddof=1)
    return R.mean() / sd if sd > 0 else 0.0


def boot_meanR_ci(R, block=8, nboot=5000):
    m = len(R); rng = np.random.default_rng(7)
    nb = int(np.ceil(m / block)); means = np.empty(nboot)
    for b in range(nboot):
        idx = (np.concatenate([np.arange(s, s + block) for s in rng.integers(0, m, nb)]) % m)[:m]
        means[b] = R[idx].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi), float((means <= 0).mean())


files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
per = {os.path.basename(f).replace('duka_', '').replace('_m5.csv', ''): collect(_m15(_load(f))) for f in files}
allR = np.concatenate(list(per.values()))

print("M15 BOLLINGER-REVERSION — VALIDATION (fixed config: stop 1.0×ATR, tp=mean, ADX<20)\n")
for rg, R in per.items():
    if len(R) >= 5:
        print(f"  {rg:11} n={len(R):>4}  sumR {R.sum():>+6.1f}  meanR {R.mean():>+.3f}  t {_sharpe(R)*np.sqrt(len(R)):>+.1f}")
es.edge_report(allR, "M15 Bollinger reversion (pooled, 4 regimes)", n_variants=4)

print("\n--- Leave-one-regime-out: does pooled meanR stay >0 dropping each regime? ---")
loro_ok = 0
for drop in per:
    R = np.concatenate([v for k, v in per.items() if k != drop])
    mu = R.mean(); loro_ok += mu > 0
    print(f"  drop {drop:11}: meanR {mu:>+.3f} (t {_sharpe(R)*np.sqrt(len(R)):>+.1f})  {'ok' if mu>0 else 'FLIPS NEG'}")

lo, hi, p = boot_meanR_ci(allR)
print(f"\n--- Block-bootstrap 95% CI on pooled meanR: [{lo:+.3f}, {hi:+.3f}]  p(mean≤0)={p:.3f} ---")
mtrl = es.min_trl(allR, 0.0, 0.95)
sig = (lo > 0) and loro_ok >= 3
print(f"\nVERDICT: {'PROMISING — significant & robust; build as a monitored range strategy + walk-forward' if sig else 'NOT YET — CI includes 0 or not robust; a LEAD, needs more data/refinement before live'}")
print(f"  (MinTRL {mtrl:.0f} vs n={len(allR)}; robust {loro_ok}/4; CI-excludes-0 {lo>0})")
