# Gold/XAUUSD Strategy — "Trend-Continuation, Risk-First" (v1)

> **Honest framing.** This is a *trend-following / pullback-continuation* design, because
> that is the **only** gold edge with real academic + reproducible backtest support
> (AQR Moskowitz–Ooi–Pedersen TSMOM; D1 Donchian/EMA systems backtest at profit factor
> ~1.5–1.85, ~38–43% win rate). Expect **few signals, ~40% win rate, +0.1–0.5R/trade,
> modest growth, and long losing streaks (6–10 losers in a row is normal).** It will NOT
> make money on its own and **must be validated on real spot data before it is trusted.**
> 74–89% of retail CFD accounts lose money; assume you are fighting a low base rate.

## What changed from the old engine
The old engine fired a BUY/SELL on **almost every bar** from a 6–8 indicator majority vote,
which included things the evidence says **lose** on gold:
- **RSI/oscillator mean-reversion** → documented loser on gold (PF ~0.60).
- **ADX used as a hard on/off gate** → measured to cut a gold strategy's PF from 1.54 → 0.40.
- **Over-trading** → it never stood aside.

The new engine (`analysis/signals.py::_trend_setup`) trades **only with the trend, only on a
pullback, and stands aside by default.**

## The rules (selective)
**Trade only if ALL hold (mirror for short):**
1. **Trend bias:** close > EMA200, EMA50 > EMA200, and EMA50 **slope up** over 5 bars.
   *(MA slope is the regime gate — NOT ADX. ADX is a soft confidence input only.)*
2. **Pullback + reclaim:** price pulled back toward the EMA20 in the last ~4 bars, then
   reclaimed it in the trend direction.
3. **Not chasing:** RSI(14) not already extreme (long < 72 / short > 28).
4. **Room:** nearest opposing support/resistance is ≥ 1.5R away.

If any fails → **NO TRADE** (the default posture). Few trades is a *feature*.

## Risk rules (risk-first)
- Risk per trade capped: **conservative 0.5% · moderate 0.75% · aggressive 1.0%** (never >1%).
- ATR/structure stops (never fixed pips). Let winners run — no flat 1:1 target.
- The ticket builds structure-based SL + a laddered TP (TP1 ~1–1.5R, runner trailed).

### Still to build (next phase — only matters once validated)
Hard caps the bot does **not** yet enforce in code: max trades/day, daily/weekly loss
kill-switch, consecutive-loss cooldown, session filter (London/NY), news blackout
(CPI/NFP/FOMC ±15–30 min), breakeven-at-1R + trailing, and a **broadcast auto-disable** if
live expectancy goes negative.

## ⚠️ Validation gate — NOT yet passed
Before this should be trusted with real money it must clear, **on real spot XAU/USD with
costs modeled**, OUT-OF-SAMPLE:
- ≥ 100 closed trades (200+ preferred; < 30 = noise)
- Profit factor ≥ 1.3 (net of spread + slippage); PF > 2.5 = overfit red flag
- Expectancy ≥ +0.2R; profitable across the majority of walk-forward windows
- Max drawdown within Monte-Carlo 95th pct; win rate 38–55% (70%+ = curve-fit red flag)
- Then forward/paper-test 1–3 months at micro size before going live.

**Current status:** ❌ **UNVALIDATED.** On the current data (yfinance gold *futures*,
~300–400 bars, EMA200 consumes 200) backtests yield only **1–3 trades = pure noise.**
We cannot yet say it has an edge.

## v2 — Active Intraday "pullback" mode (default, `STRATEGY_MODE=pullback`)
To fix "always NO TRADE", a more permissive intraday mode drives off **M15**:
2-of-3 trend bias (price/EMA50, EMA50/EMA200, slope), entry on a **zone** (pullback-tag
*or* MA-band + RSI turn *or* MACD resume) instead of a knife-edge reclaim, lower floor
(0.40–0.50), kept anti-chase + trend-only. Result: **~several setups/day** (≈25 over the
last 400 M15 bars) — active, as requested.

**⚠️ Honest backtest (real spot M15, costs modeled): it LOSES.**
`24 trades · 38% WR · −0.32R avg · PF 0.53` (in-sample 0.52, OOS 0.55). Frequent ≠ profitable —
exactly what the research warned. Use this mode for **paper-trading / learning only.**
Switch to the selective daily edge with `STRATEGY_MODE=trend` (rarely trades, ~breakeven,
also unvalidated). **Neither mode has a proven money-making edge.**

## The data blocker (do this first)
The bot falls back to **yfinance gold futures (GC=F), delayed ~15–20 min** — not real spot
XAU/USD. Backtests on free/futures data overstate returns ~80% and understate drawdown ~50%.
**Fix:** add a free `TWELVEDATA_API_KEY` (spot XAU/USD, 20 yrs history) — or an OANDA v20
practice feed — then a real backtest/walk-forward becomes possible. Until then, treat every
signal as educational only.
