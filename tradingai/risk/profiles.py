"""Risk profiles — switchable live from Telegram via /risk.

Each profile tunes the deterministic signal engine: how wide the ATR stop is,
how strict the trend filter is, the take-profit ladder, how much account risk
to suggest, and the minimum confidence required to surface a trade.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

VALID_PROFILES = ("conservative", "moderate", "aggressive")


@dataclass(frozen=True)
class RiskProfile:
    name: str
    risk_pct: float          # suggested % of equity to risk per trade
    atr_mult: float          # stop distance = atr_mult * ATR(14)
    min_adx: float           # ADX must exceed this for a trend trade
    tp_r_multiples: tuple    # take-profit ladder in R multiples
    tp_close_pct: tuple      # % of position closed at each TP
    confidence_floor: float  # below this, the engine returns "flat" (no trade)
    emoji: str
    # ── active intraday strategy (v2) ──
    strategy_mode: str = "pullback"   # "pullback" (active intraday) | "trend" (selective daily)
    intraday_floor: float = 0.45      # lower floor used by the active intraday mode
    relax_room_r: float = 1.0         # min structure room in R for active mode

    @property
    def primary_rr(self) -> float:
        """Reward:risk to the final take-profit."""
        return float(self.tp_r_multiples[-1])

    def describe(self) -> str:
        return (
            f"{self.emoji} <b>{self.name.title()}</b>\n"
            f"• Risk per trade: <b>{self.risk_pct:g}%</b> of equity\n"
            f"• Stop distance: <b>{self.atr_mult:g}× ATR(14)</b>\n"
            f"• Trend filter: ADX ≥ <b>{self.min_adx:g}</b>\n"
            f"• Take-profits (R): <b>{', '.join(str(r) for r in self.tp_r_multiples)}</b>\n"
            f"• Min confidence to fire: <b>{int(self.confidence_floor * 100)}%</b>"
        )


PROFILES: dict[str, RiskProfile] = {
    "conservative": RiskProfile(
        name="conservative",
        risk_pct=0.5,
        atr_mult=2.0,
        min_adx=25.0,
        tp_r_multiples=(1.0, 2.0, 3.0),
        tp_close_pct=(50.0, 30.0, 20.0),
        confidence_floor=0.62,
        intraday_floor=0.50,
        emoji="🛡️",
    ),
    "moderate": RiskProfile(
        name="moderate",
        risk_pct=0.75,
        atr_mult=1.8,
        min_adx=22.0,
        tp_r_multiples=(1.0, 2.0, 3.0),
        tp_close_pct=(50.0, 30.0, 20.0),
        confidence_floor=0.52,
        intraday_floor=0.45,
        emoji="⚖️",
    ),
    "aggressive": RiskProfile(
        name="aggressive",
        risk_pct=1.0,          # research: never risk >1%/trade — capped for survival
        atr_mult=1.5,
        min_adx=18.0,
        # FIXED 2026-07-08 (live audit): was (1.5, 3.0, 5.0)/(40,35,25) — the 1.5R first rung
        # meant a typical +1R winner never tagged TP1, never locked breakeven, then reversed to
        # a FULL stop (several −$20+ aggressive losses), while 60% chased an unreachable 5R
        # (winners realised a MEDIAN 32% of final TP; 1/39 ever hit it). Now locks at the
        # achievable 1R, banks the bulk there, and tops out at a realistic 3R.
        tp_r_multiples=(1.0, 2.0, 3.0),
        tp_close_pct=(55.0, 30.0, 15.0),
        confidence_floor=0.42,
        intraday_floor=0.40,
        emoji="🔥",
    ),
}


def get_profile(name: str) -> RiskProfile:
    """Return the named profile (default 'moderate'), with the active strategy
    mode applied from config (STRATEGY_MODE: 'pullback' active | 'trend' selective)."""
    p = PROFILES.get((name or "").lower().strip(), PROFILES["moderate"])
    try:
        from config import CONFIG
        mode = (CONFIG.strategy_mode or "pullback").lower()
        if mode in ("pullback", "trend", "orb", "secondentry", "breakout", "firstentry") and mode != p.strategy_mode:
            p = replace(p, strategy_mode=mode)
    except Exception:  # noqa: BLE001
        pass
    return p
