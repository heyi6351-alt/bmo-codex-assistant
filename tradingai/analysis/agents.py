"""Multi-agent analysis pipeline (TradingAgents-style, lightweight).

Flow:
  deterministic engine (per timeframe)            ← analysis/signals.py
        │
  Analyst + Bull/Bear debate  ──► Risk Manager  ──► Synthesizer
        │                              │
   news + sentiment               applies the active risk profile
   deep web research              (can downgrade size or veto)

The LLM reasons over numbers it cannot change; the deterministic Signal is the
source of truth for every price/level.
"""

from __future__ import annotations

import logging

from config import CONFIG
from core.models import LLMReview, NewsItem, Signal, Trade
from data import news as news_mod
from data import social as social_mod
from data.market import MARKET
from risk.profiles import RiskProfile

from . import llm, ml_predictor, research
from .backtest import LABEL_TO_INTERVAL
from .model_catalog import is_tool_capable
from .signals import generate_signal

log = logging.getLogger(__name__)

# Mixed timeframes — one signal per timeframe, headline picked by mode's driver TF.
TIMEFRAMES = ["5min", "15min", "1h", "4h", "1day"]
_DRIVER_TF = {"secondentry": "5min", "pullback": "15min", "orb": "15min"}


# ── helpers ──────────────────────────────────────────────────────────────────
def _choose_headline(per_tf: dict[str, Signal], mode: str = "pullback") -> Signal | None:
    if not per_tf:
        return None
    # Active scalp/intraday modes trade their OWN driver timeframe or STAND ASIDE.
    # We deliberately do NOT fall back to a higher timeframe when the driver chart
    # is flat — that fallback is what produced wide ~$40 H1 stops (and even
    # counter-trend entries) when the M5/M15 setup chart had nothing. A scalper
    # with no setup on its chart takes no trade.
    driver = _DRIVER_TF.get(mode)
    if mode != "trend" and driver:
        return per_tf.get(driver)
    # Trend mode (and any mode without a driver TF): highest-confidence non-flat.
    non_flat = [s for s in per_tf.values() if s.direction != "flat"]
    pool = non_flat or list(per_tf.values())
    return max(pool, key=lambda s: s.confidence)


def _signals_context(per_tf: dict[str, Signal]) -> str:
    lines = []
    for tf, s in per_tf.items():
        i = s.indicators
        lines.append(
            f"[{s.timeframe}] dir={s.direction} conf={s.confidence:.2f} "
            f"close~{s.entry} EMA20={i.ema_fast:.2f} EMA50={i.ema_slow:.2f} "
            f"RSI={i.rsi14:.1f} MACDhist={i.macd_hist:.3f} ADX={i.adx14:.1f} "
            f"Stoch={i.stoch_k:.0f}/{i.stoch_d:.0f} PROC={i.proc:.2f}% "
            f"OBVslope={'+' if i.obv_slope > 0 else '-' if i.obv_slope < 0 else '0'} "
            f"ATR={i.atr14:.3f} S={i.support} R={i.resistance}"
        )
    return "\n".join(lines)


def _news_context(items: list[NewsItem], limit: int = 8) -> str:
    out = []
    for it in items[:limit]:
        tag = it.sentiment_label or "n/a"
        out.append(f"- ({tag}) {it.title} [{it.source}]")
    return "\n".join(out) if out else "No recent headlines retrieved."


