"""Trade-ticket engine — turns an actionable Signal into a broker-style call.

Safety design (grounded in the research):
- Prices come ONLY from the deterministic engine. We precompute a menu of
  candidate SL/TP levels (ATR ladder + structural swings with a volatility
  buffer + round numbers). LLMs pick by integer id — they never emit prices,
  so they cannot hallucinate a level.
- A small model tournament each proposes {sl_id, tp_ids}; we validate, take a
  trimmed-median SL, build a voted TP ladder, snap everything back to vetted
  candidates, enforce an R:R floor, and fall back to the engine's own ladder if
  consensus fails or the LLM is unavailable.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from config import CONFIG
from core.models import Signal, TradeTicket
from data import social as social_mod
from risk.profiles import RiskProfile

from . import llm
from .model_catalog import by_slug

log = logging.getLogger(__name__)

_DISPLAY = {"XAU": "GOLD", "XAG": "SILVER", "XPT": "PLATINUM", "XPD": "PALLADIUM", "WTI": "OIL"}
# Fast, strong, tool-reliable models for the risk-plan vote.
_TICKET_SLUGS = ["kimi-k2", "qwen35", "glm51"]


def display_name(symbol: str) -> str:
    base, _, quote = symbol.partition("/")
    base, quote = base.upper(), (quote or "").upper()
    if base in _DISPLAY:
        return _DISPLAY[base]
    return f"{base}{quote}" if quote else base


def _dec(price: float) -> int:
    if price < 10:
        return 5
    if price < 1000:
        return 3
    return 2


def _r(price: float, ref: float) -> float:
    return round(price, _dec(ref))


# ── candidate levels ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Candidate:
    id: int
    price: float
    kind: str          # atr | swing | round
    atr_dist: float    # signed (price - entry) / ATR


def _round_levels(lo: float, hi: float, step: float) -> list[float]:
    if step <= 0 or hi <= lo:
        return []
    start = step * (int(lo / step) + 1)
    out, x = [], start
    while x < hi and len(out) < 40:
        out.append(round(x, 4))
        x += step
    return out


def build_candidates(sig: Signal, profile: RiskProfile):
    """Return (sl_candidates, tp_candidates) as id'd menus on the correct sides."""
    E = sig.entry
    atr = max(sig.atr, abs(E) * 0.0005, 1e-6)
    long = sig.direction == "long"
    sup, res = sig.indicators.support, sig.indicators.resistance
    buffer = max(0.5 * atr, CONFIG.ticket_spread_pad)
    step = 5.0 if E >= 1000 else (1.0 if E >= 100 else 0.0)

    # SL candidates (loss side: below E for long, above for short).
    sl_raw: list[tuple[float, str]] = []
    for k in (1.0, 1.5, 2.0, 2.5, 3.0):
        sl_raw.append((E - k * atr if long else E + k * atr, "atr"))
    if long and sup and 0 < sup < E:
        sl_raw.append((sup - buffer, "swing"))
    if (not long) and res and res > E:
        sl_raw.append((res + buffer, "swing"))
    if step:
        for lvl in _round_levels(E - 4 * atr, E, step) if long else _round_levels(E, E + 4 * atr, step):
            sl_raw.append((lvl - buffer if long else lvl + buffer, "round"))

    sl_cands = _finalize(sl_raw, E, atr, long, loss_side=True)

    # TP candidates (profit side).
    tp_raw: list[tuple[float, str]] = []
    for k in (1.0, 1.5, 2.0, 3.0, 4.0, 5.0):
        tp_raw.append((E + k * atr if long else E - k * atr, "atr"))
    if long and res and res > E:
        tp_raw.append((res, "swing"))
    if (not long) and sup and 0 < sup < E:
        tp_raw.append((sup, "swing"))
    if step:
        for lvl in _round_levels(E, E + 5.5 * atr, step) if long else _round_levels(E - 5.5 * atr, E, step):
            tp_raw.append((lvl, "round"))

    tp_cands = _finalize(tp_raw, E, atr, long, loss_side=False)
    return sl_cands, tp_cands


def _finalize(raw, E, atr, long, loss_side) -> list[Candidate]:
    seen, kept = set(), []
    for price, kind in raw:
        p = _r(price, E)
        on_loss = (p < E) if long else (p > E)
        on_profit = (p > E) if long else (p < E)
        if loss_side and not on_loss:
            continue
        if (not loss_side) and not on_profit:
            continue
        dist = abs(E - p) / atr
        if loss_side and not (0.8 <= dist <= 2.6):   # cap stop width (was 3.5 = too wide)
            continue
        if (not loss_side) and dist < 0.5:
            continue
        if p in seen:
            continue
        seen.add(p)
        kept.append((p, kind, (p - E) / atr))
    kept.sort(key=lambda t: abs(t[0] - E))   # nearest first
    return [Candidate(i, p, k, d) for i, (p, k, d) in enumerate(kept)]


