# Trading Bot — Improvement Workflow & Roadmap

_Last updated 2026-07-08. The disciplined loop we use to improve the bot without fooling ourselves,
plus the validated roadmap and the "proven-harmful, do not repeat" list._

---

## 0. The core principle

> **>90% of strategies that look great in one backtest fail live.** The edge isn't a clever
> strategy — it's the *discipline* that separates a real edge from luck and noise. Every change
> must clear that discipline before it touches live money.

---

## 1. The improvement loop (run this for EVERY proposed change)

1. **Hypothesis first** — state *why* an edge should exist (market structure / economics), not
   "the backtest liked it." Backtest-mined rules overfit.
2. **Backtest net-of-cost** on the Dukascopy regime data (`storage/duka_*_m5.csv`) at the **live
   cost (0.20 px)**, not the raw Dukascopy spread.
3. **Walk-forward validate** — `python walk_forward.py [n_windows]`. A real edge is positive in
   **most out-of-sample windows across regimes**. A coin-flip window-to-window = FRAGILE = luck.
4. **Ship OFF by default** behind an env flag; enable + **monitor live expectancy**.
5. **Verify live** — restart, confirm no errors, watch the first real fills.
6. **Re-audit periodically** — regimes are non-stationary; re-run the walk-forward.

**Tools already built for this loop:**
- `mt5_backtest.py` — replays the LIVE engine on a CSV (`BACKTEST_CSV=…`), with `BT_SCAN_STEP`
  (speed), `BT_N_WINDOWS`/`BT_WINDOW_IDX` (walk-forward windows), `BT_MIN_BOOSTERS`, `BT_BE_TRIGGER_R`.
- `walk_forward.py` — rolls OOS windows across all regimes, prints ROBUST/FRAGILE/NEGATIVE per strategy.
- `data/dukascopy.py` + `scripts_build_regimes.py` — free tick history → regime CSVs.

---

## 2. What we've VALIDATED (trust these)

- **secondentry = ROBUST** (walk-forward +35R, 6/8 OOS windows). The real workhorse.
- **Regime auto-switcher** — continuation in trend, mean-reversion in chop. Sizes, never blocks.
- **Slam-dunk sizing** — 98–100% conviction trades genuinely win (65%, +$80 live). Bet more there.
- **Edge-gated ML** — the ML only influences conviction when its walk-forward CV proves an edge.

## 3. Proven HARMFUL — do NOT repeat (each was backtested and made things worse)

- ❌ **Breakeven / moving SL to entry sooner** — reduces profit (BE-off +46R > BE-0.5 +44R > BE-0.3 +43R).
- ❌ **Room gate on secondentry** — cuts winners, +44R → +14R.
- ❌ **Raising the confluence-booster requirement** — over-filters (chop2026: ≥1 PF 1.44 → ≥2 PF 1.30).
- ❌ **Trusting a small live sample over the walk-forward** — pullback's live +$66 was 14-trade luck
  (walk-forward: pullback NEGATIVE). Direction-ML is a coin-flip (0.51) — don't let it size trades.
- ❌ **The M1 1-min scalp** — 27% win, PF 0.44. Regime-specific; off (default now fail-safe `false`).

### Loss post-mortem 2026-07-09 (15-agent adversarial workflow) — REFUTED, do NOT chase:
- ❌ **"LLM-confirmed beats engine" (+$46 vs −$129)** — tier-mix confound; edge is −$0.02/trade (p=0.995)
  once tier is controlled. And the whole confirmed bucket was qwen/kimi — models that no longer run.
- ❌ **Hard D1-direction block on secondentry longs** — the −$85 long bleed is ONE chop day (07-07 = −$81,
  WR CI straddles 50%); a block violates "size don't block" + guts the Brooks reversal second-entry.
- ❌ **Hard secondentry conviction floor (SE_MIN_CONVICTION)** — the ">=0.90 edge" is a 2-trade artifact
  and a laundered "trade fewer longs" (conviction is a near-perfect direction proxy). Counter-tape penalty covers it.
