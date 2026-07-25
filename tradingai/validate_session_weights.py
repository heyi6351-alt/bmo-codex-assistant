"""LEAVE-ONE-REGIME-OUT validation of SESSION-aware strategy weighting — RIGOROUS version.
Question: if we size each (session, strategy) by its edge, does that REALLOCATION improve
out-of-sample risk-adjusted return — or is it in-sample overfit (like the refuted per-hour filter)?

Strengthened per the 2026-07-10 deep-research verify pass (6 sourced claims that survived
adversarial checking). The old harness only SIGN-COUNTED ΔSharpe across 4 folds — a 3/4 vote is
NOT significant (binomial p=0.3125). This version adds the three gates the research demanded:

  1. MinTRL PER-CELL GATE (Bailey & LdP): a (session,strategy) cell may receive a NON-1.0 weight
     ONLY if it has enough trades to even assert its Sharpe sign at 95% (edge_stats.min_trl).
     Thin cells (range_fade n=16, squeeze n=42, NY breakout n=11) FREEZE at 1.0 — no betting on noise.
  2. PAIRED STATIONARY-BLOCK-BOOTSTRAP ΔSharpe CI (Ledoit-Wolf 2008 spirit; block≈streak): the
     ship statistic is the held-out ΔSharpe(weighted − equal), with a 95% CI that must EXCLUDE 0.
  3. MAX-DD GUARD (Magdon-Ismail / edge_stats.mc_drawdown_envelope): the weighted book must not
     worsen the p99 max-drawdown envelope vs equal-weight, in ANY held-out regime.

Shape params G,T0,LO,HI are FIXED PRIORS (modest, bounded), declared a-priori — NOT fitted to the
OOS result (that would leak all 4 regimes into the verdict). Documented as fixed, per the research.

Reads scratchpad/ssm_rows.csv (dumped by scripts_session_strategy_matrix.py).
"""
import numpy as np, pandas as pd
import edge_stats as es

# ── FIXED PRIORS (declared a-priori; do NOT tune these to the OOS result) ──
G, T0, LO, HI = 0.35, 1.5, 0.55, 1.35     # weight = clip(1 + G*tanh(t/T0), LO, HI)
CELL_FLOOR_N = 30                          # hard floor before a cell is even considered
BLOCK, N_BOOT = 10, 3000                   # stationary block bootstrap
DD_WINDOW = 250

df = pd.read_csv("scratchpad/ssm_rows.csv")
REGIMES = sorted(df["regime"].unique())


def _sharpe(R):
    R = np.asarray(R, float); sd = R.std(ddof=1)
    return R.mean() / sd if sd > 0 else 0.0


def cell_weight(sub):
    """Weight for one (session,strategy) cell, from TRAINING rows only.
    Freezes at 1.0 unless the cell clears its own MinTRL (can assert its Sharpe SIGN at 95%)."""
    R = sub["R"].to_numpy(float)
    n = len(R)
    if n < CELL_FLOOR_N:
        return 1.0, "n<floor"
    mean = R.mean()
    mtrl = es.min_trl(R if mean > 0 else -R, 0.0, 0.95)   # trades needed to assert this sign
    if not np.isfinite(mtrl) or n < mtrl:
        return 1.0, f"MinTRL {mtrl:.0f}>n={n}"             # not enough evidence → no bet
    _, _, _, sr, _, _ = es._sharpe_moments(R)
    t = sr * np.sqrt(n)
    return float(np.clip(1.0 + G * np.tanh(t / T0), LO, HI)), f"t={t:+.1f},n={n}"


def train_weights(train_df, verbose=False):
    w = {}
    for (sess, strat), sub in train_df.groupby(["session", "strategy"]):
        wt, why = cell_weight(sub)
        w[(sess, strat)] = wt
        if verbose and abs(wt - 1.0) > 0.02:
            print(f"     weight {sess:7} {strat:12} = {wt:.2f}  ({why})")
    return w


def apply_weights(sample_df, weights):
    R = sample_df["R"].to_numpy(float)
    wv = np.array([weights.get((s, st), 1.0) for s, st in zip(sample_df["session"], sample_df["strategy"])], float)
    wv = wv / wv.mean()                                   # normalize: equal TOTAL risk = pure reallocation
    return R, wv * R


