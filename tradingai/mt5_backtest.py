"""Walk-forward backtest on REAL MetaTrader 5 spot-gold history.

The research's hard prerequisite: no strategy goes live until it clears a walk-forward
on the user's own MT5 XAUUSD data. This harness pulls broker history and replays every
strategy bar-by-bar through the SAME deterministic engine the live bot uses
(generate_signal + the confluence gate + the live TP-rung SL ladder + breakeven), so
the numbers reflect what the bot would actually have done. No look-ahead: the decision
at bar i sees only candles[:i]; the trade is simulated on candles[i:].

Usage:
    python mt5_backtest.py                 # all strategies, default history
    python mt5_backtest.py --bars 6000     # deeper history
    python mt5_backtest.py --strategy breakout
"""
from __future__ import annotations

import argparse
import os
from dataclasses import replace

import MetaTrader5 as mt5

from analysis.indicators import to_dataframe
from analysis.signals import confluence, generate_signal
from core.models import Candle
from risk.profiles import PROFILES
from instruments import get_instrument

# Set by main() from --symbol (default gold). Any registry symbol works: EURUSD, BTCUSD…
_INST = get_instrument(os.getenv("BOT_SYMBOL", "XAUUSD"))
MT5_SYMBOL = _INST.mt5_symbol
SIG_SYMBOL = _INST.sig_symbol
RISK_PROFILE = "moderate"   # risk profile for the run; override with --risk

# strategy -> (mode, interval, mt5_timeframe, default bars of history)
SPECS = {
    "secondentry": ("secondentry", "5min",  mt5.TIMEFRAME_M5,  8000),
    "pullback":    ("pullback",    "15min", mt5.TIMEFRAME_M15, 8000),
    "trend":       ("trend",       "1h",    mt5.TIMEFRAME_H1,  6000),
    "breakout":    ("breakout",    "15min", mt5.TIMEFRAME_M15, 8000),
    "firstentry":  ("firstentry",  "5min",  mt5.TIMEFRAME_M5,  8000),
    "range_fade":  ("range_fade",  "5min",  mt5.TIMEFRAME_M5,  8000),
    "squeeze":     ("squeeze",     "5min",  mt5.TIMEFRAME_M5,  8000),
}

WINDOW = 320              # candles fed to the engine each step (matches the live bot)
MIN_BOOSTERS = int(os.getenv("BT_MIN_BOOSTERS", "1") or 1)   # confluence requirement (A/B lever)
SESSION_START, SESSION_END = 7, 21   # London/NY UTC window (matches the live session filter)


def _connect() -> bool:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    login = int(os.getenv("MT5_LOGIN", "0"))
    pw = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
    if not mt5.initialize(login=login, password=pw, server=server):
        print("MT5 init failed:", mt5.last_error())
        return False
    return True


EXT_CSV = os.getenv("BACKTEST_CSV", "")   # if set, replay EXTERNAL data (Dukascopy) not MT5
_EXT_MEAN_SPREAD = 0.0                     # populated from the CSV's own per-bar spread column


def _load(tf, bars) -> list[Candle]:
    from datetime import datetime, timezone
    if EXT_CSV:                            # ── DUKASCOPY / external CSV path (deep history) ──
        global _EXT_MEAN_SPREAD
        import pandas as pd
        df = pd.read_csv(EXT_CSV)
        tcol = df.columns[0]
        if "spread" in df.columns:
            _EXT_MEAN_SPREAD = float(df["spread"].mean())
        out = []
        for _, r in df.iterrows():
            out.append(Candle(dt=str(r[tcol])[:19], open=float(r["open"]), high=float(r["high"]),
                              low=float(r["low"]), close=float(r["close"]),
                              volume=float(r.get("volume", 0) or 0)))
        # WALK-FORWARD: slice to ONE sequential out-of-sample window if requested
        # (BT_N_WINDOWS split count, BT_WINDOW_IDX which 0-based window). walk_forward.py
        # drives this per (regime, window) so each is an independent OOS test.
        nwin = int(os.getenv("BT_N_WINDOWS", "0") or 0)
        if nwin > 1:
            idx = max(0, min(nwin - 1, int(os.getenv("BT_WINDOW_IDX", "0") or 0)))
            sz = len(out) // nwin
            out = out[idx * sz:] if idx == nwin - 1 else out[idx * sz:(idx + 1) * sz]
        return out   # external: full regime window (or one walk-forward slice)
    rates = mt5.copy_rates_from_pos(MT5_SYMBOL, tf, 0, bars)
    if rates is None or len(rates) == 0:
        return []
    out = []
    for r in rates:
        dt = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
        out.append(Candle(dt=dt.isoformat(timespec="seconds"), open=float(r["open"]),
                          high=float(r["high"]), low=float(r["low"]),
                          close=float(r["close"]), volume=float(r["tick_volume"])))
    return out


