"""EDGE STATISTICS INSTRUMENT — "measure, don't tinker" (playbook 2026-07-09).

Turns a per-trade R-multiple series into the numbers that tell us whether the edge is REAL and
what drawdowns are NORMAL — so we stop judging by cumulative R and stop tightening in drawdowns.

Implements (per-trade units, NEVER annualized — Bailey & López de Prado):
  • t-stat + one-sided p on mean(R)>0
  • Probabilistic Sharpe Ratio PSR(0) with skew/kurtosis correction
  • Minimum Track Record Length MinTRL(0.95) — trades needed to confirm the edge at 95%
  • Expected longest losing streak = ln(N)/−ln(1−WR)
  • Monte-Carlo (block-bootstrap) drawdown envelope: the 95th/99th-pct max drawdown you must EXPECT

Sources: Bailey & LdP "The Sharpe Ratio Efficient Frontier" (PSR/MinTRL) + "The Deflated Sharpe Ratio";
Magdon-Ismail (max drawdown of a diffusion); Schilling (longest run).

Usage:
  python edge_stats.py                 # live trade log (storage/mt5_trades.csv), per strategy + core
  python edge_stats.py --strat secondentry
Feed a walk-forward OOS R-series for the AUTHORITATIVE read (the live log is a small, confounded sample).
"""
from __future__ import annotations
import argparse, math
import numpy as np
import pandas as pd
from scipy import stats as ss

Z95 = 1.6448536269514722   # one-sided 95%


def _sharpe_moments(R: np.ndarray):
    n = len(R)
    mu, sd = R.mean(), R.std(ddof=1)
    sr = mu / sd if sd > 0 else 0.0            # per-trade Sharpe
    skew = ss.skew(R) if n > 2 else 0.0
    kurt = ss.kurtosis(R, fisher=False) if n > 3 else 3.0   # NON-excess (normal=3)
    return n, mu, sd, sr, skew, kurt


