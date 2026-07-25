"""Curated catalog of NVIDIA-hosted models for trading analysis.

All ids below were confirmed live on https://integrate.api.nvidia.com/v1
(``client.models.list()``). Each entry carries professional guidance on what
the model is good for. The catalog is editable — the full live list is always
available in-bot via ``/model all``.

Roles:
  • analysis  — the "brain" for the multi-agent read (deep reasoning)
  • research  — drives the web-research tool loop (needs reliable tool calls)
  • fast      — low-latency summaries / quick tasks
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    slug: str          # short key used in callback_data (≤ ~20 chars)
    id: str            # exact NVIDIA model id
    name: str          # display name
    blurb: str         # what it's good for (shown to the user)
    tools: bool        # reliable OpenAI tool/function calling
    reasoning: bool    # strong multi-step reasoning
    tier: str          # "⭐ top" | "strong" | "fast"


# Ordered best-first for a trading/analysis bot.
CATALOG: list[ModelInfo] = [
    ModelInfo("kimi-k2", "moonshotai/kimi-k2.6", "Kimi K2.6",
              "DEFAULT. Agentic 1T-param MoE — live-tested fast (~2s) with elite tool use + strong reasoning. Best all-round analyst.",
              tools=True, reasoning=True, tier="⭐ top"),
    ModelInfo("qwen35", "qwen/qwen3.5-122b-a10b", "Qwen 3.5 (122B)",
              "Fast and the MOST CONSISTENT function-calling — the default web-research engine. Great analyst too.",
              tools=True, reasoning=True, tier="⭐ fast"),
    ModelInfo("glm51", "z-ai/glm-5.1", "GLM-5.1",
              "Strong bilingual reasoning + tool use. Reliable, balanced generalist analyst.",
              tools=True, reasoning=True, tier="strong"),
    ModelInfo("nemotron-super", "nvidia/llama-3.3-nemotron-super-49b-v1.5", "Nemotron Super 49B",
              "NVIDIA-tuned, efficient and reliable reasoning (~1s). Solid, snappy alternative.",
              tools=True, reasoning=True, tier="strong"),
    ModelInfo("llama33", "meta/llama-3.3-70b-instruct", "Llama 3.3 70B",
              "Fastest baseline (~0.5s). Dependable tool-calling, lighter reasoning. Used as the safe fallback.",
              tools=True, reasoning=False, tier="⚡ fast"),
    ModelInfo("qwen35-big", "qwen/qwen3.5-397b-a17b", "Qwen 3.5 (397B)",
              "Bigger Qwen — stronger reasoning, higher latency.",
              tools=True, reasoning=True, tier="strong"),
    ModelInfo("gpt-oss-120b", "openai/gpt-oss-120b", "GPT-OSS 120B",
              "Open GPT-class generalist. Balanced speed and quality.",
              tools=True, reasoning=True, tier="strong"),
    ModelInfo("deepseek-v4", "deepseek-ai/deepseek-v4-pro", "DeepSeek V4 Pro",
              "Ranks #1 open on real-world benchmarks; deepest reasoning. ⚠️ SLOW in testing (>30s) — best for /analyze, not snappy /signal.",
              tools=True, reasoning=True, tier="🐢 heavy"),
    ModelInfo("minimax-m3", "minimaxai/minimax-m3", "MiniMax M3",
              "Huge-context multimodal reasoning. ⚠️ SLOW in testing (>30s) and tool reliability behind Kimi/Qwen. Deep research only.",
              tools=True, reasoning=True, tier="🐢 heavy"),
    ModelInfo("nemotron-ultra", "nvidia/nemotron-3-ultra-550b-a55b", "Nemotron-3 Ultra",
              "NVIDIA's 550B flagship reasoning model. Top quality but heavy latency.",
              tools=True, reasoning=True, tier="🐢 heavy"),
]

_BY_SLUG = {m.slug: m for m in CATALOG}
_BY_ID = {m.id: m for m in CATALOG}


def by_slug(slug: str) -> ModelInfo | None:
    return _BY_SLUG.get(slug)


def by_id(model_id: str) -> ModelInfo | None:
    return _BY_ID.get(model_id)


def is_tool_capable(model_id: str) -> bool:
    """True if we know this model does reliable tool calls (default True if unknown)."""
    info = _BY_ID.get(model_id)
    return info.tools if info else True