# MT5 bar epochs are the BROKER SERVER's wall time (MetaQuotes = EET, UTC+2/+3),
# not UTC — so the "UTC" session window below is really server time. Shift the
# window by the server offset so 7-21 UTC means what it says (research 2026-07-03).
SERVER_UTC_OFFSET_H = int(os.getenv("MT5_SERVER_UTC_OFFSET_H", "3"))


def _hour(dt_iso: str) -> int:
    # dt_iso like '2026-07-01T13:00:00+00:00' (server-time epoch labelled UTC)
    try:
        return (int(dt_iso[11:13]) - SERVER_UTC_OFFSET_H) % 24
    except Exception:
        return 12


COST_PRICE = 0.0   # round-trip cost in PRICE units (spread + slippage), set in main()


# Breakeven trigger (R in profit before SL → entry). Env BT_BE_TRIGGER_R: 0.5 = live default;
# LOWER = move to breakeven sooner (saves reversals, but more BE stop-outs on shallow pullbacks);
# "off"/negative = never move to breakeven (SL stays at the original stop).
try:
    BT_BE_TRIGGER_R = float(os.getenv("BT_BE_TRIGGER_R", "0.5"))
except ValueError:
    BT_BE_TRIGGER_R = -1.0


def _simulate(entry, stop, tps, direction, future) -> float:
    """Replay the live exit logic on future bars; return the realised R multiple
    NET OF COSTS (spread+slippage deducted once per round trip — critical on M5).

    TP-rung SL ladder + breakeven-at-0.5R, matching mt5_bot.manage_open. Conservative:
    if a bar spans both the stop and a target, the STOP is assumed hit first.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    cost_r = COST_PRICE / risk        # round-trip cost expressed in R
    sl = stop
    tps = [float(getattr(t, "price", t)) for t in tps]   # accept TakeProfit objects OR raw prices
    tagged = 0
    for c in future:
        hi, lo, cl = c.high, c.low, c.close
        if direction == "long":
            if lo <= sl:                                   # stop / locked rung hit
                return (sl - entry) / risk - cost_r
            if BT_BE_TRIGGER_R >= 0 and cl >= entry + BT_BE_TRIGGER_R * risk:  # breakeven once in profit
                sl = max(sl, entry)
            while tagged < len(tps) and hi >= tps[tagged]:
                if tagged == len(tps) - 1:                 # final target reached
                    return (tps[tagged] - entry) / risk - cost_r
                sl = max(sl, tps[tagged])                  # ladder the stop up to the rung
                tagged += 1
        else:
            if hi >= sl:
                return (entry - sl) / risk - cost_r
            if BT_BE_TRIGGER_R >= 0 and cl <= entry - BT_BE_TRIGGER_R * risk:
                sl = min(sl, entry)
            while tagged < len(tps) and lo <= tps[tagged]:
                if tagged == len(tps) - 1:
                    return (entry - tps[tagged]) / risk - cost_r
                sl = min(sl, tps[tagged])
                tagged += 1
    # timed out — exit at the last close
    last = future[-1].close if future else entry
    gross = (last - entry) / risk if direction == "long" else (entry - last) / risk
    return gross - cost_r


FORCE_M5 = False   # set by --m5: run EVERY strategy on the 5-minute chart
FORCE_M1 = False   # set by --m1: run EVERY strategy on the 1-minute chart (FAST)
FORCE_TF = ""      # set by --tf: run EVERY strategy on this timeframe (5min|15min|30min|1h)
_TF_CONST = {"1min": mt5.TIMEFRAME_M1, "5min": mt5.TIMEFRAME_M5, "15min": mt5.TIMEFRAME_M15,
             "30min": mt5.TIMEFRAME_M30, "1h": mt5.TIMEFRAME_H1, "1day": mt5.TIMEFRAME_D1}
_TF_BARS = {"1min": 6000, "5min": 8000, "15min": 8000, "30min": 6000, "1h": 5000, "1day": 4000}
SCAN_STEP = int(os.getenv("BT_SCAN_STEP", "1") or 1)   # >1 samples every N bars (speed lever; walk-forward uses 3)


def backtest(strategy: str, bars: int, session_filter: bool = True) -> dict:
    mode, interval, tf, default_bars = SPECS[strategy]
    if FORCE_M1:
        interval, tf, default_bars = "1min", mt5.TIMEFRAME_M1, 6000   # fewer bars for a FAST M1 run
    elif FORCE_M5:
        interval, tf, default_bars = "5min", mt5.TIMEFRAME_M5, 8000
    elif FORCE_TF:
        interval, tf, default_bars = FORCE_TF, _TF_CONST[FORCE_TF], _TF_BARS.get(FORCE_TF, 8000)
    candles = _load(tf, bars or default_bars)
    if len(candles) < WINDOW + 100:
        return {"strategy": strategy, "error": f"only {len(candles)} bars"}
    results: list[float] = []
    i = WINDOW
    n = len(candles)
    while i < n - 1:
        # SCAN_STEP>1 samples every N bars — the FAST-M1 speed lever (skip most bars).
        if SCAN_STEP > 1 and (i - WINDOW) % SCAN_STEP != 0:
            i += 1
            continue
        window = candles[i - WINDOW:i]                     # decision sees only the past
        if session_filter and not (SESSION_START <= _hour(candles[i - 1].dt) < SESSION_END):
            i += 1
            continue
        prof = replace(PROFILES[RISK_PROFILE], strategy_mode=mode)
        try:
            sig = generate_signal(SIG_SYMBOL, window, prof, interval)
        except Exception:
            i += 1
            continue
        if sig.direction == "flat" or not sig.take_profits:
            i += 1
            continue
        boosters, _ = confluence(to_dataframe(window), sig.indicators, sig.direction)
        if boosters < MIN_BOOSTERS:
            i += 1
            continue
        # First-entry: approximate its STRICT live gate (conf>=FE_CONF_FLOOR, ADX>=FE_ADX_MIN).
        # The multi-TF H1/D1 with-trend requirement can't be applied on this single-TF replay,
        # so the real live PF is likely a bit HIGHER still than this measures.
        if strategy == "firstentry":
            if sig.confidence < 0.66 or getattr(sig.indicators, "adx14", 0) < 22.0:
                i += 1
                continue
        entry = candles[i].open                            # enter next bar open (realistic)
        # Apply the SAME cost-clearing floors the live open_combo uses (absolute per-instrument
        # min stop / min TP1 + a spread floor), so the backtest measures the FIXED M5 geometry.
        sign = 1 if sig.direction == "long" else -1
        stop_dist = max(abs(sig.entry - sig.stop_loss), _INST.min_stop_price, 2.0 * COST_PRICE)
        sl_f = entry - sign * stop_dist
        min_tp1 = max(_INST.min_tp1_price, 3.0 * COST_PRICE)
        dists, prev = [], 0.0
        for k, tp in enumerate(sig.take_profits):
            d = max(abs(sig.entry - tp.price), min_tp1 if k == 0 else prev + 0.5 * min_tp1)
            dists.append(d)
            prev = d
        tps_f = [entry + sign * d for d in dists]
        r = _simulate(entry, sl_f, tps_f, sig.direction, candles[i + 1:])
        results.append(r)
        # skip ahead so we don't re-enter the same setup every bar (1 trade per ~5 bars max)
        i += 5
    return _stats(strategy, interval, len(candles), results)


def _stats(strategy, interval, n_bars, R) -> dict:
    if not R:
        return {"strategy": strategy, "interval": interval, "bars": n_bars, "trades": 0}
    wins = [r for r in R if r > 0]
    losses = [r for r in R if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return {
        "strategy": strategy, "interval": interval, "bars": n_bars,
        "trades": len(R), "win_rate": len(wins) / len(R),
        "avg_R": sum(R) / len(R), "expectancy_R": sum(R) / len(R),
        "profit_factor": pf, "total_R": sum(R),
        "best_R": max(R), "worst_R": min(R),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=0, help="history bars per strategy (0 = per-strategy default)")
    ap.add_argument("--strategy", choices=list(SPECS), help="only this strategy")
    ap.add_argument("--no-session", action="store_true", help="ignore the London/NY session filter")
    ap.add_argument("--m5", action="store_true", help="run EVERY strategy on the M5 (5-min) chart")
    ap.add_argument("--m1", action="store_true", help="run EVERY strategy on M1 (1-min), FAST")
    ap.add_argument("--tf", default="", choices=["", "5min", "15min", "30min", "1h", "1day"],
                    help="override timeframe for the run (TF sweep)")
    ap.add_argument("--risk", default="moderate", choices=["conservative", "moderate", "aggressive"],
                    help="risk profile (TP r-multiples + conf floor) for the run")
    args = ap.parse_args()

    global FORCE_M5, FORCE_M1, SCAN_STEP, FORCE_TF, RISK_PROFILE
    FORCE_M5 = args.m5
    FORCE_M1 = args.m1
    FORCE_TF = args.tf
    RISK_PROFILE = args.risk
    if args.m1:
        SCAN_STEP = 3      # sample every 3rd M1 bar → ~3× faster, still plenty of setups

    global COST_PRICE
    if EXT_CSV:
        # EXTERNAL replay (Dukascopy): no MT5 needed. Session filter forced OFF (external
        # data is true-UTC, and regime runs use --no-session anyway). Load once to learn
        # the real mean spread, then cost each trade at spread × 1.5 (round-trip + slippage).
        args.no_session = True
        _probe = _load(mt5.TIMEFRAME_M5 if mt5 else 0, 0)   # populates _EXT_MEAN_SPREAD
        COST_PRICE = float(os.getenv("BACKTEST_COST_PX") or (_EXT_MEAN_SPREAD * 1.5) or 0.30)
        print(f"[EXTERNAL] {EXT_CSV} | {len(_probe):,} bars | mean spread {_EXT_MEAN_SPREAD:.4f} "
              f"| cost/trade {COST_PRICE:.4f} px\n")
    else:
        if not _connect():
            return
        si = mt5.symbol_info(MT5_SYMBOL)
        spread_px = (si.spread * si.point) if (si and si.spread and si.point) else 0.0
        COST_PRICE = spread_px * 1.5          # round-trip spread + a ~0.5× slippage haircut
        # What-if override: force a realistic spread when the demo reports 0 (e.g. market
        # near-closed) so tight scalps are costed honestly. BACKTEST_COST_PX = price units.
        _cost_env = os.getenv("BACKTEST_COST_PX")
        if _cost_env:
            COST_PRICE = float(_cost_env)
            print(f"[cost override] COST_PRICE = {COST_PRICE:.5f} px (BACKTEST_COST_PX)")
        acc = mt5.account_info()
        print(f"Connected - {MT5_SYMBOL} on {acc.server if acc else '?'} | walk-forward, confluence>={MIN_BOOSTERS}, "
              f"risk={RISK_PROFILE} | session={'London/NY' if not args.no_session else 'all'} | "
              f"cost/trade {COST_PRICE:.5f} px{' (M5)' if FORCE_M5 else ''}\n")
    try:
        strategies = [args.strategy] if args.strategy else list(SPECS)
        rows = []
        for s in strategies:
            r = backtest(s, args.bars, session_filter=not args.no_session)
            rows.append(r)
            if r.get("error"):
                print(f"  {s:12} - {r['error']}")
            elif r.get("trades", 0) == 0:
                print(f"  {s:12} - 0 trades over {r.get('bars','?')} bars")
            else:
                print(f"  {s:12} {r['interval']:5} | trades {r['trades']:4d} | "
                      f"win {r['win_rate']*100:4.0f}% | avgR {r['avg_R']:+.2f} | "
                      f"PF {r['profit_factor']:.2f} | totalR {r['total_R']:+.1f} "
                      f"(best {r['best_R']:+.1f} / worst {r['worst_R']:+.1f})")
        print("\nReading: PF>1 and avgR>0 = positive expectancy. This is the go/no-go gate for live capital.")
    finally:
        if not EXT_CSV:
            mt5.shutdown()


if __name__ == "__main__":
    main()
