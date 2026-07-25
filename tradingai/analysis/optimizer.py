"""Parameter optimizer (inspired by NexusTrade's genetic optimizer).

Grid-searches the risk-engine knobs (ATR-stop multiple, ADX trend filter,
confidence floor) using the backtester's ``simulate`` as the fitness function
on the in-sample window, then VALIDATES the winner on a held-out out-of-sample
window so we don't reward overfitting. Makes ZERO LLM calls — pure deterministic
replay — so it never touches the NVIDIA rate limit.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace

from data.market import MARKET
from risk.profiles import RiskProfile

from .backtest import LABEL_TO_INTERVAL, _stats, simulate

log = logging.getLogger(__name__)

ATR_MULTS = [1.2, 1.5, 1.8, 2.2, 2.5]
MIN_ADXS = [15.0, 20.0, 25.0, 30.0]
CONF_FLOORS = [0.40, 0.50, 0.60]


def _fitness(trades: list[float]) -> float:
    """Reward consistent edge with enough samples; punish thin sample sizes."""
    n = len(trades)
    if n < 5:
        return float("-inf")
    mean = sum(trades) / n
    return mean * math.sqrt(n)


def optimize(symbol: str, base_profile: RiskProfile, interval: str = "1h",
             lookback: int = 360, warmup: int = 90) -> dict:
    candles = MARKET.get_candles(symbol, interval, outputsize=lookback)
    n = len(candles)
    if n < warmup + 60:
        return {"error": f"Not enough history ({n} bars) to optimize {symbol} {interval}."}

    split = warmup + int((n - warmup) * 0.7)
    label = next((lbl for lbl, iv in LABEL_TO_INTERVAL.items() if iv == interval), interval)

    best = None
    tested = 0
    for am in ATR_MULTS:
        for adx in MIN_ADXS:
            for cf in CONF_FLOORS:
                prof = replace(base_profile, atr_mult=am, min_adx=adx, confidence_floor=cf)
                trades = [r for _, r in simulate(symbol, candles, prof, interval, warmup, split)]
                score = _fitness(trades)
                tested += 1
                if best is None or score > best["score"]:
                    best = {"score": score, "atr_mult": am, "min_adx": adx,
                            "confidence_floor": cf, "profile": prof,
                            "train": _stats(symbol, interval, split - warmup, trades)}

    if best is None or best["score"] == float("-inf"):
        return {"error": "No parameter set produced enough trades to optimize on."}

    # Validate the winner + the current settings on the held-out window.
    best["oos"] = _stats(symbol, interval, n - split,
                         [r for _, r in simulate(symbol, candles, best["profile"], interval, split, n)])
    best["baseline_oos"] = _stats(symbol, interval, n - split,
                                  [r for _, r in simulate(symbol, candles, base_profile, interval, split, n)])
    best.pop("profile", None)  # not serialisable / not needed downstream
    best.update({"tested": tested, "interval": label, "symbol": symbol,
                 "base_profile": base_profile.name})
    return best
