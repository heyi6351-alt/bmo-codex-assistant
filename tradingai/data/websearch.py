"""Web search + page fetch — the tools that power "deep investigation".

Search:  Tavily (primary) → Exa (optional) → DuckDuckGo (no-key fallback).
Fetch:   Trafilatura (free, local) → Firecrawl (optional, JS/anti-bot) →
         Tavily Extract (fallback).

All functions are synchronous and defensive: a missing key or library simply
disables that provider rather than crashing the bot.
"""

from __future__ import annotations

import logging
from importlib.util import find_spec as iu_find

from config import CONFIG

log = logging.getLogger(__name__)

_tavily = None
_exa = None


def _tavily_client():
    global _tavily
    if _tavily is None and CONFIG.tavily_api_key:
        try:
            from tavily import TavilyClient
            _tavily = TavilyClient(api_key=CONFIG.tavily_api_key)
        except Exception as e:  # noqa: BLE001
            log.warning("Tavily unavailable: %s", e)
            _tavily = False
    return _tavily or None


def _exa_client():
    global _exa
    if _exa is None and CONFIG.exa_api_key:
        try:
            from exa_py import Exa
            _exa = Exa(api_key=CONFIG.exa_api_key)
        except Exception as e:  # noqa: BLE001
            log.warning("Exa unavailable: %s", e)
            _exa = False
    return _exa or None


def _ddg_available() -> bool:
    return bool(iu_find("ddgs") or iu_find("duckduckgo_search"))


def search_available() -> bool:
    # Tavily/Exa need a key; DuckDuckGo works with no key if the lib is installed.
    return bool(CONFIG.tavily_api_key or CONFIG.exa_api_key) or _ddg_available()


def _ddg_search(query: str, max_results: int, topic: str) -> list[dict]:
    """No-key DuckDuckGo fallback (unofficial, rate-limited)."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        return []
    try:
        with DDGS() as d:
            raw = list(d.news(query, max_results=max_results) if topic == "news"
                       else d.text(query, max_results=max_results))
        out = []
        for r in raw:
            out.append({
                "title": r.get("title", ""),
                "url": r.get("url") or r.get("href", ""),
                "content": (r.get("body") or r.get("excerpt") or "")[:1500],
            })
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("DuckDuckGo search failed: %s", e)
        return []


def web_search(query: str, max_results: int = 5, topic: str = "news") -> list[dict]:
    """Return [{title, url, content}] for a query. Tavily → Exa → DuckDuckGo."""
    client = _tavily_client()
    if client:
        for t in (topic, "general"):
            try:
                r = client.search(query=query, max_results=max_results,
                                   topic=t, include_raw_content=False)
                res = [
                    {"title": x.get("title", ""), "url": x.get("url", ""),
                     "content": (x.get("content") or "")[:1500]}
                    for x in r.get("results", [])
                ]
                if res:
                    return res
            except Exception as e:  # noqa: BLE001
                log.warning("Tavily search (topic=%s) failed: %s", t, e)
    exa = _exa_client()
    if exa:
        try:
            res = exa.search_and_contents(query, num_results=max_results, text=True)
            out = [
                {"title": getattr(x, "title", "") or "", "url": getattr(x, "url", "") or "",
                 "content": (getattr(x, "text", "") or "")[:1500]}
                for x in res.results
            ]
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            log.warning("Exa search failed: %s", e)
    # No-key fallback.
    return _ddg_search(query, max_results, topic)


def fetch_url(url: str, max_chars: int = 8000) -> str:
    """Fetch readable page content as text/markdown."""
    # 1) Trafilatura — free, local.
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, output_format="markdown",
                                       include_comments=False) or ""
            if text.strip():
                return text[:max_chars]
    except Exception as e:  # noqa: BLE001
        log.debug("Trafilatura failed for %s: %s", url, e)

    # 2) Firecrawl — optional, handles JS/anti-bot.
    if CONFIG.firecrawl_api_key:
        try:
            from firecrawl import FirecrawlApp
            app = FirecrawlApp(api_key=CONFIG.firecrawl_api_key)
            res = app.scrape_url(url, params={"formats": ["markdown"]})
            md = (res.get("markdown") if isinstance(res, dict) else getattr(res, "markdown", "")) or ""
            if md.strip():
                return md[:max_chars]
        except Exception as e:  # noqa: BLE001
            log.debug("Firecrawl failed for %s: %s", url, e)

    # 3) Tavily Extract — stay single-vendor as last resort.
    client = _tavily_client()
    if client:
        try:
            res = client.extract(urls=[url])
            results = res.get("results", []) if isinstance(res, dict) else []
            if results:
                return (results[0].get("raw_content") or "")[:max_chars]
        except Exception as e:  # noqa: BLE001
            log.debug("Tavily extract failed for %s: %s", url, e)

    return ""
