"""Social-media sentiment (Reddit public JSON + optional X/Twitter API).

Reddit needs no key (public search JSON, just a User-Agent). X requires a
bearer token (X_BEARER_TOKEN); if absent, X is silently skipped. Items reuse
the NewsItem model and are scored locally with VADER.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from config import CONFIG
from core.models import NewsItem

from .news import _get_vader, label_for

log = logging.getLogger(__name__)

_UA = {"User-Agent": "ai-gold-trading-bot/1.0 (personal use)"}

# Map a base asset to a natural-language search query.
_QUERY = {
    "XAU": "gold price", "XAG": "silver price",
    "BTC": "bitcoin", "ETH": "ethereum", "WTI": "crude oil price",
}


def _query(symbol: str) -> str:
    base = symbol.partition("/")[0].upper()
    return _QUERY.get(base, base)


def _fmt_ts(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:  # noqa: BLE001
        return ""


def fetch_reddit(symbol: str, limit: int = 8) -> list[NewsItem]:
    try:
        r = requests.get("https://www.reddit.com/search.json",
                         params={"q": _query(symbol), "sort": "new", "limit": 25, "t": "week"},
                         headers=_UA, timeout=12)
        children = r.json().get("data", {}).get("children", [])
    except Exception as e:  # noqa: BLE001 (Reddit often blocks server IPs — harmless, news still works)
        log.debug("Reddit fetch failed: %s", e)
        return []
    vader = _get_vader()
    out = []
    for ch in children:
        d = ch.get("data", {})
        title = d.get("title", "")
        if not title:
            continue
        text = (d.get("selftext", "") or "")[:300]
        score = label = None
        if vader:
            score = vader.polarity_scores(f"{title}. {text}")["compound"]
            label = label_for(score)
        out.append(NewsItem(
            title=title, url="https://www.reddit.com" + d.get("permalink", ""),
            source=f"Reddit r/{d.get('subreddit', '')}", published=_fmt_ts(d.get("created_utc", 0)),
            summary=text, sentiment_score=score, sentiment_label=label or "",
        ))
    return out[:limit]


def fetch_x(symbol: str, limit: int = 8) -> list[NewsItem]:
    token = CONFIG.x_bearer_token
    if not token:
        return []
    try:
        r = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            params={"query": _query(symbol) + " -is:retweet lang:en",
                    "max_results": max(10, min(limit, 100)), "tweet.fields": "created_at"},
            headers={"Authorization": f"Bearer {token}"}, timeout=12)
        tweets = r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        log.warning("X fetch failed: %s", e)
        return []
    vader = _get_vader()
    out = []
    for t in tweets:
        txt = t.get("text", "")
        score = vader.polarity_scores(txt)["compound"] if vader else None
        out.append(NewsItem(
            title=txt[:140], url=f"https://twitter.com/i/web/status/{t.get('id')}",
            source="X", published=t.get("created_at", ""), summary=txt[:300],
            sentiment_score=score, sentiment_label=label_for(score) if score is not None else "",
        ))
    return out[:limit]


def fetch_social(symbol: str, limit: int = 8) -> list[NewsItem]:
    items = fetch_reddit(symbol, limit) + fetch_x(symbol, limit)
    seen, out = set(), []
    for it in items:
        key = it.title.strip().lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:limit]


def avg_sentiment(items: list[NewsItem]) -> tuple[float, str]:
    scores = [it.sentiment_score for it in items if it.sentiment_score is not None]
    if not scores:
        return 0.0, "n/a"
    avg = sum(scores) / len(scores)
    return avg, label_for(avg)
