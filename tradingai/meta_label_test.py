"""META-LABELING test (Lopez de Prado) — the DISCIPLINED validation before building it live.

The primary model (our secondentry strategy) picks the SIDE. A secondary "meta" model predicts
whether THAT signal will WIN (1) or lose (0) — a far more tractable question than raw direction
(which we already proved is a 0.51 coin-flip). If the meta-model's predicted win-probability
actually discriminates winners from losers OUT-OF-SAMPLE, we size by it. If not, we don't build it.

For each historical secondentry signal on a Dukascopy regime CSV we capture the market FEATURES at
signal time + the realised WIN/LOSS from the same _simulate the backtest uses, then walk-forward
(TimeSeriesSplit) evaluate: AUC + do the top-tercile predicted-prob signals win more than the bottom.

Usage:  python meta_label_test.py [regime]   (default: all 4 regimes; META_SCAN_STEP, META_COST env)
"""
from __future__ import annotations

import glob
import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from analysis.indicators import to_dataframe
from analysis.ml_predictor import _features
from analysis.signals import confluence, generate_signal
from core.models import Candle
from risk.profiles import PROFILES
import mt5_backtest as bt

bt.COST_PRICE = float(os.getenv("META_COST", "0.20"))
SCAN_STEP = int(os.getenv("META_SCAN_STEP", "4"))
WINDOW = bt.WINDOW
INST = bt._INST


def _load(csv: str) -> list[Candle]:
    df = pd.read_csv(csv); t = df.columns[0]
    return [Candle(dt=str(r[t])[:19], open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]),
                   volume=float(r.get("volume", 0) or 0)) for _, r in df.iterrows()]


def build_dataset(candles, strategy="secondentry", interval="5min"):
    prof = replace(PROFILES["moderate"], strategy_mode=strategy)
    X, y, cols = [], [], None
    i, n = WINDOW, len(candles)
    while i < n - 1:
        if (i - WINDOW) % SCAN_STEP != 0:
            i += 1; continue
        window = candles[i - WINDOW:i]
        try:
            sig = generate_signal(bt.SIG_SYMBOL, window, prof, interval)
        except Exception:
            i += 1; continue
        if sig.direction == "flat" or not sig.take_profits:
            i += 1; continue
        dfw = to_dataframe(window)
        b, _ = confluence(dfw, sig.indicators, sig.direction)
        f = _features(dfw)
        if cols is None:
            cols = list(f.columns) + ["sig_conf", "boosters"]
        row = f.iloc[-1]
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
        r = bt._simulate(entry, sl_f, tps_f, sig.direction, candles[i + 1:])
        X.append([float(row.get(c, 0.0)) for c in f.columns] + [float(sig.confidence), float(b)])
        y.append(1 if r > 0 else 0)
        i += 5
    return np.array(X, float), np.array(y, int), cols


def evaluate(name, X, y):
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score
    from analysis.ml_predictor import _make_model
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    if len(y) < 120 or len(set(y)) < 2:
        print(f"  {name}: too few samples ({len(y)})"); return
    base = y.mean()
    oof = np.full(len(y), np.nan)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        if len(set(y[tr])) < 2:
            continue
        m = _make_model(); m.fit(X[tr], y[tr])
        oof[te] = m.predict_proba(X[te])[:, list(m.classes_).index(1)]
    mask = ~np.isnan(oof)
    if mask.sum() < 60:
        print(f"  {name}: insufficient OOF"); return
    yy, pp = y[mask], oof[mask]
    try:
        auc = roc_auc_score(yy, pp)
    except Exception:
        auc = float("nan")
    # practical test: win rate of top vs bottom tercile of predicted win-prob
    order = np.argsort(pp)
    k = len(pp) // 3
    bot_wr = yy[order[:k]].mean() * 100
    top_wr = yy[order[-k:]].mean() * 100
    verdict = ("USEFUL -> size by it" if (auc >= 0.55 and top_wr - bot_wr >= 8)
               else "WEAK -> marginal" if auc >= 0.52 else "NO EDGE -> don't build")
    print(f"  {name:10s}: n={len(yy):4d} baseWin {base*100:.0f}% | AUC {auc:.3f} | "
          f"bottom-3rd win {bot_wr:.0f}% vs top-3rd win {top_wr:.0f}%  ({top_wr-bot_wr:+.0f}pp) | {verdict}")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    files = [f for f in sorted(glob.glob("storage/duka_*_m5.csv")) if "test_week" not in f]
    if which:
        files = [f for f in files if which in f]
    print(f"META-LABELING TEST | secondentry | cost {bt.COST_PRICE}px | scan_step {SCAN_STEP}")
    allX, allY = [], []
    for csv in files:
        name = os.path.basename(csv).replace("duka_", "").replace("_m5.csv", "")
        X, y, cols = build_dataset(_load(csv))
        evaluate(name, X, y)
        if len(y):
            allX.append(X); allY.append(y)
    if len(allX) > 1:
        evaluate("ALL", np.vstack(allX), np.concatenate(allY))
    print("DONE")


if __name__ == "__main__":
    main()