def paired_block_boot_dsharpe(eq_R, wt_R, seed=0):
    """95% CI + one-sided p for ΔSharpe = Sharpe(weighted) − Sharpe(equal), paired stationary block bootstrap."""
    m = len(eq_R)
    if m < 40:
        return None
    obs = _sharpe(wt_R) - _sharpe(eq_R)
    rng = np.random.default_rng(1234 + seed)
    nblk = int(np.ceil(m / BLOCK))
    deltas = np.empty(N_BOOT)
    for b in range(N_BOOT):
        starts = rng.integers(0, m, size=nblk)
        idx = (np.concatenate([np.arange(s, s + BLOCK) for s in starts]) % m)[:m]
        deltas[b] = _sharpe(wt_R[idx]) - _sharpe(eq_R[idx])
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    p_le0 = float((deltas <= 0).mean())                   # one-sided p for H0: Δ<=0
    return obs, float(lo), float(hi), p_le0


def dd_p99(R, seed=0):
    if len(R) < 40:
        return float("nan")
    return es.mc_drawdown_envelope(np.asarray(R, float), window=DD_WINDOW, paths=4000, seed_offset=seed)["dd_R"]["p99"]


print(f"RIGOROUS session-weight validation | shape w=clip(1+{G}·tanh(t/{T0}),{LO},{HI}) FIXED PRIOR")
print(f"gates: MinTRL per-cell + paired block-boot ΔSharpe 95% CI + p99 max-DD guard | boot={N_BOOT} block={BLOCK}\n")
print(f"{'held-out':11} {'n':>5} {'ΔSharpe':>8} {'95% CI':>18} {'p(Δ≤0)':>7} {'DDeq→DDwt(R,p99)':>18}  verdict")
print("-" * 96)
folds_pos = folds_sig = folds_dd_ok = 0
for i, H in enumerate(REGIMES):
    train, test = df[df["regime"] != H], df[df["regime"] == H]
    w = train_weights(train)
    eq_R, wt_R = apply_weights(test, w)
    res = paired_block_boot_dsharpe(eq_R, wt_R, seed=i)
    dd_eq, dd_wt = dd_p99(eq_R, seed=i), dd_p99(wt_R, seed=i)
    if res is None:
        print(f"{H:11} {len(test):>5}   (too few held-out signals)"); continue
    obs, lo, hi, p = res
    pos, sig, dd_ok = obs > 0, (lo > 0), (dd_wt <= dd_eq * 1.02)
    folds_pos += pos; folds_sig += sig; folds_dd_ok += dd_ok
    print(f"{H:11} {len(test):>5} {obs:>+8.4f} {f'[{lo:+.3f},{hi:+.3f}]':>18} {p:>7.3f} "
          f"{f'{dd_eq:.1f}→{dd_wt:.1f}':>18}  {'BETTER' if pos else 'worse'}"
          f"{' ,sig' if sig else ''}{'' if dd_ok else ' ,DD↑'}")

# Pooled (weights trained on ALL regimes = the shipped table)
wall = train_weights(df)
eq_R, wt_R = apply_weights(df, wall)
pres = paired_block_boot_dsharpe(eq_R, wt_R, seed=99)
print("-" * 96)
if pres:
    obs, lo, hi, p = pres
    print(f"{'POOLED':11} {len(df):>5} {obs:>+8.4f} {f'[{lo:+.3f},{hi:+.3f}]':>18} {p:>7.3f}   (in-sample fit — context only)")

print(f"\nOOS folds: ΔSharpe>0 in {folds_pos}/{len(REGIMES)}, CI-excludes-0 (significant) in {folds_sig}/{len(REGIMES)}, "
      f"DD-not-worse in {folds_dd_ok}/{len(REGIMES)}")
ship = folds_sig >= 3 and folds_dd_ok >= 3
print(f"VERDICT: {'SHIP — reallocation generalizes OOS with significance' if ship else 'DO NOT SHIP — not significant OOS (freeze at equal-weight; the honest per-hour-filter lesson)'}")

print("\n=== Cells that would receive a non-1.0 weight (trained on ALL regimes) ===")
any_bet = False
for (s, st), wt in sorted(wall.items()):
    if abs(wt - 1.0) > 0.02:
        any_bet = True
        sub = df[(df.session == s) & (df.strategy == st)]
        _, _, _, sr, _, _ = es._sharpe_moments(sub["R"].to_numpy(float))
        print(f"  {s:7} {st:12} w={wt:.2f}  (t={sr*np.sqrt(len(sub)):+.1f}, n={len(sub)}, MinTRL "
              f"{es.min_trl(sub['R'].to_numpy(float) if sub['R'].mean()>0 else -sub['R'].to_numpy(float),0,0.95):.0f})")
if not any_bet:
    print("  NONE — every cell froze at 1.0 (no cell cleared its MinTRL). Session-weighting has no shippable signal.")