# ── main pipeline ──────────────────────────────────────────────────────────────
def run_analysis(symbol: str, profile: RiskProfile, do_research: bool = False,
                 model: str | None = None) -> dict:
    """Full analysis for a symbol. Returns
    {signal, per_tf, news, research}. Blocking — call via asyncio.to_thread.

    ``model`` overrides the analysis "brain" (defaults to CONFIG.model_deep).
    """
    deep_model = model or CONFIG.model_deep
    per_tf: dict[str, Signal] = {}
    for tf in TIMEFRAMES:
        try:
            candles = MARKET.get_candles(symbol, tf, outputsize=220)
            if len(candles) >= 30:
                per_tf[tf] = generate_signal(symbol, candles, profile, tf)
        except Exception as e:  # noqa: BLE001
            log.warning("Signal gen failed for %s %s: %s", symbol, tf, e)

    headline = _choose_headline(per_tf, getattr(profile, "strategy_mode", "pullback"))
    news_items = news_mod.fetch_news(symbol, limit=10)
    social_items = social_mod.fetch_social(symbol, limit=6)
    research_out = {}
    if do_research:
        # Use the chosen model for research only if it does reliable tool calls.
        research_model = deep_model if is_tool_capable(deep_model) else CONFIG.model_research
        research_out = research.deep_research(
            f"What is driving {symbol} right now? Summarise the latest news, macro/Fed/USD "
            f"context, market sentiment, and the key risks over the next 1-3 days.",
            model=research_model,
        )

    # ML predictor (extra vote) on the headline timeframe.
    if headline is not None and ml_predictor.available():
        interval = LABEL_TO_INTERVAL.get(headline.timeframe, "1h")
        ml = ml_predictor.predict(symbol, interval)
        if ml.get("available") and ml.get("direction"):
            headline.ml_direction = ml["direction"]
            headline.ml_prob_up = ml.get("prob_up", -1.0)
            headline.ml_cv_acc = ml.get("cv_accuracy", -1.0)
            # Nudge confidence: agreement boosts, disagreement trims.
            if headline.direction != "flat":
                if ml["direction"] == headline.direction:
                    headline.confidence = min(1.0, round(headline.confidence + 0.05, 2))
                else:
                    headline.confidence = max(0.0, round(headline.confidence - 0.07, 2))

    if headline is not None:
        headline.llm_review = _enrich(symbol, profile, headline, per_tf,
                                      news_items, social_items, research_out, deep_model)
        # The DETERMINISTIC engine decides trade vs no-trade (stable run-to-run).
        # The LLM verdict only ADJUSTS confidence — it never silently flips a
        # real setup to NO TRADE (that caused the approve/veto flip-flop).
        verdict = headline.llm_review.risk_verdict
        if verdict == "veto":
            headline.confidence = round(headline.confidence * 0.6, 2)
        elif verdict == "approve_reduced_size":
            headline.confidence = round(headline.confidence * 0.85, 2)

    return {"signal": headline, "per_tf": per_tf, "news": news_items,
            "social": social_items, "research": research_out}