- ❌ **Kill firstentry on 0/2** — n=2 vs a positive backtest (PF 1.17) = the "trust a small sample" trap.
- ❌ **Flatten TIER_RISK_SPLIT (aggressive 0.40→0.33)** — the tier→loss ladder is a composition artifact
  (aggressive was polluted by the M1 scalp); fixing the strategy axis handles it, penalizing the tier is friendly fire.
- ❌ **Per-hour / session filter** — small-sample fan-out noise (25 bleed-hour trades = 13 independent signals);
  session filter (07–21 UTC) + session-open half-risk already exist.
- ⚠️ **Regime-aware trade GEOMETRY (scale stop+TP up)** — TESTED OOS (`scripts_regime_geometry.py`, 2026-07-10),
  DROPPED. The cost lever is REAL (Carver cost speed-limit: our M5 stops so tight the 0.20 spread eats ~149%
  of secondentry's gross edge; wider geometry cuts drag and lifts pooled OOS +19R→+31R, winning in all 3
  TRENDING regimes). BUT it wrecks CHOP: naive m=2 in chop2026 = +2.4R vs the tight baseline's +28.6R.
  So it MUST be regime-gated — and neither leak-free per-signal detector is accurate enough (M5-ADX hurts
  bull2025 −4.0 & chop2026 +22<+28.6; resampled-H1-ADX is degenerate, always "trend"). It hurts the CURRENT
  chop regime → NOT shipped. Pooled/dollar-MC gains (Calmar 0.58) are dominated by trending regimes and mask
  the current-regime loss. Confirmed lever, BLOCKED on an accurate backtest-validatable regime detector.

**Meta-lesson:** in a drawdown, *tightening* (filters, tighter stops, breakeven) reliably makes a
+EV strategy worse. The only lever that helps is **sizing** (regime weight, conviction/Kelly), not gates.

