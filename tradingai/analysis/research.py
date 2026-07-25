"""Deep web-research loop.

A tool-calling agent (NVIDIA model with reliable function calling) that runs:
plan → search → fetch → read → reflect → search again → synthesize, capped at
``max_steps`` iterations. Returns a cited briefing plus collected source URLs.
"""

from __future__ import annotations

import json
import logging

from config import CONFIG
from data import websearch

from . import llm

log = logging.getLogger(__name__)

_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for finance/markets/macro news. Returns title, url and a snippet for each result.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Focused search query."},
            "max_results": {"type": "integer", "description": "1-8 results.", "default": 5},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Fetch the full readable content of a result URL as markdown, to read details and verify claims.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
]

_SYSTEM = (
    "You are a meticulous financial research agent. Plan briefly, then loop: "
    "search → fetch the most relevant pages → read → identify gaps → search again. "
    "Cross-verify every quantitative claim (prices, dates, figures) across at least two "
    "independent sources. Prefer recent sources; markets move fast. When you have enough, "
    "stop calling tools and write a concise briefing (≤ 280 words) with: key drivers, "
    "notable news, sentiment, and the main risks. Cite source URLs inline like [1], [2]."
)


def _assistant_dict(m) -> dict:
    d = {"role": "assistant", "content": m.content or ""}
    if getattr(m, "tool_calls", None):
        d["tool_calls"] = [{
            "id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        } for tc in m.tool_calls]
    return d


def _run_tool(name: str, args: dict, sources: list[str]) -> dict | list:
    if name == "web_search":
        results = websearch.web_search(args.get("query", ""),
                                       max_results=int(args.get("max_results", 5) or 5))
        for r in results:
            if r.get("url"):
                sources.append(r["url"])
        return results
    if name == "fetch_url":
        url = args.get("url", "")
        if url:
            sources.append(url)
        return {"url": url, "content": websearch.fetch_url(url)}
    return {"error": f"unknown tool {name}"}


def deep_research(question: str, max_steps: int = 6, model: str | None = None) -> dict:
    """Returns {report, sources, note}."""
    if not websearch.search_available():
        return {"report": "", "sources": [], "note": "Deep research disabled (no TAVILY_API_KEY/EXA_API_KEY)."}
    if not llm.available():
        return {"report": "", "sources": [], "note": "Deep research disabled (no NVIDIA_API_KEY)."}

    model = model or CONFIG.model_research
    sources: list[str] = []
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": question},
    ]
    try:
        for _ in range(max_steps):
            resp = llm.chat(messages, model=model, tools=_TOOLS,
                            tool_choice="auto", temperature=0.4, max_tokens=1100)
            m = resp.choices[0].message
            messages.append(_assistant_dict(m))
            if not getattr(m, "tool_calls", None):
                return {"report": (m.content or "").strip(),
                        "sources": list(dict.fromkeys(sources)), "note": ""}
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:  # noqa: BLE001
                    args = {}
                out = _run_tool(tc.function.name, args, sources)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(out)[:9000]})
        # Out of steps → force a final synthesis without tools.
        messages.append({"role": "user", "content": "Stop researching now and write the final cited briefing."})
        resp = llm.chat(messages, model=model, temperature=0.4, max_tokens=1100)
        return {"report": (resp.choices[0].message.content or "").strip(),
                "sources": list(dict.fromkeys(sources)), "note": ""}
    except Exception as e:  # noqa: BLE001
        log.warning("deep_research failed: %s", e)
        return {"report": "", "sources": list(dict.fromkeys(sources)),
                "note": f"Research error: {e}"}
