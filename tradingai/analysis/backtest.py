"""Backtester — replays the deterministic signal engine over historical candles.

For each closed bar it asks the engine for a signal; on a fresh long/short it
"enters" at that bar's close and then walks forward bar-by-bar until either the
stop or the first take-profit is touched (stop assumed first if both hit in the
same bar — conservative). Reports win-rate, average R, profit factor, expectancy
and max drawdown in R. This makes signal quality measurable, not just asserted.
"""

from __future__ import annotations

import logging

from data.market import MARKET
from risk.profiles import RiskProfile

from .signals import TF_META, generate_signal

log = logging.getLogger(__name__)

# Telegram-friendly timeframe label → provider interval.
LABEL_TO_INTERVAL = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1day",
}


_WIN = 280  # indicator window (EMA200 needs 200) — small enough to stay fast


def simulate(symbol: str, candles: list, profile: RiskProfile, interval: str,
             start: int, end: int, cost_price: float = 0.0) -> list[tuple[int, float]]:
    """Walk bars [start, end). Returns (entry_index, net_R) per trade.

    One position at a time, entry at the signal bar's close, exit at first of
    stop / first-TP (stop assumed first if both hit in one bar). ``cost_price``
    (spread+slippage, in price units) is charged round-trip and converted to R.
    """
    trades: list[tuple[int, float]] = []
    pos = None
    i = start
    while i < end:
        c = candles[i]
        if pos is None:
            sig = generate_signal(symbol, candles[max(0, i - _WIN + 1): i + 1], profile, interval)
            if sig.direction in ("long", "short") and sig.take_profits and sig.stop_loss:
                risk = abs(sig.entry - sig.stop_loss)
                if risk > 0:
                    pos = {"dir": sig.direction, "entry": sig.entry, "stop": sig.stop_loss,
                           "target": sig.take_profits[0].price, "reward_r": sig.take_profits[0].r_multiple,
                           "risk": risk, "i": i}
            i += 1
        else:
            hi, lo = c.high, c.low
            r = None
            if pos["dir"] == "long":
                if lo <= pos["stop"]:
                    r = -1.0
                elif hi >= pos["target"]:
                    r = pos["reward_r"]
            else:
                if hi >= pos["stop"]:
                    r = -1.0
                elif lo <= pos["target"]:
                    r = pos["reward_r"]
            if r is not None:
                trades.append((pos["i"], r - cost_price / pos["risk"]))
                pos = None
            i += 1

    if pos is not None:  # close at the slice's last bar
        last = candles[end - 1].close
        move = (last - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - last)
        trades.append((pos["i"], move / pos["risk"] - cost_price / pos["risk"]))
    return trades


def _default_cost(price: float) -> float:
    """Round-trip spread + slippage in price units (gold ≈ $0.6 on a $4000 quote)."""
    return max(0.3, price * 0.00015)


def backtest(symbol: str, profile: RiskProfile, interval: str = "1h",
             lookback: int = 400, warmup: int = 120, oos: bool = True,
             cost_price: float | None = None) -> dict:
    """Stats dict (or {'error': ...}). Runs the simulation ONCE and splits it into
    in-sample (first 70%) / out-of-sample (last 30%). Costs are modeled."""
    candles = MARKET.get_candles(symbol, interval, outputsize=lookback)
    n = len(candles)
    if n < warmup + 30:
        return {"error": f"Not enough history for {symbol} {interval} "
                         f"(got {n} bars, need ≥ {warmup + 30})."}
    if cost_price is None:
        cost_price = _default_cost(candles[-1].close)

    all_tr = simulate(symbol, candles, profile, interval, warmup, n, cost_price)
    stats = _stats(symbol, interval, n, [r for _, r in all_tr])
    stats["cost_price"] = round(cost_price, 3)
    if oos:
        split = warmup + int((n - warmup) * 0.7)
        stats["in_sample"] = _stats(symbol, interval, split - warmup, [r for idx, r in all_tr if idx < split])
        stats["out_of_sample"] = _stats(symbol, interval, n - split, [r for idx, r in all_tr if idx >= split])
    return stats


def _stats(symbol: str, interval: str, bars: int, trades: list[float]) -> dict:
    label = next((lbl for lbl, iv in LABEL_TO_INTERVAL.items() if iv == interval), interval)
    if not trades:
        return {"symbol": symbol, "interval": label, "bars": bars, "n_trades": 0,
                "note": "No trades were triggered over this window."}

    wins = [r for r in trades if r > 0]
    losses = [r for r in trades if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_r = sum(trades)

    # Max drawdown on the cumulative-R equity curve.
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in trades:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "symbol": symbol, "interval": label, "bars": bars,
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_r": total_r / len(trades),
        "total_r": total_r,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "max_drawdown_r": max_dd,
        "best_r": max(trades),
        "worst_r": min(trades),
    }
