# The Honest Guide to Trading Profits — for this Gold-M5 bot

_Sourced research briefing (2026-07-09, 13-agent workflow, every claim web-verified). No hype.
Bottom line up front: on the things that decide survival, **we are already doing them right**.
The remaining work is **cost accounting, sample size, and patience** — not more cleverness._

---

## 1. How trading profits actually work (the honest base rates)
- **~70–90% of retail accounts lose** — regulator-mandated CFD/forex broker disclosure (ESMA/FCA, 74–89%). [FCA PS19-18](https://www.fca.org.uk/publication/policy/ps19-18.pdf)
- **<1–3% are durably profitable** net of costs — full Taiwan day-trader records 1992–2006 (Barber, Lee, Liu & Odean): ~5% profitable in a year, **<1% predictably positive** net of fees. [PDF](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf)
- **Over-trading IS the losing behavior** — avg day trader loses ~23.9 bps/day; survival 44%/24%/15% at 1/2/3yr; **unprofitable traders are 72–80% of all volume** (Barber-Odean). The losers *are* the high-frequency cohort.
- **An in-sample edge is not a live edge** — 97 published predictors decayed **~26% out-of-sample, ~58% post-publication** (McLean & Pontiff, JoF 2016).
- **Backtest overfitting is the core algo failure** — enough tries always fit the past (Bailey & López de Prado; Deflated Sharpe / CPCV exist to correct it).

**Maps to us — confirmatory:** disabling pullback (−EV OOS) + the M1 scalp moved us off the high-volume losing cohort. Our secondentry edge failing the Deflated Sharpe is the *honest* verdict, not a failure. The job is survive-and-don't-fool-yourself, not maximize signals.

## 2. The six durable principles (formula-backed)
1. **Expectancy** `E = p·AvgWinR − (1−p)·AvgLossR` (Van Tharp). Positive but thin E is fragile to cost + variance.
2. **Sample size / SQN** `= √N · mean(R)/std(R) = per-trade-Sharpe × √N`. Ours ~0.025/trade → below Tharp's ~1.6 "tradeable" floor until N is in the **thousands**. This is the math behind "needs thousands of trades to confirm."
3. **Risk of ruin / Kelly** growth peaks at f*, turns **negative past ~2× Kelly** (asymmetric — over-betting is far worse than under-betting). We're ~half-Kelly at 2% → **never scale up on a streak, never tighten in a drawdown.**
4. **Payoff vs win-rate** — expectancy is invariant to the mix; "cut losers, let winners run" = keep AvgLossR small, AvgWinR large.
5. **⭐ Cost speed-limit (THE decisive one for us)** — Carver: **spend < 1/3 of pre-cost Sharpe on costs.** `MaxTrades/yr = [(grossSR/3) − holding] / costPerTrade`, cost in vol units. [qoppac](https://qoppac.blogspot.com/2020/04/how-fast-should-we-trade.html)
6. **Edge decay / Deflated Sharpe** — expected max Sharpe over N trials rises with N; **every backtested variant raises the bar** → stop variant-fishing. [Bailey & LdP](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)

## 3. ⭐ THE COST FINDING — measured on OUR trades (edge_stats.py cost speed-limit)
| Cohort | gross R | cost R (spread/stop) | net R | cost drag | verdict |
|---|---|---|---|---|---|
| **secondentry** | +0.093 | 0.138 | **−0.045** | **149%** | OVER — spread eats MORE than the whole edge |
| all ex-M1 | +0.259 | 0.121 | +0.138 | 47% | OVER (Carver ceiling = 33%) |

**We scalp M5 with stops so tight (~2.6px) that the 0.20px spread is a bigger factor than the signal.**
This is the structural reason the edge is so thin. The math-sound levers (to VALIDATE, not rush):
**fewer / higher-conviction trades and/or WIDER stops+targets** (so cost is a smaller fraction of R) — the
opposite of our tighten-in-drawdown reflex. Must pass walk-forward + dollar-curve MC before shipping.

## 4. Open-source tools to borrow (ranked by fit)
**Install & use now:**
- **quantstats** (~7.4k★, Apache-2.0) — one-call tearsheets (Calmar/Sortino/tail-ratio/DD). Complements, does NOT replace, edge_stats.py. https://github.com/ranaroussi/quantstats
- **purgedcv** (MIT, PyPI) — `deflated_sharpe_ratio`/`probabilistic_sharpe_ratio`/`min_track_record_length` to **cross-check** our hand-rolled math; pass `effective_n_trials` = honest variant count so the DSR bar auto-inflates. https://github.com/eslazarev/purged-cross-validation

**Study the pattern, don't adopt (reimplement ~100–200 lines, avoid the GPL/AGPL):**
- **Freqtrade "protections"** (StoplossGuard, auto-resuming MaxDrawdown, CooldownPeriod, LowProfitPairs) — the generalized version of our kill-trigger. **Validate any overlay by REPLAYING the OOS trade series through it as a deterministic post-processor** (no new fitted params → no new DSR penalty). Avoid its hyperopt (overfit machine).
- **pysystemtrade** (Carver) — read the vol-scalar/half-Kelly formulas; independent confirmation our 2% sizing is right. Don't adopt the multi-asset engine.
- **pypbo** — port ~200 lines of the CSCV Probability-of-Backtest-Overfitting math.

**Skip:** mlfinlab (paywalled), vectorbt/backtrader/backtesting.py/LEAN (edge-discovery param-sweep engines = variant-fishing; idealized fills understate the spread that dominates us; can't replay our LLM layer), ML purged-CV splitters (prediction is dead here).

## 5. Curated study list (risk/sizing/validation-first)
1. **Carver — _Leveraged Trading_ (2019)** — the closest book to our exact situation (ONE instrument, vol-based constant-risk sizing). Read first.
2. **Carver — _Systematic Trading_ + free [blog](https://qoppac.blogspot.com)** — vol-targeting/half-Kelly + the four fatal mistakes (we violate none).
3. **Bailey & López de Prado — _The Deflated Sharpe Ratio_** ([PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)) — the exact math in edge_stats.py.
4. **Bailey/Borwein/LdP/Zhu — _Pseudo-Mathematics & Financial Charlatanism_ (PBO/CSCV)** — why every variant raises your bar.
5. **Van Tharp — _Definitive Guide to Position Sizing_** — expectancy, R-multiples, SQN.
6. **Ernie Chan — _Quantitative Trading_ (2nd ed)** — honest retail-quant workflow + Kelly appendix.
7. **López de Prado — _Advances in Financial ML_** — Ch. 7, 11–12, 14–15 ONLY (skip the ML-alpha chapters; prediction is dead here).
8. **López de Prado — _10 Reasons Most ML Funds Fail_** ([free PDF](https://www.smallake.kr/wp-content/uploads/2018/07/SSRN-id3104816.pdf)) — 20-min gut-check.

## 6. What NOT to do (traps the research confirms)
Chase prediction · over-optimize/variant-fish · add strategies (over-trading is *the* losing behavior) ·
ignore costs (the speed-limit is a hard ceiling) · trust small samples · over-bet or tighten in a drawdown.

## 7. The three concrete next moves (low-regret, aligned with our findings)
1. **✅ DONE — cost speed-limit gate + over-trading metrics in edge_stats.py.** (Revealed 47–149% cost drag.) Next: add trades/day + cost-as-%-of-gross-profit as first-class metrics; treat the MAX-TRADES/YEAR as a hard ceiling.
2. **Cross-check DSR/PSR/MinTRL with `purgedcv`** and gate live sizing on **DSR > 0 AND trades ≥ MinTRL** — we currently FAIL DSR → hold at 2%, do not scale.
3. **Ship a protection overlay validated by REPLAY, not re-fitting** — generalize the kill-trigger (auto-resume, StoplossGuard, per-strategy LowProfit), thresholds a-priori from risk-of-ruin math.

**The honest bottom line:** we already do the things that separate the ~1–3% who last — half-Kelly sizing,
killed negative-EV strategies, refused to chase a dead forecast, shipped a kill-trigger, and we flag our own
edge as not-yet-proven. The research hands us **one new structural insight (the cost drag)** and otherwise
**confirms the discipline.** Add the checks, cut cost drag (validated), then let the trade count accumulate
without touching the sizing.
