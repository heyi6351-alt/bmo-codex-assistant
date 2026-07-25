"""RANGE-EDGE PROBE: does ANY canonical mean-reversion edge work on gold — and does WIDER (M15)
geometry beat cost, like the M15 secondentry win? The bot has NO working range-trading capability
(secondentry=trend; AI gates forbid fading levels; range_fade too strict). If ranges are tradeable
at all, a proper mean-reversion strategy on the right timeframe is the fix. Test Bollinger-band and
RSI-2 reversion, on M5 vs M15, only in low-ADX (range) bars, per regime, at LIVE cost. Report
net-R + cost drag (Carver 1/3 speed-limit). Dukascopy ts=UTC.  Usage: python -u scripts_range_edge_probe.py
"""
import glob, os, sys
import numpy as np, pandas as pd
from core.models import Candle
import mt5_backtest as bt
import edge_stats as es

bt.COST_PRICE = 0.20; COST = bt.COST_PRICE; INST = bt._INST


def _load(csv):
    df = pd.read_csv(csv); t = df.columns[0]
    df = df.rename(columns={t: "ts"})
    df["ts"] = df["ts"].astype(str)
    return df


def _resample(df, k):
    """M5 -> k-bar OHLC (k=1 M5, k=3 M15)."""
    out = []
    for j in range(0, len(df) - k, k):
        ch = df.iloc[j:j + k]
        out.append((ch["ts"].iloc[0], ch["open"].iloc[0], ch["high"].max(), ch["low"].min(),
                    ch["close"].iloc[-1]))
    return pd.DataFrame(out, columns=["ts", "open", "high", "low", "close"])


def _rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)


def sim_series(df, entries, stop_atr, tp_kind):
    """entries: list of (idx, dir). stop = stop_atr*ATR; tp = mid-band (mean) or 1R. Returns (Rs, costRs)."""
    atr = _atr(df).to_numpy(); mid = df["close"].rolling(20).mean().to_numpy()
    o = df["open"].to_numpy()
    cds = [Candle(dt=str(df["ts"].iloc[i]), open=float(df["open"].iloc[i]), high=float(df["high"].iloc[i]),
                  low=float(df["low"].iloc[i]), close=float(df["close"].iloc[i]), volume=0.0)
           for i in range(len(df))]
    Rs, cRs = [], []
    for idx, d in entries:
        if idx + 1 >= len(df) or not np.isfinite(atr[idx]) or atr[idx] <= 0:
            continue
        sign = 1 if d == "long" else -1
        entry = o[idx + 1] if idx + 1 < len(o) else df["close"].iloc[idx]
        stop_dist = max(stop_atr * atr[idx], INST.min_stop_price, 2 * COST)
        sl = entry - sign * stop_dist
        if tp_kind == "mean":
            tp = mid[idx] if np.isfinite(mid[idx]) else entry + sign * stop_dist
            if (sign == 1 and tp <= entry) or (sign == -1 and tp >= entry):
                tp = entry + sign * stop_dist
        else:
            tp = entry + sign * stop_dist            # 1R
        tp = entry + sign * max(abs(tp - entry), max(INST.min_tp1_price, 3 * COST))
        r = bt._simulate(entry, sl, [tp], d, cds[idx + 2:])
        Rs.append(r); cRs.append(COST / stop_dist)
    return np.array(Rs, float), np.array(cRs, float)


def bollinger_entries(df, adx_max=20):
    close = df["close"]; mid = close.rolling(20).mean(); sd = close.rolling(20).std()
    up, lo = mid + 2 * sd, mid - 2 * sd; adx = _adx(df).to_numpy()
    ent = []
    c = close.to_numpy(); u = up.to_numpy(); l = lo.to_numpy()
    for i in range(25, len(df) - 2):
        if not np.isfinite(adx[i]) or adx[i] >= adx_max:
            continue
        if c[i] < l[i] and c[i - 1] >= l[i - 1]:      # poke below lower band -> fade long
            ent.append((i, "long"))
        elif c[i] > u[i] and c[i - 1] <= u[i - 1]:    # poke above upper band -> fade short
            ent.append((i, "short"))
    return ent


def rsi2_entries(df, adx_max=25):
    r2 = _rsi(df["close"], 2).to_numpy(); adx = _adx(df).to_numpy()
    ent = []
    for i in range(20, len(df) - 2):
        if not np.isfinite(adx[i]) or adx[i] >= adx_max:
            continue
        if r2[i] < 5:
            ent.append((i, "long"))
        elif r2[i] > 95:
            ent.append((i, "short"))
    return ent


files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
regimes = {os.path.basename(f).replace('duka_', '').replace('_m5.csv', ''): _load(f) for f in files}


def report(name, tf_k, entry_fn, stop_atr, tp_kind):
    print(f"\n### {name} | {'M5' if tf_k==1 else 'M15'} | stop {stop_atr}×ATR tp={tp_kind}", flush=True)
    print(f"  {'regime':11} {'n':>5} {'WR%':>5} {'sumR':>7} {'meanR':>7} {'t':>6} {'drag':>6}", flush=True)
    allR, allC = [], []
    for rg, df5 in regimes.items():
        df = df5 if tf_k == 1 else _resample(df5, tf_k)
        ent = entry_fn(df)
        R, C = sim_series(df, ent, stop_atr, tp_kind)
        allR.append(R); allC.append(C)
        if len(R) >= 5:
            _, mu, sd, sr, _, _ = es._sharpe_moments(R); t = sr * np.sqrt(len(R))
            drag = C.mean() / (R + C).mean() if (R + C).mean() > 0 else float('inf')
            dstr = f"{drag*100:.0f}%" if np.isfinite(drag) else "inf"
            print(f"  {rg:11} {len(R):>5} {(R>0).mean()*100:>5.0f} {R.sum():>+7.1f} {mu:>+7.3f} {t:>+6.1f} {dstr:>6}", flush=True)
        else:
            print(f"  {rg:11} {len(R):>5}  (too few)", flush=True)
    R = np.concatenate(allR); C = np.concatenate(allC)
    if len(R) >= 10:
        _, mu, sd, sr, _, _ = es._sharpe_moments(R); t = sr * np.sqrt(len(R))
        drag = C.mean() / (R + C).mean() if (R + C).mean() > 0 else float('inf')
        print(f"  {'POOLED':11} {len(R):>5} {(R>0).mean()*100:>5.0f} {R.sum():>+7.1f} {mu:>+7.3f} {t:>+6.1f} "
              f"{(f'{drag*100:.0f}%' if np.isfinite(drag) else 'inf'):>6}", flush=True)


print(f"RANGE-EDGE PROBE | cost {COST}px | canonical mean-reversion, low-ADX only", flush=True)
for tf_k in (1, 3):                       # M5, then M15
    report("Bollinger reversion", tf_k, bollinger_entries, 1.0, "mean")
    report("RSI-2 extreme fade", tf_k, rsi2_entries, 1.5, "mean")
print("\nRead: a REAL range edge = pooled meanR>0 with t≥~2 AND cost drag <33% (Carver). "
      "Watch if M15 beats M5 on drag (the proven wider-geometry lever). If nothing clears it, gold ranges "
      "aren't profitably tradeable at our cost — the bot is RIGHT to wait, and 'more trades' would mean losses.")
