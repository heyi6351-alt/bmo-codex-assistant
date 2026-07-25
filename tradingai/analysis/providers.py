"""FREE multi-provider LLM failover registry.

We run on FREE tiers only. Any single provider can throttle (NVIDIA's free tier
429s under load, Groq/OpenRouter have daily caps, Cerebras is 5 RPM). So instead
of standing aside on a 429, ``llm.chat`` walks an ORDERED chain of free providers
and uses the first one that answers.

Design:
  • Every model chain in config uses a CANONICAL NVIDIA id (unchanged).
  • ``MODEL_MAP`` translates that canonical id to each provider's real id.
  • If a provider doesn't host the exact model, ``PROVIDER_DEFAULT`` gives it a
    capable general free model (gpt-oss-120b everywhere) so uptime is maximised —
    the council/confirm logic is model-agnostic, and the trade message always
    prints which model actually answered, so it stays transparent.
  • A provider is only ever tried if its API key is configured. With no extra
    keys set, the chain is NVIDIA-only → byte-identical to the old behaviour.

All endpoints are OpenAI-compatible, so one ``openai`` client per provider works.
Model ids drift on these platforms — every id here is overridable via env
(<PROVIDER>_MODEL_<SLUG>) so you can correct one without a code change.
"""

from __future__ import annotations

import itertools
import os
import threading
from dataclasses import dataclass

from config import CONFIG

# Round-robin cursor so consecutive calls START on a DIFFERENT NVIDIA key — the two
# free accounts share load evenly (≈2× smooth throughput) instead of hammering
# nvidia_0 into a 429 while nvidia_1 sits idle. rl_key stays pinned to the physical
# key index so each account keeps its own throttle window + 429 breaker.
_nv_rr = itertools.count()
_nv_rr_lock = threading.Lock()


@dataclass(frozen=True)
class Target:
    provider: str      # logical provider (base url + RPM-cap lookup)
    base_url: str
    api_key: str
    model_id: str
    rl_key: str        # UNIQUE rate-limit / circuit-breaker key. For NVIDIA's
                       # rotating keys this is "nvidia_0", "nvidia_1", … so each
                       # account gets its own 429 breaker + throttle window.


def nvidia_keys() -> list[str]:
    """All configured NVIDIA API keys (rotated in order). Sources: NVIDIA_API_KEY
    (may itself be a comma-list), NVIDIA_API_KEY_2, and NVIDIA_API_KEYS."""
    keys: list[str] = []

    def _add(raw: str | None) -> None:
        for k in (raw or "").split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)

    _add(CONFIG.nvidia_api_key)
    _add(getattr(CONFIG, "nvidia_api_key_2", ""))
    _add(os.getenv("NVIDIA_API_KEYS", ""))
    return keys


# ── Per-provider GLOBAL request-per-minute caps (shared across all symbol
# processes via a lock-file window in llm.py). Kept under each free tier's real
# ceiling with headroom. Override via <PROVIDER>_MAX_RPM.
PROVIDER_RPM: dict[str, int] = {
    "nvidia": CONFIG.nvidia_global_max_rpm,                 # ~40/min free tier
    "cloudflare": 200,                                      # 300 RPM ceiling
    "groq": 25,                                             # 30 RPM ceiling
    "cerebras": 4,                                          # 5 RPM ceiling (tight)
    "openrouter": 18,                                       # 20 RPM ceiling
}


def _rpm_for(provider: str) -> int:
    env = os.getenv(f"{provider.upper()}_MAX_RPM")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, PROVIDER_RPM.get(provider, 15))


def _base_url(provider: str) -> str | None:
    if provider == "nvidia":
        return CONFIG.nvidia_base_url
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    if provider == "cerebras":
        return "https://api.cerebras.ai/v1"
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "cloudflare":
        acct = CONFIG.cloudflare_account_id
        if not acct:
            return None
        return f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1"
    return None


def _api_key(provider: str) -> str:
    return {
        "nvidia": CONFIG.nvidia_api_key,
        "cloudflare": CONFIG.cloudflare_api_key,
        "groq": CONFIG.groq_api_key,
        "cerebras": CONFIG.cerebras_api_key,
        "openrouter": CONFIG.openrouter_api_key,
    }.get(provider, "")


