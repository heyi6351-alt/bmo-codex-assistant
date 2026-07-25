"""Plain data models used across the app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

DISCLAIMER = (
    "⚠️ <b>NOT FINANCIAL ADVICE.</b> Educational/informational only. "
    "Trading gold/FX (incl. CFDs) is high-risk and you can lose all your capital. "
    "Past performance does not guarantee future results. Do your own research."
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── User settings ───────────────────────────────────────────────────────────
@dataclass
class UserSettings:
    chat_id: int
    base: str = "XAU"
    quote: str = "USD"
    risk: str = "aggressive"          # conservative | moderate | aggressive
    digests_enabled: bool = True
    model: str = ""                   # analysis model id; "" → use config default
    news_interval_min: int = 5        # per-user breaking-news cadence (minutes)
    last_news_at: str = ""            # last breaking-news push timestamp
    updated_at: str = field(default_factory=utcnow_iso)

    @property
    def symbol(self) -> str:
        """Combined market symbol, e.g. 'XAU/USD'."""
        return f"{self.base}/{self.quote}"


# ── Market data ───────────────────────────────────────────────────────────────
@dataclass
class Candle:
    dt: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


# ── News ──────────────────────────────────────────────────────────────────────
@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: str = ""
    summary: str = ""
    sentiment_score: Optional[float] = None   # -1..1
    sentiment_label: str = ""                 # Bearish .. Bullish


# ── Open trades (monitored by the guardian) ────────────────────────────────────
@dataclass
class Trade:
    chat_id: int
    symbol: str
    direction: str                  # "long" | "short"
    entry: float
    stop: Optional[float] = None
    take_profit: Optional[float] = None
    size_note: str = ""
    status: str = "open"            # "open" | "closed"
    id: Optional[int] = None
    opened_at: str = field(default_factory=utcnow_iso)
    last_alert_at: str = ""


# ── Trading signal (structured) ─────────────────────────────────────────────────
@dataclass
class TakeProfit:
    price: float
    r_multiple: float
    close_pct: float


@dataclass
class Indicators:
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema200: float = 0.0
    rsi14: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    adx14: float = 0.0
    atr14: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    support: float = 0.0
    resistance: float = 0.0
    # Extra indicators (inspired by E-TRADIFY's feature set).
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    proc: float = 0.0          # Price Rate of Change (%)
    obv: float = 0.0           # On-Balance Volume (cumulative)
    obv_slope: float = 0.0     # recent OBV change (volume confirmation)
    adl: float = 0.0           # Accumulation/Distribution Line (cumulative)
    adl_slope: float = 0.0     # recent ADL change


@dataclass
class LLMReview:
    technical: str = ""
    sentiment: str = ""
    bull_case: str = ""
    bear_case: str = ""
    risk_verdict: str = ""          # approve | approve_reduced_size | veto
    portfolio_note: str = ""
    summary: str = ""


@dataclass
class TradeTicket:
    """Broker-style, copy-pasteable trade call derived from a Signal + risk plan."""
    symbol: str                     # e.g. "XAU/USD"
    display: str                    # e.g. "GOLD"
    side: str                       # "BUY" | "SELL"
    entry: float                    # approximate ("around") entry
    stop: float
    take_profits: list[float]       # ordered in trade direction (up to 5)
    rr: float = 0.0                 # reward:risk to the final TP
    risk_pct: float = 1.0
    confidence: float = 0.0         # 0..1 (agreement across the risk-plan tournament)
    safety_note: str = ""
    valid: bool = True
    generated_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Signal:
    symbol: str
    timeframe: str                  # M15 | H1 | H4 | D1
    direction: str                  # long | short | flat
    entry: float
    stop_loss: float
    take_profits: list[TakeProfit]
    risk_reward: float
    confidence: float               # 0..1 heuristic agreement (NOT win probability)
    atr: float
    indicators: Indicators
    invalidation_price: float
    invalidation_reason: str
    position_size: dict             # {risk_pct, formula, note}
    regime_ok: bool                 # passed ADX trend filter
    style: str = ""                 # scalp | intraday | swing
    # Optional ML predictor output (extra analyst vote).
    ml_direction: str = ""          # long | short | ""
    ml_prob_up: float = -1.0        # probability price is higher in `horizon` bars
    ml_cv_acc: float = -1.0         # walk-forward accuracy estimate
    llm_review: LLMReview = field(default_factory=LLMReview)
    generated_at: str = field(default_factory=utcnow_iso)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return asdict(self)