def _enrich(symbol, profile: RiskProfile, signal: Signal,
            per_tf: dict[str, Signal], news_items, social_items, research_out: dict,
            deep_model: str | None = None) -> LLMReview:
    if not llm.available():
        return LLMReview(summary="(AI commentary unavailable — set NVIDIA_API_KEY to enable it.)")
    deep_model = deep_model or CONFIG.model_deep

    ctx = (
        f"SYMBOL: {symbol}\nRISK PROFILE: {profile.name} "
        f"(risk {profile.risk_pct}%/trade, stop {profile.atr_mult}xATR, min ADX {profile.min_adx})\n\n"
        f"DETERMINISTIC SIGNALS (do NOT change these numbers):\n{_signals_context(per_tf)}\n\n"
        f"HEADLINE SETUP: {signal.direction.upper()} {signal.timeframe} | entry {signal.entry} | "
        f"stop {signal.stop_loss} | TPs {[tp.price for tp in signal.take_profits]} | "
        f"R:R {signal.risk_reward} | confidence {signal.confidence}\n\n"
        f"RECENT NEWS:\n{_news_context(news_items)}\n"
    )
    if social_items:
        avg, label = social_mod.avg_sentiment(social_items)
        ctx += (f"\nSOCIAL CHATTER (avg sentiment {avg:+.2f} {label}):\n"
                f"{_news_context(social_items, limit=5)}\n")
    if signal.ml_prob_up >= 0:
        ctx += (f"\nML PREDICTOR: {signal.ml_direction} "
                f"(P(up)={signal.ml_prob_up:.2f}, walk-forward acc={signal.ml_cv_acc:.2f}). "
                f"Treat as one weak vote, not ground truth.\n")
    if research_out.get("report"):
        ctx += f"\nDEEP RESEARCH BRIEFING:\n{research_out['report'][:1800]}\n"

    review = LLMReview()

    # ONE combined call (analyst + sentiment + bull/bear debate + risk verdict +
    # summary). Collapsing 3 calls into 1 keeps us well under the free-tier rate
    # limit and removes the run-to-run inconsistency from multiple calls.
    try:
        out = llm.complete(
            system=(
                "You are a gold/FX trading desk. The technical DIRECTION is already decided by the engine — "
                "do NOT overturn a strong multi-timeframe trend just because one lower timeframe is flat. "
                "Return JSON ONLY with keys: "
                "summary (ONE sentence, <=30 words: the plan + the single biggest risk; no line breaks), "
                "technical (2-3 sentences on the multi-timeframe picture), "
                "sentiment (1-2 sentences on news/macro tone), "
                "bull_case (strongest argument FOR the direction), "
                "bear_case (strongest argument AGAINST), "
                "verdict (one of 'approve' | 'approve_reduced_size' | 'veto'; use 'veto' ONLY for a genuine "
                "show-stopper such as imminent high-impact news (CPI/FOMC/NFP within hours) or sentiment clearly "
                "OPPOSITE the trade), note (one short sentence on the verdict). Do not invent prices."
            ),
            user=ctx + "\nReturn ONLY the JSON object.",
            model=deep_model,
            temperature=0.3, max_tokens=1100,
        )
        data = llm.parse_json(out)
        review.summary = data.get("summary", "")
        review.technical = data.get("technical", "")
        review.sentiment = data.get("sentiment", "")
        review.bull_case = data.get("bull_case", "")
        review.bear_case = data.get("bear_case", "")
        review.risk_verdict = (data.get("verdict") or "approve").strip().lower()
        review.portfolio_note = data.get("note", "")
    except Exception as e:  # noqa: BLE001
        log.warning("LLM enrich step failed: %s", e)
        review.risk_verdict = "approve"

    return review


# ── open-trade guardian ─────────────────────────────────────────────────────
def assess_trade_threat(trade: Trade, news_items: list[NewsItem]) -> dict:
    """Does any recent news threaten this open position? Returns
    {threat: bool, severity, reason, items}.
    """
    relevant: list[NewsItem] = []
    for it in news_items:
        s = it.sentiment_score
        if s is None:
            continue
        if trade.direction == "long" and s <= -0.3:
            relevant.append(it)
        elif trade.direction == "short" and s >= 0.3:
            relevant.append(it)

    if not relevant:
        return {"threat": False, "severity": "none", "reason": "", "items": []}

    severity, reason = "medium", relevant[0].title
    if llm.available():
        try:
            headlines = "\n".join(f"- {it.title} ({it.sentiment_label})" for it in relevant[:5])
            out = llm.complete(
                system=(
                    "You assess threat to an OPEN trade. Return JSON only with keys: "
                    "threat (true/false), severity ('low'|'medium'|'high'), reason (one sentence). "
                    "Threat=true only if the news materially endangers the position."
                ),
                user=(f"OPEN TRADE: {trade.direction.upper()} {trade.symbol} from {trade.entry} "
                      f"(stop {trade.stop}).\nRECENT ADVERSE HEADLINES:\n{headlines}\nReturn ONLY JSON."),
                temperature=0.3, max_tokens=250,
            )
            data = llm.parse_json(out)
            if "threat" in data:
                return {
                    "threat": bool(data.get("threat")),
                    "severity": (data.get("severity") or "medium").lower(),
                    "reason": data.get("reason", reason),
                    "items": relevant[:3],
                }
        except Exception as e:  # noqa: BLE001
            log.warning("Trade-threat LLM check failed: %s", e)

    return {"threat": True, "severity": severity, "reason": reason, "items": relevant[:3]}
