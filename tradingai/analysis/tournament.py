"""Model tournament — several NVIDIA models read the same setup and vote.

Inspired by NexusTrade's "AI Strategy Tournament". Each model gets the same
deterministic multi-timeframe technical context and returns a JSON opinion;
we aggregate a confidence-weighted consensus. One LLM call per model, so the
shared rate limiter keeps us within the free tier.
"""

from __future__ import annotations

import logging

from data.market import MARKET
from risk.profiles import RiskProfile

from . import llm
from .agents import TIMEFRAMES, _choose_headline, _signals_context
from .model_catalog import CATALOG
from .signals import generate_signal

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are a professional trader. Given the multi-timeframe technical data, reply with "
    'JSON ONLY: {"direction":"long|short|flat","confidence":0-1,"reason":"one short sentence"}. '
    "Do not invent prices; base your call on the data provided."
)


# Vote weight by tier — stronger/heavier reasoners count a little more.
_TIER_WEIGHT = {"⭐ top": 1.3, "⭐ fast": 1.1, "🐢 heavy": 1.2, "strong": 1.0, "⚡ fast": 0.9}


def tournament_models(mode: str = "fast"):
    """mode='all' → every catalogued model; otherwise the fast (non-🐢) set."""
    if mode == "all":
        return list(CATALOG)
    return [m for m in CATALOG if not m.tier.startswith("🐢")][:6]


def run_tournament(symbol: str, profile: RiskProfile, mode: str = "fast") -> dict:
    if not llm.available():
        return {"error": "AI unavailable — set NVIDIA_API_KEY."}

    per_tf = {}
    for tf in TIMEFRAMES:
        try:
            candles = MARKET.get_candles(symbol, tf, outputsize=200)
            if len(candles) >= 30:
                per_tf[tf] = generate_signal(symbol, candles, profile, tf)
        except Exception as e:  # noqa: BLE001
            log.warning("tournament signal %s %s failed: %s", symbol, tf, e)
    if not per_tf:
        return {"error": f"No market data for {symbol}."}

    headline = _choose_headline(per_tf)
    ctx = f"SYMBOL: {symbol}\nDETERMINISTIC SIGNALS (do not change numbers):\n{_signals_context(per_tf)}\n"

    models = tournament_models(mode)
    votes = []
    for m in models:
        try:
            out = llm.complete(system=_SYSTEM, user=ctx + "\nReturn ONLY the JSON object.",
                               model=m.id, temperature=0.4, max_tokens=200)
            data = llm.parse_json(out)
            d = (data.get("direction") or "").lower()
            if d not in ("long", "short", "flat"):
                d = "flat"
            conf = float(data.get("confidence", 0) or 0)
            votes.append({"model": m.name, "tier": m.tier, "direction": d,
                          "weight": _TIER_WEIGHT.get(m.tier, 1.0),
                          "confidence": round(max(0.0, min(1.0, conf)), 2),
                          "reason": (data.get("reason", "") or "")[:160]})
        except Exception as e:  # noqa: BLE001
            log.warning("tournament model %s failed: %s", m.id, e)
            votes.append({"model": m.name, "tier": m.tier, "direction": "error",
                          "weight": 0.0, "confidence": 0.0, "reason": str(e)[:60]})

    # Consensus weighted by both model tier and self-reported confidence.
    score = {"long": 0.0, "short": 0.0, "flat": 0.0}
    for v in votes:
        if v["direction"] in score:
            score[v["direction"]] += v["weight"] * max(v["confidence"], 0.1)
    consensus = max(score, key=score.get) if any(score.values()) else "flat"
    valid = [v for v in votes if v["direction"] in ("long", "short", "flat")]
    agree = sum(1 for v in valid if v["direction"] == consensus)

    return {"symbol": symbol, "headline": headline, "votes": votes, "mode": mode,
            "consensus": consensus, "agree": agree, "total": len(valid)}