### Quant-discipline research playbook (2026-07-09) — the framework that ENDS the tinkering
Deep-research workflow (17 agents, sourced: Bailey & López de Prado, Magdon-Ismail, Harvey et al, Moreira–Muir).
Built `edge_stats.py` (PSR / t-stat / MinTRL / block-bootstrap drawdown envelope from a per-trade R-series).
- **⭐ SHIPPED THE FIRST PROFIT LEVER: M15 secondentry** (`SECONDENTRY15_ENABLED=true`, conservative tier,
  monitored). Root cause of the thin edge = COST DRAG (spread eats 149% of the M5 scalp's gross edge).
  Principled fix: trade the SAME edge on M15 where the wider ATR geometry cuts drag 65%→37%, DOUBLES the
  per-trade Sharpe (0.038→0.10), and **PASSES the Deflated Sharpe that M5 FAILS** (PSR 96%, MinTRL 299 vs
  1853). Validated under the bot's ACTUAL live geometry (+32.5R, 3/4 regimes). Regime-COMPLEMENT to M5
  (M15 wins trends where M5 loses; M5 wins chop). Wired like the M1 variant, magic-stable. WATCH live
  expectancy vs backtest before promoting tiers; `SECONDENTRY15_ENABLED=false` reverts.
- **SHIPPED: `edge_stats.py` + `edge_walkforward.py`** (measurement) and the **drawdown kill-trigger**
  (`mt5_bot.py` `_drawdown_kill`, env `DD_KILL_*`, default DD_KILL_PCT=55 = secondentry OOS 99th-pct; inert
  until 200 trades; pauses new entries only past the envelope AND rolling-200 t-stat<0). The bot can no
  longer be panic-tightened in a normal drawdown.
- **AUTHORITATIVE MEASURED read** (edge_walkforward, 730 OOS trades): secondentry **+19.2R, 54.5% WR,
  per-trade Sharpe 0.025** (thinner than the PF-1.12 model's 0.057), PSR 75.5%, **MinTRL ≈ 4,146 trades**,
  and **Deflated Sharpe FAILS** (SR0(8 variants) 0.040 > 0.025). pullback **NEGATIVE −18.1R/264, t=−1.19**.
  The edge is thin, marginally-positive, UNCONFIRMED — and our own ~8 variants are why DSR fails.
  Judge on per-trade R-series (never annualize, never cumulative-R). **Every rule variant is a DSR trial
  that buries the edge → the fix is STOP adding variants + collect clean forward tape.**
- **Normal variance (so we STOP panicking):** an **8–12 loss streak and a 25–45% drawdown are ORDINARY**;
  median MC drawdown ≈ 27–34%. We tinkered at a **−$83 ≈ 2.8%** drawdown — nowhere near the kill line.
- **Drawdown-discipline KILL-TRIGGER (to hard-code):** pause & re-validate ONLY if live DD > the 99th-pct
  bootstrap envelope AND rolling-200-trade t-stat < 0. Otherwise **change NOTHING.** Judge only on ≥200-trade windows.
- **Volatility targeting:** we are ALREADY vol-targeted per trade (`lots = equity×risk% / (atr_mult×ATR×point)`).
  2% ≈ half-Kelly (full-Kelly 5.4%) — correctly sized. Gold is a commodity → vol-managed Sharpe boost is
  negligible (Harvey et al); only a **cut-only** account scalar `clip(vol_ref/vol_now, 0.5, 1.0)` off a DAILY
  vol measure is defensible, and ONLY if it clears a **dollar-equity-curve + block-bootstrap MC** (the step
  skipped on the reverted wide-stop haircut). Low priority — after ~260 more trades.
- **Order of ops:** freeze rules → wire PSR/DSR/PBO into walk_forward.py → pre-register the kill-trigger →
  collect ~260 more trades → only THEN test the vol scalar. **We need more tape, not more rules.**

---

## 4. Roadmap — validated NEXT builds (highest value first)

1. **Fractional-Kelly + volatility-targeting sizing** — size by *realized* edge (¼–½ Kelly, never
   full) and shrink positions in high vol. **PREDICTION-FREE** — it doesn't forecast which trades
   win (our tests keep proving we can't); it just manages money better. Directly attacks the
   win/loss-**dollar** asymmetry (avg loss $11 > avg win $9 despite ~symmetric R). Layer on the 2% cap.
2. **Regime-aware confluence** (modest) — confluence A/B showed secondentry needs MORE confluence in
   the losing regimes: range2021 `>=2` flips it +PF 1.27 (vs 0.95 at `>=0`), bear2022 `>=2` = 0.90
   (vs 0.83). In chop/bull, `>=1` is best. Could tie `MIN_BOOSTERS` to the regime detector. Small gain.
3. **Confirmer robustness** (bug backlog #1/#2) — give `llm.complete` a cooperative **deadline** so
   one confirm can't burn the whole budget on a slow provider (starving fast failover) and so
   abandoned daemon threads stop spending shared NVIDIA RPM quota.

### ❌ Meta-labeling — TESTED, no within-regime edge — DO NOT build
`meta_label_test.py` (López de Prado meta-labeling: secondary model predicting "will this secondentry
signal win?"). Per-regime AUC 0.45–0.50 (coin-flip; bear2022 0.45 = *inverted*). The pooled "ALL"
AUC 0.558 is a **regime base-rate confound**, not usable within-regime signal. Same verdict as the
direction-ML: gold M5 outcomes aren't predictable from our features. Prediction is a dead end here —
**edge lives in sizing & risk management, not forecasting.**

---

## 5. Open bug backlog (triaged 2026-07-08, not yet fixed)

| # | Area | Issue | Severity |
|---|------|-------|----------|
| 1 | `trade_confirmer.ask_models` | per-model budget can let a slow first model starve the fast fallback → re-entry fails-closed | MED-HIGH |
| 2 | `_complete_with_timeout` | abandoned daemon thread keeps walking the chain + writing to the shared RPM lockfile → phantom quota use | MED-HIGH |
| 3 | `_passes_gate_impl` | `MIN_CONVICTION` only gates trend/squeeze; counter-tape penalty can't BLOCK secondentry (only de-slam-dunks it) | MED (by design) |
| 4 | `manage_open` | `RATCHET` / `_ratchet_floor_r` documented but never called (dead feature) | MED |
| 5 | `providers.resolve_chain` | `_nv_rr` advances on `rate_limited()` bookkeeping → NVIDIA rotation isn't a clean +1 | LOW |
| 6 | `_global_reserve` | on lock-timeout it proceeds *uncounted*, bypassing the RPM cap under contention (low impact: single-symbol) | LOW |

## 6. FIXED 2026-07-08 (this pass)

- ✅ **TP-ladder gating** (`manage_open`) — tagged TP rungs now lock **before** breakeven. Was the
  prime cause of "tagged-TP winners → full losses" and live-underperforms-backtest.
- ✅ **Unknown-action safety** (`trade_confirmer`) — an unrecognised LLM verdict is now rejected,
  not turned into a synthesized unconfirmed trade.
- ✅ **Aggressive TP ladder** — was `(1.5,3,5)` (unreachable, +1R winners never locked) → `(1,2,3)`.

## 7c. SL/TP/BREAKEVEN DEEP AUDIT (2026-07-10, 13-agent workflow vs Brooks doctrine + research + OSS)

**Conformance verdict: the geometry is fundamentally sound.** Stop LOCATION (signal-bar extreme ∓ 0.1×ATR)
= exactly Brooks doctrine + the structure-stop literature. 2×ATR cap, $2.50/2×spread cost floor, TP_CAP_ATR=4,
candle-confirmed BE, rung ratchet, no-trailing, time-stops — all CONFORM to research/industry practice.
KEY DISCOVERY: the scary 0.3×ATR signal floor is NOT binding live — instruments.py min_stop_price=$2.50
lifts every gold stop to an effective 1.0–1.7×ATR (spread = 8–14% of 1R, inside viability guidance).
The M15 package (+32.5R OOS) is DEVIATES-BUT-VALIDATED in full — do not touch without re-validation.

**FIXED (3 confirmed BE bugs, convention-restoring, no validation needed):**
- ✅ **BE could LOOSEN a rung-locked stop** — the BE block never compared against the current SL and the
  ladder never set be_done; price tags TP1 intrabar → later a ≥0.5R candle close moved SL back DOWN to
  entry+0.05R (gave back 0.20–0.45R of locked profit). Now tighten-only + ladder sets be_done.
- ✅ **BE off a pre-entry candle** — `_last_closed_candle` had no timestamp guard; a mid-bar entry could
  get INSTANT breakeven from a candle that closed before the trade existed. Now requires one full TF bar
  of trade age (timezone-safe guarantee the bar closed during the trade).
- ✅ **weekend_protect retry loop** — BE-locked without setting be_done → identical SL modify re-sent
  every 5s. Now sets be_done. (+ ladder `improved` now compares against the LIVE SL, not a stale snapshot;
  BE Telegram message now says the trade's own TF, not "5-min".)
- ✅ **Cap-bind diagnostic shipped** (zero-risk log): `SE-GEOM stop/ATR` at every scalp entry — decides
  whether the 0.25R-TP1 pathology (when the 2×ATR cap binds) is material (>20% bind rate → run the A/B).

**BREAKEVEN VERDICT:** mechanics now sound; policy-wise BE-0.5 is expectancy-suboptimal by EVERY source
(our sweep: off +46.3R > 0.5R +44.4R > 0.3R +43.8R; Brooks says ~1R; OSS puts BE far later) — kept
deliberately as a documented ~1.9R/window insurance premium. Validation-gated 2D sweep (trigger × lock
level) is the sanctioned experiment. NEVER below 0.5R (frozen).

**Validation-gated candidates (none ship without walk-forward):** #1 ROI-by-time decay exit (Freqtrade
minimal_roi pattern — monetize stalled winners at +0.2–0.5R before the binary time-stop; our median winner
reaches only 32% of final TP). #2 TP-to-stop coupling if the cap-bind diagnostic shows >20% (single-TP
theory says 2.5–4× EV IF the edge is persistent; the ladder is least-bad for a thin edge — data decides).
#3 ATR-relative stop floor max($2.50, k×ATR) for high-ATR (news) tails. Housekeeping: dead RATCHET code.

## 7b. FIXED 2026-07-10 (latest-trades investigation — 9-agent adversarial workflow)

- ✅ **`_closed_result` fabrication bug** (mt5_bot.py ~1782) — the date-range form of
  `mt5.history_deals_get` SILENTLY IGNORES `position=`; the fallback summed 154 unrelated deals and
  wrote a PHANTOM −$82.91 "close" into the CSV, combo pnl and `day_realized` (the daily-loss stop's
  input). The real trade closed at its exact SL for −$5.31 (zero slippage — the server-side SL worked
  perfectly during downtime). Fix: filter fallback deals by `d.position_id`, return None if empty
  (reconcile retries; NEVER fabricate). Books repaired (CSV row 88, day_realized, combo pnl).
- ✅ **Wedge watchdog** — the bot died 18:33 UTC after sitting WEDGED >2h in an all-provider LLM 429
  retry storm (alive, but no manage/reconcile ticks). Main loop now stamps `storage/tick_<SYMBOL>.txt`
  every tick; `start_bot.ps1` kills+relaunches when process >10min old AND tick stale >10min.
- ✔️ **Verified BY-DESIGN (do not "fix"):** off-session 02–06 UTC entries (SESSION_FILTER_ENABLED=false
  is deliberate AND off-session is the profitable half: +$75 vs −$162 in-session); the 15-second
  0.09-lot re-entry (stronger-signal bypass + mandatory AI confirm, 0.65% risked); M15 trade #1's
  SL-tighten (AI manager tighten-only authority; all M15 TF plumbing correct end-to-end).
- 👁️ **WATCH:** AI manager may re-manage an M15 trade inside its first 15-min bar (150s cadence).
  Candidate: one-bar age-skip in `_llm_advisor_loop` — only after replay validation.

## 7. FIXED 2026-07-09 (loss post-mortem + audit fixes)

- ↩️ **Wide-stop $-risk haircut** (`WIDE_STOP_HAIRCUT`) — SHIPPED then **REVERTED (default OFF)** the
  same day after the disciplined OOS check. In-sample the top-30%-stop cohort looked like the payoff-<1
  culprit (33% WR −$121 vs tight 65% +$114), so I shipped a stop>2×ATR risk-haircut. But `validate_wide_stop.py`
  on the 4 Dukascopy regimes showed **stop/ATR has ~0 corr with R-outcome for secondentry (−0.03), wide
  stops actually had BETTER meanR, and the haircut cut secondentry +20.6R→+18.4R.** The live signal was
  raw-stop∝high-ATR-period NOISE, not a stop-geometry edge — the exact "trust a small live sample" trap.
  Code kept env-gated for research. **Lesson re-underscored: validate OOS BEFORE shipping, not after.**
- ✅ **M1 scalp fail-safe default** — `M1_SECONDENTRY_ENABLED` default `true → false` (it was −$76 =
  92% of the account loss; a lost `.env` line would have silently re-armed it).
- ✅ **Closed-bar entries** (`ENTRY_ON_CLOSED_BAR`) — live was feeding the still-forming bar to
  generate_signal; walk-forward/backtest use closed bars. Now aligned (was the live-vs-backtest gap).
- ✅ **Audit bugs** — `by_combo` KeyError (adopt_orphans), stale `day_realized` loss-stop, day-counter
  RLock (main loop + 2 daemon threads).
- ✅ **Dead LLM models** — kimi (404 entitlement) / qwen (timeout) → minimax-m3; dropped Cloudflare
  GLM (100% timeout). AI trader now functions instead of "standing aside: models unavailable".
- ⏸️ **SLAMDUNK disable (3%→2%)** — HELD: finders disagreed (sizing-aggressive: slam-dunk net +$7,
  "earns its keep"; payoff synthesis: disable it). Awaiting owner call; env `SLAMDUNK_RISK_PCT`.
