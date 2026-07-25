"""Scheduled background jobs (run by python-telegram-bot's JobQueue).

- ``digest_job``   — periodic full digest (signal + news) to subscribed chats.
- ``news_job``     — frequent BREAKING-news push: only genuinely new headlines.
- ``monitor_job``  — the open-trade guardian: watches price vs stop/TP and scans
                     news for threats to each tracked position, alerting urgently.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from analysis import ticket as ticket_mod
from analysis.agents import assess_trade_threat, run_analysis
from config import CONFIG
from core import db
from core.models import UserSettings, utcnow_iso
from data import news as news_mod
from data.market import MARKET
from risk.profiles import get_profile

from . import formatting as F

log = logging.getLogger(__name__)
HTML = ParseMode.HTML
ALERT_THROTTLE_MIN = 30  # don't re-alert the same trade more often than this


# ── scheduled digest ──────────────────────────────────────────────────────────
def _digest_source() -> UserSettings:
    """One canonical config for the SHARED digest (owner's, else defaults)."""
    if CONFIG.owner_id:
        return db.get_settings(CONFIG.owner_id)
    return UserSettings(chat_id=0, base=CONFIG.default_base,
                        quote=CONFIG.default_quote, risk=CONFIG.default_risk)


def _digest_signature(sig, ticket) -> str:
    """Coarse fingerprint so small price drift doesn't count as a 'new' signal."""
    bucket = round(sig.entry / max(0.5 * sig.atr, 1.0)) if sig.atr else round(sig.entry)
    tp_n = len(ticket.take_profits) if (ticket and ticket.valid) else 0
    return f"{sig.direction}|{bucket}|{tp_n}"


def _mins_since(iso: str) -> float:
    if not iso:
        return 1e9
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return 1e9


HEARTBEAT_MIN = 60  # re-broadcast an UNCHANGED signal at most this often


async def digest_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every few minutes: ONE shared analysis, broadcast to ALL subscribers
    + channel, but only when the signal CHANGED (or once per heartbeat) — so we
    never spam the same call every tick."""
    chat_ids = await asyncio.to_thread(db.chats_with_digests)
    if not chat_ids:
        return
    src = _digest_source()
    profile = get_profile(src.risk)
    result = await asyncio.to_thread(run_analysis, src.symbol, profile, False, src.model or None)
    sig = result["signal"]
    if sig is None:
        log.info("Digest tick: no signal (no market data).")
        return

    ok, reason = ticket_mod.should_emit_ticket(result, profile)
    ticket = await asyncio.to_thread(ticket_mod.build_ticket, result, profile, False) if ok else None

    signature = _digest_signature(sig, ticket)
    last_sig = db.get_config("last_digest_sig", "") or ""
    changed = signature != last_sig
    heartbeat = _mins_since(db.get_config("last_digest_at", "") or "") >= HEARTBEAT_MIN
    if not changed and not heartbeat:
        log.info("Digest tick: unchanged (%s) — not re-sending.", signature)
        return

    header = f"⏰ <b>Signal — {F.esc(src.symbol)}</b>\n"
    sig_text = header + F.fmt_signal(sig)
    ticket_text = F.fmt_ticket(ticket) if (ticket and ticket.valid) else ""

    db.set_config("last_digest_sig", signature)
    db.set_config("last_digest_at", utcnow_iso())

    sent = 0
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id, sig_text, parse_mode=HTML, disable_web_page_preview=True)
            if ticket_text:
                await context.bot.send_message(chat_id, ticket_text, parse_mode=HTML, disable_web_page_preview=True)
            sent += 1
        except Exception as e:  # noqa: BLE001
            log.warning("Digest send to %s failed: %s", chat_id, e)
    log.info("Digest broadcast %s (%s%s) → %d/%d chats",
             signature, sig.direction, " +ticket" if ticket_text else f" no-ticket:{reason}",
             sent, len(chat_ids))


# ── breaking-news push ────────────────────────────────────────────────────────
def _news_due(s, now: datetime) -> bool:
    """Is this chat due for a breaking-news push, per ITS own interval?"""
    if not s.last_news_at:
        return True
    try:
        last = datetime.fromisoformat(s.last_news_at)
    except Exception:  # noqa: BLE001
        return True
    return (now - last).total_seconds() >= max(1, s.news_interval_min) * 60


async def news_job(context: ContextTypes.DEFAULT_TYPE):
    """1-minute base tick. Pushes only NEW headlines to each chat on its own
    cadence, deduplicated per-chat so users on different intervals don't starve
    each other."""
    chat_ids = await asyncio.to_thread(db.chats_with_digests)
    if not chat_ids:
        return
    now = datetime.now(timezone.utc)

    # Only chats whose personal interval has elapsed; group by symbol.
    by_symbol: dict[str, list] = {}
    for cid in chat_ids:
        s = db.get_settings(cid)
        if _news_due(s, now):
            by_symbol.setdefault(s.symbol, []).append(s)
    if not by_symbol:
        return

    for symbol, subs in by_symbol.items():
        try:
            items = await asyncio.to_thread(news_mod.fetch_news, symbol, 12)
        except Exception as e:  # noqa: BLE001
            log.warning("news_job fetch failed for %s: %s", symbol, e)
            continue
        keyed = [(news_mod.news_key(it), it) for it in items]

        for s in subs:
            fresh = [(h, it) for h, it in keyed
                     if not await asyncio.to_thread(db.chat_news_seen, s.chat_id, h)]
            await asyncio.to_thread(db.set_last_news, s.chat_id)  # reset cadence even if nothing new
            if not fresh:
                log.info("news → chat %s (%s): nothing new", s.chat_id, symbol)
                continue
            for h, _ in fresh:
                await asyncio.to_thread(db.mark_chat_news_seen, s.chat_id, h)
            text = F.fmt_news([it for _, it in fresh][:5], symbol, header="🔔 Breaking news")
            try:
                await context.bot.send_message(s.chat_id, text, parse_mode=HTML,
                                               disable_web_page_preview=True)
                log.info("news → chat %s (%s): sent %d fresh", s.chat_id, symbol, len(fresh))
            except Exception as e:  # noqa: BLE001
                log.warning("news_job send to %s failed: %s", s.chat_id, e)


# ── open-trade guardian ─────────────────────────────────────────────────────────
def _recently_alerted(trade, minutes: int) -> bool:
    if not trade.last_alert_at:
        return False
    try:
        last = datetime.fromisoformat(trade.last_alert_at)
        return (datetime.now(timezone.utc) - last).total_seconds() < minutes * 60
    except Exception:  # noqa: BLE001
        return False


async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    trades = await asyncio.to_thread(db.all_open_trades)
    if not trades:
        return

    # Fetch news once per distinct symbol; collect unseen items.
    symbols = {t.symbol for t in trades}
    unseen_by_symbol: dict[str, list] = {}
    for sym in symbols:
        try:
            items = await asyncio.to_thread(news_mod.fetch_news, sym, 12)
        except Exception as e:  # noqa: BLE001
            log.warning("Monitor news fetch failed for %s: %s", sym, e)
            items = []
        unseen = [(news_mod.news_key(it), it) for it in items
                  if not await asyncio.to_thread(db.news_seen, news_mod.news_key(it))]
        unseen_by_symbol[sym] = unseen

    for trade in trades:
        # 1) Price vs stop / take-profit.
        try:
            price = await asyncio.to_thread(MARKET.get_price, trade.symbol)
        except Exception:  # noqa: BLE001
            price = None

        if price is not None and not _recently_alerted(trade, ALERT_THROTTLE_MIN):
            hit = None
            if trade.stop and (
                (trade.direction == "long" and price <= trade.stop)
                or (trade.direction == "short" and price >= trade.stop)
            ):
                hit = ("STOP reached", f"Price <code>{price}</code> hit your stop <code>{trade.stop}</code>.", "high")
            elif trade.take_profit and (
                (trade.direction == "long" and price >= trade.take_profit)
                or (trade.direction == "short" and price <= trade.take_profit)
            ):
                hit = ("TAKE-PROFIT reached", f"Price <code>{price}</code> hit your target <code>{trade.take_profit}</code>.", "low")
            if hit:
                kind, detail, sev = hit
                await context.bot.send_message(
                    trade.chat_id, F.fmt_trade_alert(trade, kind, detail, sev), parse_mode=HTML)
                await asyncio.to_thread(db.touch_trade_alert, trade.id)
                continue  # price alert already sent this cycle

        # 2) Adverse news threat (only unseen items).
        unseen = unseen_by_symbol.get(trade.symbol, [])
        if unseen and not _recently_alerted(trade, ALERT_THROTTLE_MIN):
            assessment = await asyncio.to_thread(
                assess_trade_threat, trade, [it for _, it in unseen])
            if assessment.get("threat"):
                detail = (f"{assessment.get('reason', '')}\n\n"
                          f"This may threaten your {trade.direction} position — review your stop/exit.")
                await context.bot.send_message(
                    trade.chat_id,
                    F.fmt_trade_alert(trade, "adverse news", detail, assessment.get("severity", "high")),
                    parse_mode=HTML, disable_web_page_preview=True)
                await asyncio.to_thread(db.touch_trade_alert, trade.id)

    # Mark all fetched unseen items as seen so we don't re-alert on them.
    for unseen in unseen_by_symbol.values():
        for h, _ in unseen:
            await asyncio.to_thread(db.mark_news_seen, h)
