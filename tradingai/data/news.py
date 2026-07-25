"""News + sentiment aggregation.

Primary: Alpha Vantage NEWS_SENTIMENT (native macro/markets sentiment).
Fallback: always-on RSS feeds (Investing.com, Kitco, FXStreet, ForexLive),
scored locally with VADER. Items are deduplicated by canonical URL + title.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

import requests

from config import CONFIG
from core.models import NewsItem

log = logging.getLogger(__name__)

AV_URL = "https://www.alphavantage.co/query"

RSS_FEEDS = [
    ("Investing-Commodities", "https://www.investing.com/rss/news_11.rss"),
    ("Investing-Forex", "https://www.investing.com/rss/news_1.rss"),
    ("Kitco", "https://www.kitco.com/news/category/mining/rss"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("ForexLive", "https://www.forexlive.com/feed/"),
]

_CRYPTOS = {"BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "BNB"}

# Lazily-initialised VADER analyzer.
_vader = None


def _get_vader():
    global _vader
    if _vader is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        except Exception as e:  # noqa: BLE001
            log.warning("VADER unavailable (%s); RSS items will be unscored.", e)
            _vader = False
    return _vader or None


def label_for(score: float) -> str:
    """Alpha Vantage sentiment buckets."""
    if score <= -0.35:
        return "Bearish"
    if score <= -0.15:
        return "Somewhat-Bearish"
    if score < 0.15:
        return "Neutral"
    if score < 0.35:
        return "Somewhat-Bullish"
    return "Bullish"


def _canonical_key(url: str, title: str) -> str:
    u = (url or "").split("?")[0].rstrip("/").lower()
    t = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return hashlib.sha1(f"{u}|{t}".encode()).hexdigest()


def _av_time(raw: str) -> str:
    """'20260625T133000' -> '2026-06-25 13:30 UTC'."""
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:  # noqa: BLE001
        return raw


def _sort_ts(item: NewsItem) -> float:
    """Best-effort epoch for sorting newest-first."""
    for fmt in ("%Y-%m-%d %H:%M UTC", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(item.published, fmt).replace(tzinfo=timezone.utc).timestamp()
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def _alpha_vantage_news(symbol: str, limit: int = 50) -> list[NewsItem]:
    if not CONFIG.alphavantage_key:
        return []
    base = symbol.partition("/")[0].upper()
    params = {
        "function": "NEWS_SENTIMENT",
        "topics": "economy_macro,economy_monetary,financial_markets",
        "sort": "LATEST",
        "limit": limit,
        "apikey": CONFIG.alphavantage_key,
    }
    if base in _CRYPTOS:
        params["tickers"] = f"CRYPTO:{base}"
    try:
        feed = requests.get(AV_URL, params=params, timeout=20).json().get("feed", [])
    except Exception as e:  # noqa: BLE001
        log.warning("Alpha Vantage news failed: %s", e)
        return []
    out = []
    for a in feed:
        try:
            score = float(a.get("overall_sentiment_score", 0.0))
            out.append(NewsItem(
                title=a.get("title", ""),
                url=a.get("url", ""),
                source=a.get("source", "AlphaVantage"),
                published=_av_time(a.get("time_published", "")),
                summary=(a.get("summary", "") or "")[:400],
                sentiment_score=score,
                sentiment_label=a.get("overall_sentiment_label") or label_for(score),
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _rss_news() -> list[NewsItem]:
    try:
        import feedparser
    except ImportError:
        log.error("feedparser not installed; RSS news unavailable.")
        return []
    vader = _get_vader()
    out = []
    for source, url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            log.warning("RSS parse failed for %s: %s", url, e)
            continue
        for e in parsed.entries[:15]:
            title = e.get("title", "")
            summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400]
            published = e.get("published", "")
            score = None
            label = ""
            if vader:
                score = vader.polarity_scores(f"{title}. {summary}")["compound"]
                label = label_for(score)
            out.append(NewsItem(
                title=title, url=e.get("link", ""), source=source,
                published=published, summary=summary,
                sentiment_score=score, sentiment_label=label,
            ))
    return out


def fetch_news(symbol: str = "XAU/USD", limit: int = 12, include_rss: bool = True) -> list[NewsItem]:
    """Aggregated, deduplicated, newest-first news for the symbol."""
    items = _alpha_vantage_news(symbol)
    if include_rss:
        items += _rss_news()

    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for it in items:
        if not it.title:
            continue
        key = _canonical_key(it.url, it.title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    deduped.sort(key=_sort_ts, reverse=True)
    return deduped[:limit]


def news_key(item: NewsItem) -> str:
    """Stable hash for persistent dedup (used by the monitoring job)."""
    return _canonical_key(item.url, item.title)
