"""Retail crowd positioning for gold (XAU/USD) — 'what are other traders doing'.

Research (2026-07-03, cited in memory): retail positioning is a CONTRARIAN regime
signal — the crowd is usually wrong at extremes (academic: retail FX order-flow
fade strategies profitable; IG built its Client Sentiment tool explicitly as a
contrarian input). So the bot FADES extremes as a small nudge — it NEVER copies
the crowd and NEVER originates/blocks a trade on sentiment alone.

Sources (both optional, both free):
  • Myfxbook Community Outlook API — long/short % + lots for XAUUSD.
    Needs MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD in .env. Free tier ≈100 req/day →
    we poll at most every CROWD_POLL_MIN minutes and disk-cache the session.
  • (Cross-check, future) IG client sentiment — needs an IG demo API key.

Fail-open everywhere: any error → None → the bot trades exactly as before.
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests

from config import CONFIG, STORAGE_DIR

log = logging.getLogger(__name__)

CROWD_POLL_MIN = float(os.getenv("CROWD_POLL_MIN", "15"))
CROWD_EXTREME_PCT = float(os.getenv("CROWD_EXTREME_PCT", "75"))

_SESSION_FILE = STORAGE_DIR / "myfxbook_session.json"
_cache: dict = {"t": 0.0, "data": None}


def _myfxbook_session() -> str | None:
    """Login session token, disk-cached (Myfxbook sessions are IP-bound, ~1 month)."""
    email = os.getenv("MYFXBOOK_EMAIL", "")
    pw = os.getenv("MYFXBOOK_PASSWORD", "")
    if not email or not pw:
        return None
    try:
        if _SESSION_FILE.exists():
            d = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
            if time.time() - d.get("t", 0) < 20 * 86400 and d.get("session"):
                return d["session"]
    except Exception:  # noqa: BLE001
        pass
    try:
        r = requests.get("https://www.myfxbook.com/api/login.json",
                         params={"email": email, "password": pw}, timeout=15).json()
        if not r.get("error") and r.get("session"):
            _SESSION_FILE.write_text(json.dumps({"session": r["session"], "t": time.time()}),
                                     encoding="utf-8")
            return r["session"]
        log.warning("Myfxbook login failed: %s", r.get("message"))
    except Exception as e:  # noqa: BLE001
        log.debug("Myfxbook login error: %s", e)
    return None


def fetch_crowd(symbol_name: str = "Gold") -> dict | None:
    """Latest crowd positioning: {"long_pct","short_pct","source","age_min","extreme",
    "crowd_dir"} or None (fail-open). Cached CROWD_POLL_MIN minutes (free-tier budget)."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["t"] < CROWD_POLL_MIN * 60:
        d = dict(_cache["data"])
        d["age_min"] = (now - _cache["t"]) / 60.0
        return d
    sess = _myfxbook_session()
    if not sess:
        return _cache["data"]          # no creds / login down → stale-or-none, fail-open
    try:
        r = requests.get("https://www.myfxbook.com/api/get-community-outlook.json",
                         params={"session": sess}, timeout=15).json()
        if r.get("error"):
            log.debug("Myfxbook outlook error: %s", r.get("message"))
            return _cache["data"]
        for sym in r.get("symbols", []):
            if str(sym.get("name", "")).lower() in (symbol_name.lower(), "xauusd", "gold"):
                long_pct = float(sym.get("longPercentage", 0) or 0)
                short_pct = float(sym.get("shortPercentage", 0) or 0)
                extreme = max(long_pct, short_pct) >= CROWD_EXTREME_PCT
                data = {"long_pct": long_pct, "short_pct": short_pct,
                        "source": "Myfxbook", "age_min": 0.0, "extreme": extreme,
                        "crowd_dir": "long" if long_pct >= short_pct else "short"}
                _cache.update(t=now, data=data)
                return data
    except Exception as e:  # noqa: BLE001
        log.debug("Myfxbook outlook fetch failed: %s", e)
    return _cache["data"]


def crowd_line(symbol_name: str = "Gold") -> str:
    """One snapshot line for the AI prompts, or '' when unavailable (fail-open)."""
    d = fetch_crowd(symbol_name)
    if not d:
        return ""
    warn = " — crowds at extremes are usually WRONG (contrarian)" if d["extreme"] else ""
    return (f"CROWD: retail {d['long_pct']:.0f}% long / {d['short_pct']:.0f}% short "
            f"({d['source']}, {d['age_min']:.0f}m old){warn}")
