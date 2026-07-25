# Guardian + Re-Entry Workflow (research-backed)

Fixes the two money-losing behaviours the user reported on GOLD:
1. **Guardian closed wide-stop trend/pullback winners too early** ("TP later hit but no open trade").
2. **Churn** — a combo closed by the guardian/SL was immediately re-opened with the same SL/TP and closed again.

Every number below is a **research-backed default behind a constant** (tune, don't treat as sacred). See the two research pillars: Maximum Adverse Excursion (Sweeney), the Trader's Equation (Al Brooks), and re-entry practice (Brooks 2nd-entry, Freqtrade `CooldownPeriod`, López de Prado meta-labeling). Related memory: `llm-provider-failover`, project memory `gold-trading-bot`.

---

## Part A — Protective guardian (early exit BEFORE the hard stop)

Code: [`_danger()`](../mt5_bot.py) + [`_guard_frac()`](../mt5_bot.py), constants `GUARD_FRAC_BY_MODE`.

**Problem:** one fixed `GUARD_FRAC = 0.60` for every strategy. A wide-stop (9–11pt) trend/pullback trade breathes to 70–90% of its stop on a *normal winner* (MAE research), so cutting at 60% + "momentum against" (trivially true in any retrace) converted winners into realized losses. Tight 1-min scalps never tripped it and won — the trader's-equation math says a ~90%-win, reward≤risk scalp must **never** be cut early.

**Rule now (per strategy, evaluated on a CLOSED candle — never an intrabar wick):**

| Strategy (`guard_mode`) | M5 fraction | M1 fraction | Meaning |
|---|---|---|---|
| `secondentry` / `secondentry1` | 0.95 | 0.90 | Effectively OFF — let the tight hard stop run |
| `trend` | 0.88 | 0.83 | Wide stop, retraces normal → give room |
| `pullback` | 0.82 | 0.78 | Adverse move is the entry premise → exit only on real reversal |
| `breakout` | 0.55 | 0.50 | Failed breakout = fast invalidation (early exit adds value) |
| default | 0.80 | 0.75 | — |

Guardian still closes on **(1) a reversal signal** (opposite signal ≥ `GUARD_REV_CONF`) and **(3) adverse news confirmed by momentum** — those are unchanged. The **hard broker SL stays intrabar** as the backstop; only the discretionary early-exit is closed-candle + per-strategy.

Tune: `GUARD_FRAC_BY_MODE`, `GUARD_FRAC_M1_TIGHTEN`. Ideal = 85–90th percentile of each strategy's own winner-MAE (log MAE per trade and re-fit over ≥100 trades).

---

## Part B — Anti-churn re-entry gate

Code: [`_reentry_gate()`](../mt5_bot.py) + [`_choppiness()`](../mt5_bot.py); exit metadata stamped in [`finalize()`](../mt5_bot.py); wired into the entry loop after the spread veto. Runs **per `(combo, direction)`**, only after a **recent LOSS** exit (a win → normal first-entry, no gate).

Decision flow for a combo about to open:

```
last exit was a WIN or none        → ALLOW (normal first entry, no AI forced)
opposite direction to the loss     → ALLOW immediately (Brooks: a failed breakout is a
                                      breakout the other way; cooldown is directional)
consec_losses ≥ 3 (same combo)     → BLOCK ("paused"); resets on a win or the daily rollover
within cooldown & not stronger     → BLOCK (churn).  cooldown = REENTRY_COOLDOWN_BARS[tf] × bar
                                      (M1 = 3 bars ≈ 3 min, M5 = 2 bars ≈ 10 min).
                                      "stronger" = conviction ≥ last_entry_conv + 0.05
Choppiness Index > 61.8            → BLOCK (Brooks "barbwire"/range — re-entry would churn)
otherwise                          → ALLOW, and REQUIRE AI CONFIRMATION if TF ≥ M5
```

**AI-confirmed re-entries (user's rule + meta-labeling):** an allowed re-entry on **M5+** is routed through `confirm_trade` (Kimi + council via the free multi-provider failover), **bypassing** the per-scan confirm cap, and **fails CLOSED** — no positive AI confirm ⇒ no re-open. On **M1** the re-entry uses the fast deterministic gates only (a 2–10s LLM call would eat the 60s scalp bar — research: latency hurts M1 far more than M5). First entries are unchanged.

Constants: `REENTRY_ENABLED`, `REENTRY_COOLDOWN_BARS`, `REENTRY_MAX_CONSEC_LOSS`, `REENTRY_STRONGER_MARGIN`, `REENTRY_AI_MIN_TF_SEC`, `CHOP_PERIOD`, `CHOP_BLOCK`.

Why CHOP not ADX: research + the project's own backtests show ADX **lags on M1/M5 and can hurt gold** (one test PF 1.54→0.40); the Choppiness Index is a faster, non-directional range detector (>61.8 = choppy, <38.2 = trending).

---

## What was verified (unit test)
- `_guard_frac`: trend 0.88 / pullback 0.82 / secondentry-M1 0.90 / breakout 0.55.
- `_choppiness`: steady-trend series → 36.6 (trend); oscillating series → 91.5 (choppy).
- `_reentry_gate`: same-dir-weak → BLOCK cooldown; opposite-dir → ALLOW; stronger-same-dir → ALLOW + needs_ai (M5); 3 consecutive losses → BLOCK paused.

## Honest caveats
- The specific numbers (fractions, 2–3 bar cooldown, 0.05 margin, 3-loss pause, CHOP 61.8) are **prudent literature defaults, not backtested constants** for M1/M5 gold/EURUSD. Ship as defaults, then tune on walk-forward data.
- The clearly-correct part regardless of tuning: the bot previously had **zero** re-entry guard, so even the minimal rules (cooldown + fresh-loss memory + CHOP + 3-strikes) materially cut the churn loop; and a per-strategy closed-candle guardian stops cutting wide-stop winners.
- Future phase-2 (not yet implemented): require a **new confirmed swing (BOS) + level reclaim** before a same-direction re-entry (Brooks/Wyckoff) for an even stricter structural gate.
