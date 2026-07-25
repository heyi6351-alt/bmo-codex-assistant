"""Tests for the edge-measurement instrument (edge_stats.py). These numbers decide whether we
TRUST or ABANDON the edge and where the drawdown kill-trigger fires — they must be correct.
Pure numpy/scipy — no MT5."""
import math
import numpy as np
import pytest

import edge_stats as es


# ── expected_longest_loss_run: exact closed form ln(N)/−ln(1−p) ──────────────
def test_expected_longest_loss_run_known_value():
    # N=100, win rate 0.5 → ln(100)/−ln(0.5) = 4.60517/0.693147 ≈ 6.644
    assert es.expected_longest_loss_run(100, 0.5) == pytest.approx(6.6438, abs=1e-3)
    # higher win rate → shorter expected loss runs
    assert es.expected_longest_loss_run(100, 0.7) < es.expected_longest_loss_run(100, 0.3)


# ── _max_drawdown_R: cumulative flat-stake equity curve ──────────────────────
def test_max_drawdown_zero_on_monotonic_up():
    assert es._max_drawdown_R(np.array([1.0, 1.0, 1.0, 2.0])) == pytest.approx(0.0)


def test_max_drawdown_known():
    # R=[1,-2,1] → cum=[1,-1,0], peak=[1,1,1] → max DD = 1−(−1) = 2
    assert es._max_drawdown_R(np.array([1.0, -2.0, 1.0])) == pytest.approx(2.0)


# ── cost_speed_limit (Carver): drag = mean(cost)/mean(gross), gross = net+cost ─
def test_cost_speed_limit_ok_when_cost_small():
    drag, gross, net, verdict = es.cost_speed_limit([0.2, 0.2], [0.05, 0.05])
    assert gross == pytest.approx(0.25) and net == pytest.approx(0.20)
    assert drag == pytest.approx(0.2)            # 0.05 / 0.25
    assert "OK" in verdict


def test_cost_speed_limit_over_when_cost_dominates():
    drag, gross, net, verdict = es.cost_speed_limit([0.05, 0.05], [0.10, 0.10])
    assert drag == pytest.approx(0.10 / 0.15)    # 0.667
    assert "OVER" in verdict


def test_cost_speed_limit_infinite_when_gross_nonpositive():
    drag, *_ = es.cost_speed_limit([-0.10, -0.10], [0.05, 0.05])   # gross = -0.05
    assert math.isinf(drag)


# ── PSR: probability the true per-trade Sharpe > 0 ───────────────────────────
def test_psr_bounded_and_direction():
    pos = np.random.default_rng(0).normal(0.10, 1.0, 2000)
    neg = np.random.default_rng(1).normal(-0.10, 1.0, 2000)
    p_pos, p_neg = es.psr(pos), es.psr(neg)
    assert 0.0 <= p_pos <= 1.0 and 0.0 <= p_neg <= 1.0
    assert p_pos > 0.95 and p_neg < 0.05


def test_psr_rises_with_sample_size_for_a_real_edge():
    # Tile a fixed positive-mean pattern so realized moments (mean/std/skew/kurt) are IDENTICAL
    # and only N changes → PSR must rise with N (more evidence for the same realized edge).
    base = np.array([1.0, -1.0, 1.0, -1.0, 1.0, 0.5])   # mean +1/6, positive
    short, long = np.tile(base, 20), np.tile(base, 200)  # n=120 vs n=1200
    # realized Sharpe ~identical (tiny ddof=1 difference); the point is N drives the PSR gain
    assert es._sharpe_moments(short)[3] == pytest.approx(es._sharpe_moments(long)[3], rel=0.02)
    assert es.psr(long) > es.psr(short)


# ── MinTRL: trades needed to confirm; ∞ for a non-positive edge ──────────────
def test_min_trl_finite_for_positive_edge_infinite_for_negative():
    pos = np.random.default_rng(3).normal(0.10, 1.0, 1000)
    neg = np.random.default_rng(4).normal(-0.10, 1.0, 1000)
    assert np.isfinite(es.min_trl(pos)) and es.min_trl(pos) > 0
    assert math.isinf(es.min_trl(neg))


def test_min_trl_larger_for_thinner_edge():
    # MinTRL ∝ 1/Sharpe² → a thinner edge needs many more trades
    thin = np.random.default_rng(5).normal(0.02, 1.0, 5000)
    fat = np.random.default_rng(6).normal(0.20, 1.0, 5000)
    assert es.min_trl(thin) > es.min_trl(fat)


# ── sharpe moments sanity ────────────────────────────────────────────────────
def test_sharpe_moments_basic():
    R = np.array([1.0, -1.0, 1.0, -1.0, 2.0])
    n, mu, sd, sr, skew, kurt = es._sharpe_moments(R)
    assert n == 5
    assert mu == pytest.approx(R.mean())
    assert sr == pytest.approx(mu / sd)


def test_mc_drawdown_envelope_percentiles_ordered():
    R = np.random.default_rng(7).normal(0.02, 1.0, 300)
    env = es.mc_drawdown_envelope(R, window=200, paths=500)
    d = env["dd_pct"]
    assert d["median"] <= d["p95"] <= d["p99"]     # drawdown percentiles must be monotone
    assert env["streak"]["median"] <= env["streak"]["p99"]