def psr(R: np.ndarray, benchmark_sr: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio: P(true per-trade Sharpe > benchmark)."""
    n, _, _, sr, skew, kurt = _sharpe_moments(R)
    if n < 3:
        return float("nan")
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr))
    z = (sr - benchmark_sr) * math.sqrt(n - 1) / denom
    return float(ss.norm.cdf(z))


def min_trl(R: np.ndarray, benchmark_sr: float = 0.0, conf: float = 0.95) -> float:
    """Minimum Track Record Length: #trades needed for PSR(benchmark) ≥ conf."""
    n, _, _, sr, skew, kurt = _sharpe_moments(R)
    if sr <= benchmark_sr:
        return float("inf")
    z = ss.norm.ppf(conf)
    return 1.0 + (1 - skew * sr + (kurt - 1) / 4.0 * sr * sr) * (z / (sr - benchmark_sr)) ** 2


def expected_longest_loss_run(n: int, wr: float) -> float:
    if wr >= 1 or wr <= 0:
        return float("nan")
    return math.log(n) / -math.log(1 - wr)


def _max_drawdown_R(R: np.ndarray) -> float:
    """Max drawdown of the cumulative (flat-stake) R equity curve, in R."""
    eq = np.cumsum(R)
    peak = np.maximum.accumulate(eq)
    return float((peak - eq).max())


def mc_drawdown_envelope(R: np.ndarray, window: int, paths: int = 20000,
                         block: int = 10, risk_pct: float = 0.02, seed_offset: int = 0):
    """Block-bootstrap `window`-trade paths; return the distribution of max drawdown,
    both in R (flat stake) and % (compounded at risk_pct). Block bootstrap preserves streaks."""
    rng = np.random.default_rng(1234 + seed_offset)
    m = len(R)
    dd_R, dd_pct, streaks = [], [], []
    nblocks = int(np.ceil(window / block))
    for _ in range(paths):
        starts = rng.integers(0, m, size=nblocks)
        path = np.concatenate([np.take(R, range(s, s + block), mode="wrap") for s in starts])[:window]
        dd_R.append(_max_drawdown_R(path))
        # compounded equity: each trade multiplies equity by (1 + risk_pct * R)
        eq = np.cumprod(1.0 + risk_pct * path)
        peak = np.maximum.accumulate(eq)
        dd_pct.append(float((1 - eq / peak).max()) * 100)
        # longest loss streak
        loss = path < 0
        best = cur = 0
        for x in loss:
            cur = cur + 1 if x else 0
            best = max(best, cur)
        streaks.append(best)
    q = lambda a, p: float(np.percentile(a, p))
    return {
        "dd_R":   {"median": q(dd_R, 50), "p95": q(dd_R, 95), "p99": q(dd_R, 99)},
        "dd_pct": {"median": q(dd_pct, 50), "p95": q(dd_pct, 95), "p99": q(dd_pct, 99), "worst": max(dd_pct)},
        "streak": {"median": q(streaks, 50), "p95": q(streaks, 95), "p99": q(streaks, 99), "worst": max(streaks)},
    }


def edge_report(R: np.ndarray, label: str = "", n_variants: int = 1, mc_window: int | None = None):
    R = np.asarray(R, float)
    R = R[np.isfinite(R)]
    n = len(R)
    print(f"\n{'='*72}\n  EDGE REPORT — {label}   (n={n} trades)\n{'='*72}")
    if n < 30:
        print(f"  n={n} < 30 — below CLT; treat everything below as indicative only.")
    if n < 3:
        print("  too few trades."); return
    _, mu, sd, sr, skew, kurt = _sharpe_moments(R)
    wr = float((R > 0).mean())
    t = sr * math.sqrt(n)
    p1 = 1 - ss.norm.cdf(t)
    _psr = psr(R, 0.0)
    _mtrl = min_trl(R, 0.0, 0.95)
    print(f"  mean {mu:+.4f}R | std {sd:.3f}R | win% {wr*100:.1f} | per-trade Sharpe {sr:.4f} | skew {skew:+.2f} kurt {kurt:.2f}")
    print(f"  t-stat {t:+.2f}  (one-sided p={p1:.3f})  |  PSR(0) {_psr*100:.1f}%  |  sum {R.sum():+.1f}R")
    print(f"  Min Track Record Length (95%): {_mtrl:.0f} trades  →  need ~{max(0, _mtrl-n):.0f} MORE  "
          f"({'CONFIRMED' if _psr>=0.95 else 'not yet — '+f'{_psr*100:.0f}%'})")
    # Deflated-Sharpe intuition: bar rises with how many variants you tried
    if n_variants > 1:
        sr_std = sd / math.sqrt(2 * n)              # ~SE of the Sharpe estimate (per-trade proxy)
        g = 0.5772156649
        sr0 = sr_std * ((1 - g) * ss.norm.ppf(1 - 1.0 / n_variants)
                        + g * ss.norm.ppf(1 - 1.0 / (n_variants * math.e)))
        print(f"  Deflated-Sharpe bar SR0(N={n_variants} variants) ≈ {sr0:.4f} vs observed {sr:.4f}  "
              f"→ {'PASSES' if sr>sr0 else 'FAILS (edge indistinguishable from best-of-noise)'}")
    print(f"  Expected longest loss streak at n={n}: {expected_longest_loss_run(n, wr):.1f}")
    w = mc_window or max(200, n)
    env = mc_drawdown_envelope(R, window=w)
    print(f"  --- Monte-Carlo NORMAL-VARIANCE envelope (block-bootstrap, {w}-trade window) ---")
    print(f"      longest loss streak : median {env['streak']['median']:.0f} | 95th {env['streak']['p95']:.0f} | 99th {env['streak']['p99']:.0f} | worst {env['streak']['worst']:.0f}")
    print(f"      max DD (flat, R)    : median {env['dd_R']['median']:.1f}R | 95th {env['dd_R']['p95']:.1f}R | 99th {env['dd_R']['p99']:.1f}R")
    print(f"      max DD (2% comp, %) : median {env['dd_pct']['median']:.1f}% | 95th {env['dd_pct']['p95']:.1f}% | 99th {env['dd_pct']['p99']:.1f}%")
    print(f"      → KILL-TRIGGER proxy: pause ONLY if live DD > 99th-pct ({env['dd_pct']['p99']:.0f}% / {env['dd_R']['p99']:.0f}R) AND rolling-200 t-stat < 0")
    return {"n": n, "sharpe": sr, "t": t, "psr0": _psr, "min_trl": _mtrl, "wr": wr, "env": env}


def cost_speed_limit(net_R, cost_R):
    """Carver's cost speed-limit (qoppac.blogspot.com/2020/04/how-fast-should-we-trade.html):
    costs must eat < 1/3 of the GROSS edge, else the strategy is trading too fast for its edge.
    `cost_R` = per-trade round-trip cost expressed in R (spread_price / stop_distance_price).
    Returns (cost_drag_fraction, mean_gross_R, mean_net_R, verdict)."""
    net_R = np.asarray(net_R, float)
    cost_R = np.asarray(cost_R, float)
    gross = net_R + cost_R
    mg = float(gross.mean())
    mc = float(cost_R.mean())
    drag = mc / mg if mg > 0 else float("inf")
    verdict = ("OK (< 1/3)" if drag < 1 / 3 else
               "OVER — costs eat >1/3 of the gross edge; trading too fast / stops too tight for the spread")
    return drag, mg, float(net_R.mean()), verdict


def _r_from_live_log(path: str, contract_size: float = 100.0, cost_price: float = 0.20) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["strategy"] = df["combo"].str.split("/").str[0]
    df["stop_px"] = (df["entry"] - df["sl"]).abs()
    df["risk_usd"] = df["stop_px"] * df["lots"] * contract_size
    df = df[(df["risk_usd"] > 0) & (df["stop_px"] > 0)].copy()
    df["R"] = df["profit"] / df["risk_usd"]                 # NET R (fills already include spread)
    df["cost_R"] = cost_price / df["stop_px"]               # round-trip spread as a fraction of the stop
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="storage/mt5_trades.csv")
    ap.add_argument("--strat", default="", help="restrict to one strategy (e.g. secondentry)")
    ap.add_argument("--variants", type=int, default=1, help="honest count of rule variants tried (Deflated Sharpe)")
    ap.add_argument("--cs", type=float, default=100.0, help="contract size (gold=100)")
    ap.add_argument("--cost", type=float, default=0.20, help="round-trip cost in price (gold live ~0.20)")
    args = ap.parse_args()
    df = _r_from_live_log(args.log, args.cs, args.cost)
    # sanity: full-stop losses should cluster near R=-1
    losers = df[df.R < 0]["R"]
    print(f"LIVE LOG {args.log}: {len(df)} trades with a stop | median loser R={losers.median():.2f} (sanity: ~−1 ⇒ contract size ok)")

    def _cost_line(sub, name):
        drag, gr, net, verdict = cost_speed_limit(sub["R"].values, sub["cost_R"].values)
        print(f"  COST SPEED-LIMIT [{name}]: gross {gr:+.3f}R − cost {sub['cost_R'].mean():.3f}R = net {net:+.3f}R "
              f"| drag {drag*100:.0f}% of gross → {verdict}")

    if args.strat:
        sub = df[df.strategy == args.strat]
        edge_report(sub["R"].values, f"{args.strat} (LIVE)", args.variants); _cost_line(sub, args.strat)
    else:
        core = df[df.strategy != "secondentry1"]        # exclude the proven-harmful M1 scalp
        se = df[df.strategy == "secondentry"]
        edge_report(se["R"].values, "secondentry (LIVE)", args.variants); _cost_line(se, "secondentry")
        edge_report(core["R"].values, "CORE book ex-M1-scalp (LIVE)", args.variants); _cost_line(core, "core")
    print("\nNOTE: the live log is a SMALL, confounded sample. The AUTHORITATIVE read is the "
          "walk-forward OOS R-series (wire it via edge_report(R_oos, ...)).")


if __name__ == "__main__":
    main()