# ── tournament ─────────────────────────────────────────────────────────────────
def _models():
    out = [by_slug(s) for s in _TICKET_SLUGS]
    return [m for m in out if m] or []


def _menu(cands: list[Candidate]) -> str:
    return "\n".join(f"  [{c.id}] {c.price}  ({c.kind} {c.atr_dist:+.1f}xATR)" for c in cands)


_SYSTEM = (
    "You are a gold (XAUUSD) risk manager. The trade DIRECTION is already decided. "
    "Choose a stop-loss and a take-profit ladder by picking ONLY from the numbered candidate "
    "levels. You MUST NOT output any price — only integer ids. Reply JSON ONLY:\n"
    '{"valid":true,"direction":"long|short|flat","sl_id":int,"tp_ids":[int,...],"confidence":0-1,"reason":"<=20 words"}\n'
    "Pick the SL just beyond real structure but within a sane ATR band. Choose AS MANY take-profits as the "
    "setup warrants — typically 2 to 8, ordered nearest-to-farthest: MORE rungs for a strong, trending setup "
    "with room to run; FEWER when choppy, low-conviction, or near a key level. Keep a real runner at the far "
    "end. Do not invent ids."
)


def run_ticket_tournament(sig: Signal, sl_cands, tp_cands) -> list[dict]:
    if not llm.available() or not sl_cands or not tp_cands:
        return []
    ctx = (
        f"SYMBOL: {sig.symbol}  DIRECTION (fixed): {sig.direction.upper()}  TF: {sig.timeframe}\n"
        f"ENTRY: {sig.entry}  ATR: {sig.atr}\n"
        f"Structure: support {sig.indicators.support}, resistance {sig.indicators.resistance}\n\n"
        f"SL CANDIDATES (pick ONE id):\n{_menu(sl_cands)}\n\n"
        f"TP CANDIDATES (pick 2-8 ids by conviction, nearest first):\n{_menu(tp_cands)}\n\nReturn ONLY the JSON object."
    )
    proposals = []
    for m in _models():
        try:
            out = llm.complete(_SYSTEM, ctx, model=m.id, temperature=0.3, max_tokens=220)
            data = llm.parse_json(out)
            if data:
                data["_model"] = m.name
                proposals.append(data)
        except Exception as e:  # noqa: BLE001
            log.warning("ticket tournament model %s failed: %s", m.id, e)
    return proposals


# ── aggregation + validation ─────────────────────────────────────────────────────
def _valid_proposal(p: dict, sig: Signal, sl_cands, tp_cands) -> bool:
    if not p.get("valid", False):
        return False
    if (p.get("direction") or "").lower() != sig.direction:
        return False
    try:
        sl_id = int(p["sl_id"])
        tp_ids = [int(i) for i in p["tp_ids"]]
    except (KeyError, TypeError, ValueError):
        return False
    if not (0 <= sl_id < len(sl_cands)) or not tp_ids:
        return False
    if any(not (0 <= i < len(tp_cands)) for i in tp_ids):
        return False
    if not (2 <= len(tp_ids) <= max(2, CONFIG.ticket_max_tps)):
        return False
    # TP ids must be increasing distance (menu already sorted nearest-first).
    dists = [abs(tp_cands[i].price - sig.entry) for i in tp_ids]
    return all(b > a for a, b in zip(dists, dists[1:]))


def _snap(price: float, cands: list[Candidate], entry: float, atr: float):
    best = min(cands, key=lambda c: abs(c.price - price), default=None)
    if best and abs(best.price - price) <= 0.35 * atr:
        return best.price
    return best.price if best else _r(price, entry)


