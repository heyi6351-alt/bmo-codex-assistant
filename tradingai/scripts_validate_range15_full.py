"""CONFIRM the M15 Bollinger-reversion edge on the ENLARGED sample (user chose 'more data first').
Adds ~8 months of FRESH out-of-sample gold from MT5 (2025-08-29..2026-07-10) MINUS the chop2026
overlap window (2026-05-01..2026-06-30), bad-tick filtered, split into chronological thirds so
leave-one-regime-out has real folds. Same FIXED config (stop 1.0xATR, tp=mid-band, ADX<20).
Reports: MT5-OOS standalone (independent confirmation) AND combined with the 4 Dukascopy regimes.
Usage: python -u scripts_validate_range15_full.py
"""
import glob, os
import numpy as np, pandas as pd
from core.models import Candle
import mt5_backtest as bt
import edge_stats as es

bt.COST_PRICE = 0.20; COST = bt.COST_PRICE; INST = bt._INST
ADX_MAX, STOP_ATR = 20.0, 1.0
CHOP_OVL = ("2026-05-01", "2026-06-30")     # exclude: overlaps duka_chop2026


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    df = df.rename(columns={t: "ts"}); df["ts"] = df["ts"].astype(str)
    return df[["ts", "open", "high", "low", "close"]].reset_index(drop=True)


def _clean_ticks(df):
    """Drop bad-tick bars (>5% intrabar range or >5% jump from prev close) — kills the 5598 spike."""
    c = df["close"].to_numpy(float); o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float); l = df["low"].to_numpy(float)
    rng = (h - l) / np.maximum(c, 1e-9)
    jump = np.abs(np.concatenate([[0.0], np.diff(c)]) / np.maximum(c, 1e-9))
    keep = (rng < 0.05) & (jump < 0.05)
    return df[keep].reset_index(drop=True)


def _m15(df, k=3):
    out = []
    v = df[["ts", "open", "high", "low", "close"]].to_numpy(object)
    for j in range(0, len(v) - k, k):
        ch = v[j:j + k]
        out.append((ch[0][0], float(ch[0][1]), float(max(x[2] for x in ch)),
                    float(min(x[3] for x in ch)), float(ch[-1][4])))
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


def boot_ci(R, block=8, nboot=5000):
    m = len(R); rng = np.random.default_rng(7); nb = int(np.ceil(m / block)); mm = np.empty(nboot)
    for b in range(nboot):
        idx = (np.concatenate([np.arange(s, s + block) for s in rng.integers(0, m, nb)]) % m)[:m]
        mm[b] = R[idx].mean()
    return float(np.percentile(mm, 2.5)), float(np.percentile(mm, 97.5)), float((mm <= 0).mean())


# ---- build regime R-series ----
per = {}
for f in sorted(glob.glob("storage/duka_*_m5.csv")):
    if "test_week" in f:
        continue
    nm = os.path.basename(f).replace("duka_", "").replace("_m5.csv", "")
    per[nm] = collect(_m15(_clean_ticks(_load(f))))

# MT5 out-of-sample: filter, exclude chop overlap, split into chronological thirds
mt5 = _clean_ticks(_load("storage/mt5_xau_hist_m5.csv"))
mask = ~((mt5["ts"] >= CHOP_OVL[0]) & (mt5["ts"] <= CHOP_OVL[1] + " 23:59"))
mt5 = mt5[mask].reset_index(drop=True)
thirds = np.array_split(np.arange(len(mt5)), 3)
for i, idx in enumerate(thirds):
    seg = mt5.iloc[idx[0]:idx[-1] + 1].reset_index(drop=True)
    per[f"mt5oos_{i+1}"] = collect(_m15(seg))
    print(f"  mt5oos_{i+1}: {seg['ts'].iloc[0][:10]}..{seg['ts'].iloc[-1][:10]}  {len(per[f'mt5oos_{i+1}'])} trades")

DUKA = [k for k in per if not k.startswith("mt5oos")]
MT5 = [k for k in per if k.startswith("mt5oos")]

print("\nM15 BOLLINGER-REVERSION — CONFIRMATION on enlarged sample (fixed config)\n")
for k, R in per.items():
    if len(R) >= 5:
        print(f"  {k:12} n={len(R):>4}  sumR {R.sum():>+6.1f}  meanR {R.mean():>+.3f}  t {_sharpe(R)*np.sqrt(len(R)):>+.1f}")

R_mt5 = np.concatenate([per[k] for k in MT5])
R_all = np.concatenate([per[k] for k in per])
es.edge_report(R_mt5, "MT5 OUT-OF-SAMPLE ALONE (fresh ~8 months, independent)", n_variants=4)
lo, hi, p = boot_ci(R_mt5)
print(f"  block-boot 95% CI meanR: [{lo:+.3f},{hi:+.3f}] p(mean<=0)={p:.3f}  MinTRL {es.min_trl(R_mt5,0,0.95):.0f} vs n={len(R_mt5)}")

es.edge_report(R_all, "COMBINED (4 duka regimes + MT5 OOS)", n_variants=4)
lo2, hi2, p2 = boot_ci(R_all)
print(f"  block-boot 95% CI meanR: [{lo2:+.3f},{hi2:+.3f}] p(mean<=0)={p2:.3f}  MinTRL {es.min_trl(R_all,0,0.95):.0f} vs n={len(R_all)}")

print("\n--- Leave-one-regime-out across ALL sub-regimes (does meanR stay >0?) ---")
ok = 0
for drop in per:
    R = np.concatenate([v for k, v in per.items() if k != drop])
    ok += R.mean() > 0
    print(f"  drop {drop:12}: meanR {R.mean():>+.3f} (t {_sharpe(R)*np.sqrt(len(R)):>+.1f})  {'ok' if R.mean()>0 else 'NEG'}")

sig_mt5 = lo > 0; sig_all = lo2 > 0
print(f"\nVERDICT: MT5-OOS significant={sig_mt5} | COMBINED significant={sig_all} | LORO robust {ok}/{len(per)}")
print("CONFIRMED & buildable if: COMBINED CI excludes 0 AND MinTRL cleared AND LORO robust in a large majority.")
