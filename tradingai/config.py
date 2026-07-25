"""Central configuration.

Loads the local ``.env`` file and exposes a single typed ``CONFIG`` object that
the rest of the app imports. All secrets and tunables live here so nothing else
has to touch ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

# Load .env beside this file (no-op if it does not exist).
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str | None = None) -> str | None:
    """Return env var, treating empty string as unset."""
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    """Truthy env parse: 1/true/yes/on → True, 0/false/no/off → False."""
    raw = _get(name, None)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_int_list(name: str) -> list[int]:
    out: list[int] = []
    for part in (_get(name, "") or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass  # ignore malformed entries
    return out


@dataclass(frozen=True)
class Config:
    # ── Telegram ────────────────────────────────────────────────────────
    telegram_bot_token: str = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN", "") or "")
    allowed_user_ids: list[int] = field(default_factory=lambda: _get_int_list("TELEGRAM_ALLOWED_USER_IDS"))
    # Single supreme owner — full control, manages admins from Telegram. 0 = unset.
    owner_id: int = field(default_factory=lambda: _get_int("TELEGRAM_OWNER_ID", 0))
    # Channel/group the bot posts to via /post. "-100…" id or "@channelusername".
    # Owner can change it at runtime with /setchannel (persisted in the DB).
    channel_id: str = field(default_factory=lambda: _get("TELEGRAM_CHANNEL_ID", "") or "")

    # ── NVIDIA NIM (OpenAI-compatible) ──────────────────────────────────
    # Multiple free NVIDIA accounts double the exact-model quota: llm.chat rotates
    # across them (key1 429 → key2 → …) BEFORE falling to other providers. Add more
    # via NVIDIA_API_KEY_2 or a comma-list in NVIDIA_API_KEYS (see providers.py).
    nvidia_api_key: str = field(default_factory=lambda: _get("NVIDIA_API_KEY", "") or "")
    nvidia_api_key_2: str = field(default_factory=lambda: _get("NVIDIA_API_KEY_2", "") or "")
    nvidia_base_url: str = field(
        default_factory=lambda: _get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    )
    # Analysis "brain" for the multi-agent read. Default minimax-m3 — live-probed
    # 2026-07-09 as the fastest NVIDIA model returning clean content on this account
    # (kimi-k2.6 = permanent 404, qwen = timeout). Switchable per chat via /model.
    model_deep: str = field(
        default_factory=lambda: _get("NVIDIA_MODEL_DEEP", "minimaxai/minimax-m3")
    )
    # Fast model for quick tasks + the safe fallback when a chosen model 404s.
    model_quick: str = field(
        default_factory=lambda: _get("NVIDIA_MODEL_QUICK", "minimaxai/minimax-m3")
    )
    # Tool-calling model for the web-research loop.
    model_research: str = field(
        default_factory=lambda: _get("NVIDIA_MODEL_RESEARCH", "minimaxai/minimax-m3")
    )
    # ── EXTRA FREE PROVIDERS (automatic failover when NVIDIA throttles) ──
    # All OpenAI-compatible + genuinely free (no card). llm.chat walks the chain
    # below in order and uses the first provider that answers, so a NVIDIA 429 no
    # longer means "stand aside". Leave a key blank to skip that provider — with
    # none set, the bot is NVIDIA-only (unchanged). See analysis/providers.py for
    # the model-id map. Get keys: Cloudflare dash → AI, console.groq.com,
    # cloud.cerebras.ai, openrouter.ai/keys.
    cloudflare_account_id: str = field(default_factory=lambda: _get("CLOUDFLARE_ACCOUNT_ID", "") or "")
    cloudflare_api_key: str = field(default_factory=lambda: _get("CLOUDFLARE_API_KEY", "") or "")
    groq_api_key: str = field(default_factory=lambda: _get("GROQ_API_KEY", "") or "")
    cerebras_api_key: str = field(default_factory=lambda: _get("CEREBRAS_API_KEY", "") or "")
    openrouter_api_key: str = field(default_factory=lambda: _get("OPENROUTER_API_KEY", "") or "")
    # Failover order (best-first). NVIDIA (exact models, fast) → Groq (fast) →
    # Cloudflare (real Kimi, ~25s latency but 300 RPM throughput — under congestion
    # throughput beats latency) → Cerebras LAST among the real providers (4 RPM quota:
    # putting it earlier serialized every call into a multi-minute queue when the fast
    # providers were 429-cooling). llm._throttle also SKIPS any provider whose queue
    # wait exceeds ~8s, so no caller ever piles up behind a tiny quota again.
    llm_provider_order: str = field(default_factory=lambda: _get(
        "LLM_PROVIDER_ORDER", "nvidia,groq,cloudflare,cerebras,openrouter") or "nvidia")

    # Hard client-side throttle (PER PROCESS). We run one process PER SYMBOL, and each
    # has its own limiter — so with 2 symbols the default must be ~half NVIDIA's ~40/min
    # free-tier ceiling to avoid combined 429s. 18 × 2 symbols = 36 < 40 (headroom). The
    # council (3 votes) fires only on the rare autonomous origination, so it fits.
    nvidia_max_rpm: int = field(default_factory=lambda: _get_int("NVIDIA_MAX_RPM", 18))
    # GLOBAL cross-process ceiling (ALL symbol processes share one 60s window via a lock
    # file) — this is the real 40/min guard. A call WAITS for a free global slot instead
    # of firing and getting 429'd. 36 leaves headroom under NVIDIA's ~40 bursty limit.
    nvidia_global_max_rpm: int = field(default_factory=lambda: _get_int("NVIDIA_GLOBAL_MAX_RPM", 36))

    # ── Kimi pre-trade confirmation (LLM proposes, algorithms dispose) ───
    # When a gated setup is about to be placed, hand it to Kimi for a full
    # verdict (confirm/reject/modify/flip/propose-new) + WHY + realistic profit.
    # Every LLM proposal is re-validated by deterministic clamps before it
    # reaches the broker; the profit figure is computed from real levels.
    kimi_confirm_enabled: bool = field(default_factory=lambda: _get_bool("KIMI_CONFIRM_ENABLED", True))
    # AI OPEN-TRADE MANAGER: let the AI (Kimi failover chain) actively manage LIVE trades —
    # close early (bank a winner / cut a loser) or move SL/TP — with FULL authority. Every
    # SL/TP it proposes is re-validated by deterministic clamps (stop may only reduce risk;
    # target must be realistic) before it reaches the broker. The deterministic guardian +
    # breakeven/ladder still run underneath as the 5-second safety net. ON by default.
    ai_manage_enabled: bool = field(default_factory=lambda: _get_bool("AI_MANAGE_ENABLED", True))
    # Fallback chain (best-first) tried in order when a model is down/rate-limited.
    # Live-verified, JSON-reliable, fast NVIDIA-hosted picks (avoids the >30s ones).
    # Role: FAST per-scan confirm. minimax-m3 first (only NVIDIA model responding fast +
    # clean on this account, ~0.8s), then GLM via Cerebras + gpt-oss multi-provider.
    # (2026-07-09 probe: qwen=timeout, kimi=404 → replaced; speed is critical on this path.)
    kimi_confirm_models: str = field(default_factory=lambda: _get(
        "KIMI_CONFIRM_MODELS",
        "minimaxai/minimax-m3,z-ai/glm-5.1,openai/gpt-oss-120b",
    ) or "")
    # Cap LLM calls per 60s scan (highest-conviction first); beyond it, gated
    # trades still place WITHOUT Kimi so the cap never blocks trading.
    kimi_max_confirms_per_scan: int = field(default_factory=lambda: _get_int("KIMI_MAX_CONFIRMS_PER_SCAN", 2))
    # Include one fast web_search() (Tavily) in the snapshot handed to Kimi.
    kimi_confirm_web: bool = field(default_factory=lambda: _get_bool("KIMI_CONFIRM_WEB", True))
    # If ALL models fail/time out: True → place the engine's already-validated
    # trade anyway (don't miss a gated setup on an outage); False → stand aside.
    kimi_confirm_fail_open: bool = field(default_factory=lambda: _get_bool("KIMI_CONFIRM_FAIL_OPEN", True))
    # Min combo history before a real win-rate is trusted for EV; else use the
    # net break-even p* and label the profit an unproven hypothesis.
    kimi_winrate_min_trades: int = field(default_factory=lambda: _get_int("KIMI_WINRATE_MIN_TRADES", 20))
    # Hard wall-clock cap (seconds) on a single confirmation model call, so a hung
    # or throttled request can never stall the entry scan / stop-management loop.
    kimi_confirm_timeout: float = field(default_factory=lambda: _get_float("KIMI_CONFIRM_TIMEOUT", 18.0))

    # ── MODEL COUNCIL (multi-LLM vote + role specialisation) ─────────────
    # A panel votes YES/NO on a trade; place only on consensus. Curbs one model's
    # over-trading. Levels stay deterministic — the panel only decides IF, not the price.
    council_enabled: bool = field(default_factory=lambda: _get_bool("COUNCIL_ENABLED", True))
    # Panel: 3 DIVERSE models that each reach a WORKING provider fast (2026-07-09 probe —
    # kimi=404, qwen=timeout on this account). minimax-m3 (NVIDIA ~0.8s), glm (skips dead
    # NVIDIA → Cerebras glm-4.7), gpt-oss (multi-provider). Keeps voting diversity + speed.
    council_panel: str = field(default_factory=lambda: _get(
        "COUNCIL_PANEL",
        "minimaxai/minimax-m3,z-ai/glm-5.1,openai/gpt-oss-120b") or "")
    council_min_agree: int = field(default_factory=lambda: _get_int("COUNCIL_MIN_AGREE", 2))
    council_timeout: float = field(default_factory=lambda: _get_float("COUNCIL_TIMEOUT", 14.0))
    # Autonomous ORIGINATOR chain — GLM first (best logic, doesn't over-trade per research),
    # via Cerebras (dead on NVIDIA), then minimax-m3 (fast NVIDIA) + gpt-oss as fallback.
    # (2026-07-09: kimi=404, qwen=timeout on this account — replaced.)
    ai_originator_models: str = field(default_factory=lambda: _get(
        "AI_ORIGINATOR_MODELS",
        "z-ai/glm-5.1,minimaxai/minimax-m3,openai/gpt-oss-120b") or "")
    # ── DAILY MACRO-REGIME PASS (once per session, sets the day's bias per symbol) ──
    macro_pass_enabled: bool = field(default_factory=lambda: _get_bool("MACRO_PASS_ENABLED", True))
    macro_model: str = field(default_factory=lambda: _get(
        "MACRO_MODEL", "minimaxai/minimax-m3") or "minimaxai/minimax-m3")
    macro_refresh_hours: float = field(default_factory=lambda: _get_float("MACRO_REFRESH_HOURS", 6.0))

    # ── AUTONOMOUS KIMI TRADER (originates its OWN trades — DEMO testing) ─
    # Kimi decides from scratch whether to open a gold trade; every proposal is
    # still clamped by validate_levels + real-$ estimate before the broker.
    # HONEST NOTE: autonomous LLM trading has NO proven edge (see project memory);
    # this is an experimental, guardrailed, demo-first capability. All values are
    # overridable at runtime from Telegram (/ai* commands write to app_config).
    ai_autonomous_enabled: bool = field(default_factory=lambda: _get_bool("AI_AUTONOMOUS_ENABLED", True))
    # "both"/"primary" = decide every scan (alongside the engine); "fallback" =
    # only decide when the deterministic engine produced ZERO setups this scan.
    ai_autonomous_mode: str = field(default_factory=lambda: (_get("AI_AUTONOMOUS_MODE", "both") or "both").lower())
    ai_risk_pct: float = field(default_factory=lambda: _get_float("AI_RISK_PCT", 0.5))          # per-trade risk cap (<=1.0)
    # Daily-loss kill-switch: OFF by default (user choice) — enable with /ai killswitch on.
    ai_kill_switch_enabled: bool = field(default_factory=lambda: _get_bool("AI_KILL_SWITCH_ENABLED", False))
    ai_daily_loss_kill_usd: float = field(default_factory=lambda: _get_float("AI_DAILY_LOSS_KILL_USD", 200.0))  # $ limit when ON
    ai_max_concurrent: int = field(default_factory=lambda: _get_int("AI_MAX_CONCURRENT", 1))    # max open AI trades
    ai_min_minutes_between: int = field(default_factory=lambda: _get_int("AI_MIN_MINUTES_BETWEEN", 0))   # cooldown (0 = none)
    # How often the autonomous trader EVALUATES the market (own background thread).
    ai_scan_seconds: int = field(default_factory=lambda: _get_int("AI_SCAN_SECONDS", 30))
    # Total LLM wall-clock budget for ONE autonomous evaluation. Runs in its OWN thread,
    # so it can afford far more than the entry-scan confirm (18s): when the fast providers
    # (NVIDIA keys, Groq) are in 429-cooldown, the only one left is Cloudflare's real Kimi
    # at ~25s — an 18s budget cut it off mid-answer, logging "models unavailable" hundreds
    # of times. 45s lets the slow-but-reliable provider actually finish.
    ai_decide_timeout: float = field(default_factory=lambda: _get_float("AI_DECIDE_TIMEOUT", 45.0))
    ai_min_confidence: int = field(default_factory=lambda: _get_int("AI_MIN_CONFIDENCE", 72))   # 0-100 floor
    ai_min_rr: float = field(default_factory=lambda: _get_float("AI_MIN_RR", 1.5))              # reward:risk floor
    # Future/real-money: True → an AI-originated trade waits for /aiapprove in Telegram
    # before it is placed. Default False (auto-place) per the demo-testing phase.
    ai_require_approval: bool = field(default_factory=lambda: _get_bool("AI_REQUIRE_APPROVAL", False))
    ai_approval_timeout_min: int = field(default_factory=lambda: _get_int("AI_APPROVAL_TIMEOUT_MIN", 20))

    # London/NY session filter for the MT5 trader. OFF by default (user choice) → trade
    # 24h whenever the market is actually open. ON = only ~07:00–21:00 UTC. Toggle live
    # from Telegram with /session on|off.
    session_filter_enabled: bool = field(default_factory=lambda: _get_bool("SESSION_FILTER_ENABLED", False))

    # ── Data / news / search providers ──────────────────────────────────
    twelvedata_api_key: str = field(default_factory=lambda: _get("TWELVEDATA_API_KEY", "") or "")
    alphavantage_key: str = field(default_factory=lambda: _get("ALPHAVANTAGE_KEY", "") or "")
    tavily_api_key: str = field(default_factory=lambda: _get("TAVILY_API_KEY", "") or "")
    exa_api_key: str = field(default_factory=lambda: _get("EXA_API_KEY", "") or "")
    firecrawl_api_key: str = field(default_factory=lambda: _get("FIRECRAWL_API_KEY", "") or "")
    oanda_api_key: str = field(default_factory=lambda: _get("OANDA_API_KEY", "") or "")
    oanda_account_id: str = field(default_factory=lambda: _get("OANDA_ACCOUNT_ID", "") or "")
    oanda_env: str = field(default_factory=lambda: _get("OANDA_ENV", "practice"))
    x_bearer_token: str = field(default_factory=lambda: _get("X_BEARER_TOKEN", "") or "")

    # ── Defaults / behaviour ────────────────────────────────────────────
    default_base: str = field(default_factory=lambda: (_get("DEFAULT_BASE", "XAU") or "XAU").upper())
    default_quote: str = field(default_factory=lambda: (_get("DEFAULT_QUOTE", "USD") or "USD").upper())
    default_risk: str = field(default_factory=lambda: (_get("DEFAULT_RISK", "aggressive") or "aggressive").lower())
    # "pullback" = active intraday (frequent signals) | "trend" = selective daily (rare).
    strategy_mode: str = field(default_factory=lambda: (_get("STRATEGY_MODE", "pullback") or "pullback").lower())

    digest_interval_min: int = field(default_factory=lambda: _get_int("DIGEST_INTERVAL_MIN", 5))
    monitor_interval_min: int = field(default_factory=lambda: _get_int("MONITOR_INTERVAL_MIN", 5))
    # Default per-user breaking-news cadence (each user overrides via /newsfreq).
    default_news_interval_min: int = field(default_factory=lambda: _get_int("NEWS_POLL_INTERVAL_MIN", 5))

    # ── Trade-ticket tuning ─────────────────────────────────────────────
    ticket_sentiment_t: float = field(default_factory=lambda: float(_get("TICKET_SENTIMENT_T", "0.30")))
    ticket_spread_pad: float = field(default_factory=lambda: float(_get("TICKET_SPREAD_PAD", "0.30")))
    ticket_max_tps: int = field(default_factory=lambda: _get_int("TICKET_MAX_TPS", 8))  # CAP; actual count is logic-decided
    ticket_cooldown_min: int = field(default_factory=lambda: _get_int("TICKET_COOLDOWN_MIN", 30))

    # ── Paths ───────────────────────────────────────────────────────────
    db_path: str = field(default_factory=lambda: str(STORAGE_DIR / "bot.db"))

    # ── Helpers ─────────────────────────────────────────────────────────
    @property
    def default_symbol(self) -> str:
        """Combined symbol, e.g. 'XAU/USD'."""
        return f"{self.default_base}/{self.default_quote}"

    def missing_required(self) -> list[str]:
        """Required keys that are not set (the bot cannot run without these)."""
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.nvidia_api_key:
            missing.append("NVIDIA_API_KEY")
        return missing

    def missing_recommended(self) -> list[str]:
        """Recommended keys that degrade features if absent (bot still runs)."""
        out = []
        if not self.twelvedata_api_key:
            out.append("TWELVEDATA_API_KEY (price/candles -> falls back to yfinance)")
        if not self.alphavantage_key:
            out.append("ALPHAVANTAGE_KEY (news sentiment -> falls back to RSS only)")
        if not self.tavily_api_key:
            out.append("TAVILY_API_KEY (deep web research disabled)")
        if not self.owner_id and not self.allowed_user_ids:
            out.append("TELEGRAM_OWNER_ID (no owner set — bot would respond to ANYONE)")
        return out


CONFIG = Config()