def aggregate_plan(proposals, sl_cands, tp_cands, sig: Signal, profile: RiskProfile) -> TradeTicket | None:
    E, atr = sig.entry, max(sig.atr, 1e-6)
    valid = [p for p in proposals if _valid_proposal(p, sig, sl_cands, tp_cands)]
    n = len(proposals)
    if not valid or len(valid) < max(2, (n + 1) // 2):
        return None  # no consensus → caller falls back

    # SL: trimmed median of chosen distances, snapped to a real candidate.
    sl_dists = sorted(abs(sl_cands[int(p["sl_id"])].price - E) / atr for p in valid)
    if len(sl_dists) >= 5:
        sl_dists = sl_dists[1:-1]
    sl_med = statistics.median(sl_dists)
    final_sl = _snap(E - sl_med * atr if sig.direction == "long" else E + sl_med * atr, sl_cands, E, atr)
    risk = abs(E - final_sl)
    if risk <= 0:
        return None

    # TPs: vote per candidate id, keep those with enough support, ladder by distance.
    votes: dict[int, int] = {}
    for p in valid:
        for i in p["tp_ids"]:
            votes[int(i)] = votes.get(int(i), 0) + 1
    threshold = max(1, (len(valid) * 2 + 2) // 5)  # ~>=40%
    chosen = [i for i, v in votes.items() if v >= threshold] or list(votes.keys())
    prices = sorted({tp_cands[i].price for i in chosen}, key=lambda x: abs(x - E))

    # First TP must pay for the risk: keep rungs >= 0.5R (drop sub-0.5R scalps).
    prices = [px for px in prices if abs(px - E) / risk >= 0.5]
    prices = prices[:CONFIG.ticket_max_tps]
    if len(prices) < 2:
        return None

    # Confidence from agreement (not self-report).
    mean_d = statistics.mean(sl_dists) or 1.0
    disp = min(1.0, (statistics.pstdev(sl_dists) / mean_d) if len(sl_dists) > 1 else 0.0)
    self_conf = statistics.mean([float(p.get("confidence", 0) or 0) for p in valid])
    conf = max(0.0, min(1.0, 0.5 * (len(valid) / n) + 0.3 * (1 - disp) + 0.2 * self_conf))

    reasons = [p.get("reason", "") for p in valid if p.get("reason")]
    note = (reasons[0][:110] if reasons else "Consensus risk plan")
    note += f" · {len(valid)}/{n} models agree"

    return TradeTicket(
        symbol=sig.symbol, display=display_name(sig.symbol),
        side="BUY" if sig.direction == "long" else "SELL",
        entry=sig.entry, stop=final_sl,
        take_profits=[_r(px, E) for px in prices],
        rr=round(abs(prices[-1] - E) / risk, 2),
        risk_pct=profile.risk_pct, confidence=round(conf, 2),
        safety_note=note, valid=True,
    )


# Lower timeframe → smaller ATR → tighter, more tradeable ticket levels.
_TF_RANK = {"M1": 0, "M5": 1, "M15": 2, "M30": 3, "H1": 4, "H4": 5, "D1": 6}


def pick_ticket_signal(result: dict) -> Signal | None:
    """Use the TIGHTEST timeframe that agrees with the headline direction, so
    the ticket's stop isn't an 80-USD swing stop when an intraday one will do."""
    headline = result.get("signal")
    if headline is None or headline.direction not in ("long", "short"):
        return None
    same = [s for s in result.get("per_tf", {}).values() if s.direction == headline.direction]
    if not same:
        return headline
    same.sort(key=lambda s: _TF_RANK.get(s.timeframe, 5))
    return same[0]


def _clean_ladder(E: float, risk: float, long: bool, n: int) -> list[float]:
    """Evenly-spaced, profitable R-ladder: TP1 = 1R, then +0.8R per rung, each
    nudged to a round number ONLY when that keeps the spacing. Guarantees TP1 >= 1R
    and strictly-increasing, well-separated targets (no bunched-up rungs like the
    old 4110/4120 problem)."""
    step = 5.0 if E >= 1000 else (1.0 if E >= 100 else 0.0)
    out: list[float] = []
    prev: float | None = None
    for i in range(n):
        r = 1.0 + 0.8 * i
        p = E + r * risk if long else E - r * risk
        if step:                                   # snap to a round number when it's close
            snapped = round(p / step) * step
            on_side = snapped > E if long else snapped < E
            gap_ok = prev is None or abs(snapped - E) - abs(prev - E) >= 0.45 * risk
            tp1_ok = i > 0 or abs(snapped - E) >= 0.95 * risk   # never pull TP1 below ~1R
            if on_side and gap_ok and tp1_ok and abs(snapped - p) <= 0.5 * step:
                p = snapped
        if prev is None or abs(p - E) > abs(prev - E) + 1e-9:   # keep strictly farther out
            out.append(_r(p, E))
            prev = p
    return out


def deterministic_ticket(sig: Signal, profile: RiskProfile, note: str = "") -> TradeTicket:
    """Smart, LLM-free plan: a stop CAPPED to a sane ATR band (never an 80-pip swing
    stop) + an even, profitable take-profit ladder starting at 1R."""
    long = sig.direction == "long"
    E = sig.entry
    atr = max(sig.atr, abs(E) * 0.0005, 1e-6)
    sl_c, _tp_c = build_candidates(sig, profile)
    if not sl_c:  # last resort: the engine's own validated ladder
        return TradeTicket(
            symbol=sig.symbol, display=display_name(sig.symbol),
            side="BUY" if long else "SELL", entry=E, stop=sig.stop_loss,
            take_profits=[tp.price for tp in sig.take_profits], rr=sig.risk_reward,
            risk_pct=profile.risk_pct, confidence=sig.confidence,
            safety_note=note or "Engine ATR levels.", valid=bool(sig.take_profits and sig.stop_loss))

    # ── Stop: prefer real structure, but NEVER wider than atr_mult×ATR. A 2-3×ATR
    #    swing stop on H1 gold is ~$50 of risk and pushes the TPs out of reach — so
    #    we cap it. ($-risk per trade is unchanged: it's risk% of equity either way;
    #    a tighter stop just means a larger lot and far more reachable targets.)
    cap_dist = profile.atr_mult * atr
    swings = [c for c in sl_c if c.kind == "swing"]
    if swings and abs(E - swings[0].price) <= cap_dist:
        sl_price = swings[0].price                       # structure is tight enough — use it
        sl_basis = "structure + buffer"
    else:
        sl_price = E - cap_dist if long else E + cap_dist
        sl_basis = (f"{profile.atr_mult:g}×ATR (structure too far — capped)"
                    if swings else f"{profile.atr_mult:g}×ATR")
    risk = abs(E - sl_price)
    if risk <= 0:
        risk = cap_dist
        sl_price = E - risk if long else E + risk

    # How many rungs? conviction-driven but kept sane (2..5): more when the trend is
    # strong (room to run), fewer when weak/choppy.
    cap = max(2, min(CONFIG.ticket_max_tps, 5))
    trend_factor = max(0.0, min(1.0, (sig.indicators.adx14 - 18) / 30))  # ADX 18..48 → 0..1
    conviction = 0.6 * sig.confidence + 0.4 * trend_factor
    n = max(2, min(cap, round(2 + conviction * (cap - 2))))

    tps = _clean_ladder(E, risk, long, n)
    if len(tps) < 2:
        tps = [tp.price for tp in sig.take_profits]        # fall back to engine ladder

    default_note = (f"SL {sl_basis} (~{round(risk, 1)} pts risk); {len(tps)} TPs evenly "
                    f"spaced 1R→{round(abs(tps[-1] - E) / risk, 1)}R." if tps else "Engine levels.")
    return TradeTicket(
        symbol=sig.symbol, display=display_name(sig.symbol),
        side="BUY" if long else "SELL", entry=_r(E, E), stop=_r(sl_price, E),
        take_profits=[_r(x, E) for x in tps],
        rr=round(abs(tps[-1] - E) / risk, 2) if tps else sig.risk_reward,
        risk_pct=profile.risk_pct, confidence=sig.confidence,
        safety_note=note or default_note, valid=bool(tps))


def build_ticket(result: dict, profile: RiskProfile, use_tournament: bool = True) -> TradeTicket | None:
    sig = pick_ticket_signal(result)
    if sig is None:
        return None
    if use_tournament and llm.available():
        sl_c, tp_c = build_candidates(sig, profile)
        ticket = aggregate_plan(run_ticket_tournament(sig, sl_c, tp_c), sl_c, tp_c, sig, profile)
        if ticket and ticket.valid:
            return ticket
        return deterministic_ticket(sig, profile, "Ensemble inconclusive — engine levels.")
    return deterministic_ticket(sig, profile)


# ── gating ─────────────────────────────────────────────────────────────────────
def should_emit_ticket(result: dict, profile: RiskProfile) -> tuple[bool, str]:
    """Decide whether to send the actionable ticket (2nd message)."""
    sig = result.get("signal")
    if sig is None or sig.direction not in ("long", "short"):
        return False, "no_trade"
    if sig.confidence < profile.confidence_floor:
        return False, "low_confidence"
    if (sig.llm_review and sig.llm_review.risk_verdict == "veto"):
        return False, "risk_veto"
    # News/social must not be clearly OPPOSITE the trade ("green/go" confirmation).
    items = (result.get("news") or []) + (result.get("social") or [])
    score, _ = social_mod.avg_sentiment(items)
    t = CONFIG.ticket_sentiment_t
    if sig.direction == "long" and score <= -t:
        return False, "sentiment_opposes_long"
    if sig.direction == "short" and score >= t:
        return False, "sentiment_opposes_short"
    return True, "ok"