# ── CANONICAL (NVIDIA) id → REAL id on each free provider that actually hosts it
# (LIVE-VERIFIED 2026-07). None = do NOT serve this model here (used for GLM on
# NVIDIA, which is dead). Providers not listed for a model get a capable
# SUBSTITUTE from PROVIDER_DEFAULT in pass 2 of resolve_chain — so a call always
# prefers the real model, then falls back to any capable free model for uptime.
# NOTE: OpenRouter gutted its free tier (2026) — nearly all ':free' variants are
# now paid; only gpt-oss-120b:free + llama-3.3-70b:free remain, so OpenRouter is
# a substitute-only last resort here.
MODEL_MAP: dict[str, dict[str, str | None]] = {
    # Kimi K2 — central analyst. Real Kimi only on NVIDIA + Cloudflare.
    "moonshotai/kimi-k2.6": {
        "nvidia": "moonshotai/kimi-k2.6",
        "cloudflare": "@cf/moonshotai/kimi-k2.6",
    },
    # Qwen — fast confirm. Real Qwen3 on NVIDIA, Cloudflare, Groq.
    "qwen/qwen3.5-122b-a10b": {
        "nvidia": "qwen/qwen3.5-122b-a10b",
        "cloudflare": "@cf/qwen/qwen3-30b-a3b-fp8",
        "groq": "qwen/qwen3-32b",
    },
    "qwen/qwen3.5-397b-a17b": {
        "nvidia": "qwen/qwen3.5-397b-a17b",
        "cloudflare": "@cf/qwen/qwen3-30b-a3b-fp8",
        "groq": "qwen/qwen3-32b",
    },
    # Nemotron — gate. Real Nemotron on NVIDIA (v1.5) + Cloudflare (Nemotron-3).
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": {
        "nvidia": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "cloudflare": "@cf/nvidia/nemotron-3-120b-a12b",
    },
    # GLM — DEAD on NVIDIA (410 EOL 2026-07-02). Real GLM now via Cerebras (zai-glm-4.7,
    # fast + reliable). Cloudflare's free @cf/zai-org/glm-5.2 edge tier was timing out 100%
    # (2026-07-09, ~10s wasted per AI decision on the hot path) — dropped; Cloudflare falls
    # back to its capable gpt-oss substitute instead of stalling every council/confirm cycle.
    "z-ai/glm-5.1": {
        "nvidia": None,                          # dead — skip NVIDIA for GLM
        "cerebras": "zai-glm-4.7",
    },
    # Safe generic model — real on NVIDIA, Cloudflare, Groq, OpenRouter.
    "meta/llama-3.3-70b-instruct": {
        "nvidia": "meta/llama-3.3-70b-instruct",
        "cloudflare": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "groq": "llama-3.3-70b-versatile",
        "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    },
    # Universal open model — free on every provider.
    "openai/gpt-oss-120b": {
        "nvidia": "openai/gpt-oss-120b",
        "cloudflare": "@cf/openai/gpt-oss-120b",
        "groq": "openai/gpt-oss-120b",
        "cerebras": "gpt-oss-120b",
        "openrouter": "openai/gpt-oss-120b:free",
    },
}

# Capable FREE substitute per provider when it doesn't host the requested model
# (pass 2 of resolve_chain). LIVE-VERIFIED to answer: Groq llama-3.3-70b returns
# clean text; Cerebras GLM-4.7 works; OpenRouter only gpt-oss-120b:free is free.
PROVIDER_DEFAULT: dict[str, str] = {
    "cloudflare": "@cf/openai/gpt-oss-120b",
    "groq": "llama-3.3-70b-versatile",
    "cerebras": "zai-glm-4.7",
    "openrouter": "openai/gpt-oss-120b:free",
}


def provider_order() -> list[str]:
    raw = getattr(CONFIG, "llm_provider_order", "") or "nvidia"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _usable(provider: str) -> tuple[str, str] | None:
    key = _api_key(provider)
    base = _base_url(provider)
    return (base, key) if (key and base) else None


def resolve_chain(canonical: str) -> list[Target]:
    """Ordered providers to TRY for `canonical`, STRICTLY in provider_order (speed
    order). Each provider uses the REAL model if it hosts it, else a capable free
    SUBSTITUTE (PROVIDER_DEFAULT). NVIDIA expands to ONE target PER key (rotation:
    key1 429 → key2 → … → next provider). Skips providers without a key. NVIDIA-only
    when no extra keys are set → unchanged behaviour.

    Single-pass (not real-first-then-substitute) so a FAST substitute (Groq) is
    preferred over a SLOW real model (Cloudflare's free edge tier) — reliability
    within the trade-decision timeout beats using the exact model too slowly. The
    exact models come fast from NVIDIA (now doubled via 2 keys)."""
    order = provider_order()
    row = MODEL_MAP.get(canonical, {})
    out: list[Target] = []
    for provider in order:
        if provider == "nvidia":
            # NVIDIA serves our canonical ids directly — unless explicitly disabled
            # for this model (GLM: dead on NVIDIA). Expand to one target per key.
            if row.get("nvidia", "keep") is None:
                continue
            mid = row.get("nvidia") or canonical
            base = CONFIG.nvidia_base_url
            keys = nvidia_keys()
            n = len(keys)
            if n:
                with _nv_rr_lock:
                    start = next(_nv_rr) % n
                for j in range(n):                       # rotate the TRY order; keep rl_key = physical index
                    i = (start + j) % n
                    out.append(Target("nvidia", base, keys[i], mid, f"nvidia_{i}"))
            continue
        u = _usable(provider)
        if not u:
            continue
        base, key = u
        mid = row.get(provider) if provider in row else None   # real model here?
        if not mid:
            mid = PROVIDER_DEFAULT.get(provider)               # else capable substitute
        if mid:
            out.append(Target(provider, base, key, mid, provider))
    return out
