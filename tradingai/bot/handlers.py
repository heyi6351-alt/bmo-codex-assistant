"""Telegram command + callback handlers."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from analysis import llm
from analysis import model_catalog as mc
from analysis import research
from analysis.agents import run_analysis
from analysis import ticket as ticket_mod
from analysis.backtest import LABEL_TO_INTERVAL, backtest
from analysis.optimizer import optimize
from analysis.tournament import run_tournament
from config import CONFIG
from core import db
from core.models import Trade
from data import social as social_mod
from data.market import MARKET
from risk.profiles import VALID_PROFILES, get_profile

from . import formatting as F
from . import keyboards as K

log = logging.getLogger(__name__)
HTML = ParseMode.HTML
MAX_LEN = 4096

def _build_help(update: Update) -> str:
    """Professional, sectioned help. Owner-only commands are shown to the owner."""
    L = [
        "🪙 <b>AI Gold Trading Bot</b>",
        "<i>Live gold/FX prices, news &amp; social sentiment, deep AI research, "
        "and risk-managed trade signals.</i>",
        "",
        "<b>📊  Market &amp; Analysis</b>",
        "• /price — live price of your asset",
        "• /news — latest headlines + sentiment",
        "• /social — Reddit/X chatter + sentiment",
        "• /signal — full multi-timeframe trade signal",
        "• /ticket — broker-style call: BUY/SELL + SL + TP ladder",
        "• /analyze <code>[question]</code> — deep web research + AI read",
        "• /backtest <code>[M15·H1·H4·D1]</code> — test the strategy on history",
        "• /optimize <code>[tf]</code> — auto-tune params (out-of-sample tested)",
        "• /tournament <code>[all]</code> — AI models vote (add 'all' for every model)",
        "",
        "<b>📈  Trade Monitoring</b>",
        "• /track <code>[SYM] long|short ENTRY [SL] [TP]</code> — watch a trade",
        "• /trades — list monitored trades",
        "• /untrack <code>&lt;id&gt;</code> — stop monitoring a trade",
        "",
        "<b>⚙️  Configuration</b>",
        "• /asset — switch tracked asset",
        "• /currency — switch quote currency",
        "• /model — switch the AI model",
        "• /risk <code>[conservative·moderate·aggressive]</code> — risk profile",
        "• /digest <code>on|off</code> — auto digests + breaking-news pushes",
        "• /newsfreq <code>[15m·1h·2h]</code> — how often I push breaking news",
        "• /settings — show your configuration",
        "• /id — show your Telegram ID &amp; role",
        "",
        "<b>📣  Channel</b>",
        "• /post <code>[text]</code> — publish a message to the channel",
        "• /post signal — publish a fresh trade signal to the channel",
    ]
    if _is_owner(update):
        L += [
            "",
            "<b>👑  Owner — admin management</b>",
            "• /admins — list authorised admins",
            "• /addadmin <code>&lt;user_id&gt; [label]</code> — grant a user access",
            "• /removeadmin <code>&lt;user_id&gt;</code> — revoke a user's access",
            "• /setchannel <code>&lt;@name | -100id | off&gt;</code> — link the channel",
        ]
    L += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "<i>⚠️ Not financial advice. Trading gold/FX is high-risk.</i>",
    ]
    return "\n".join(L)


# ── helpers ────────────────────────────────────────────────────────────────────
def _is_owner(update: Update) -> bool:
    user = update.effective_user
    return bool(CONFIG.owner_id and user and user.id == CONFIG.owner_id)


def _authorized(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    uid = user.id
    if CONFIG.owner_id and uid == CONFIG.owner_id:
        return True
    if uid in CONFIG.allowed_user_ids:
        return True
    if db.is_admin(uid):
        return True
    # First-run convenience: if NOTHING is configured, stay open (README warns).
    if not CONFIG.owner_id and not CONFIG.allowed_user_ids and not db.list_admins():
        return True
    return False


def _resolve_channel():
    """Effective broadcast target: runtime override (DB) or env, as int or @name."""
    value = db.get_config("channel_id") or CONFIG.channel_id
    if not value:
        return None
    return int(value) if value.lstrip("-").isdigit() else value


async def _reply(update: Update, text: str, **kw):
    kw.setdefault("parse_mode", HTML)
    kw.setdefault("disable_web_page_preview", True)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 20] + "\n…(truncated)"
    return await update.effective_message.reply_text(text, **kw)


async def _maybe_send_ticket(update, context, result, profile):
    """Send the broker-style trade ticket as a 2nd message when actionable."""
    ok, reason = ticket_mod.should_emit_ticket(result, profile)
    if not ok:
        log.info("ticket gated (%s)", reason)
        return
    # Auto-tickets use the LLM-free smart plan (no extra API calls → 429-safe).
    t = await asyncio.to_thread(ticket_mod.build_ticket, result, profile, False)
    if t and t.valid:
        await _reply(update, F.fmt_ticket(t))


async def _edit(msg, text: str):
    """Edit a message safely: truncate over-long text, swallow Telegram quirks."""
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 20] + "\n…(truncated)"
    try:
        await msg.edit_text(text, parse_mode=HTML, disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001  (e.g. "message is not modified", network)
        log.warning("edit_text failed: %s", e)


def _settings(update: Update):
    return db.get_settings(update.effective_chat.id)


# ── basic commands ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        uid = update.effective_user.id if update.effective_user else "?"
        return await _reply(update, (
            "⛔ You are not authorised to use this bot.\n"
            f"Your Telegram ID is <code>{uid}</code> — send it to the owner to request access."))
    _settings(update)  # creates the default row → registers chat for digests
    await _reply(update, _build_help(update))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await _reply(update, _build_help(update))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open to everyone: show the caller their Telegram id, chat id and role —
    so a prospective admin can send their id to the owner for /addadmin."""
    user = update.effective_user
    chat = update.effective_chat
    if not user:
        return
    if _is_owner(update):
        role = "👑 Owner"
    elif user.id in CONFIG.allowed_user_ids or db.is_admin(user.id):
        role = "🛡️ Admin"
    else:
        role = "Guest (no access)"
    L = ["🪪 <b>Your Telegram identity</b>", "", f"• Name: {F.esc(user.full_name)}"]
    if user.username:
        L.append(f"• Username: @{F.esc(user.username)}")
    L += [
        f"• User ID: <code>{user.id}</code>",
        f"• Chat ID: <code>{chat.id if chat else user.id}</code>",
        f"• Role: <b>{role}</b>",
    ]
    if role.startswith("Guest"):
        L += ["", "<i>Send your User ID to the owner to request access.</i>"]
    await _reply(update, "\n".join(L))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    await _reply(update, F.fmt_settings(s, get_profile(s.risk)))


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    try:
        price = await asyncio.to_thread(MARKET.get_price, s.symbol)
        await _reply(update, F.fmt_price(s.symbol, price))
    except Exception as e:  # noqa: BLE001
        await _reply(update, f"⚠️ Couldn't fetch price for {F.esc(s.symbol)}: {F.esc(e)}")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    from data import news as news_mod
    items = await asyncio.to_thread(news_mod.fetch_news, s.symbol, 8)
    await _reply(update, F.fmt_news(items, s.symbol))


# ── analysis commands ──────────────────────────────────────────────────────────
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    profile = get_profile(s.risk)
    msg = await _reply(update, f"🔍 Building a {F.esc(profile.name)} signal for <b>{F.esc(s.symbol)}</b>…")
    try:
        result = await asyncio.to_thread(run_analysis, s.symbol, profile, False, s.model or None)
        sig = result["signal"]
        if sig is not None:
            db.save_signal(update.effective_chat.id, sig.to_dict())
        await _edit(msg, F.fmt_signal(sig))
        await _maybe_send_ticket(update, context, result, profile)
    except Exception as e:  # noqa: BLE001
        log.exception("signal failed")
        await _edit(msg, f"⚠️ Analysis failed: {F.esc(e)}")


async def cmd_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force a trade ticket now (runs the analysis + risk-plan tournament)."""
    if not _authorized(update):
        return
    s = _settings(update)
    profile = get_profile(s.risk)
    msg = await _reply(update, f"🎫 Building a trade ticket for <b>{F.esc(s.symbol)}</b>…")
    try:
        result = await asyncio.to_thread(run_analysis, s.symbol, profile, False, s.model or None)
        sig = result["signal"]
        await _edit(msg, F.fmt_signal(sig))
        ok, reason = ticket_mod.should_emit_ticket(result, profile)
        if not ok:
            return await _reply(update, f"No actionable ticket right now ({F.esc(reason.replace('_', ' '))}).")
        # /ticket is on-demand → run the full risk-plan tournament for best levels.
        t = await asyncio.to_thread(ticket_mod.build_ticket, result, profile, True)
        if t and t.valid:
            await _reply(update, F.fmt_ticket(t))
    except Exception as e:  # noqa: BLE001
        log.exception("ticket failed")
        await _edit(msg, f"⚠️ Ticket failed: {F.esc(e)}")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    question = " ".join(context.args).strip() if context.args else ""

    # /analyze <question> → pure deep-research answer.
    if question:
        msg = await _reply(update, f"🔬 Researching: <i>{F.esc(question)}</i>…")
        out = await asyncio.to_thread(research.deep_research, question)
        text = F.fmt_research(out) or "No research result (check TAVILY_API_KEY / NVIDIA_API_KEY)."
        return await _edit(msg, text)

    # /analyze → full pipeline with deep research.
    profile = get_profile(s.risk)
    msg = await _reply(update, f"🔬 Deep analysis of <b>{F.esc(s.symbol)}</b> (research + multi-agent)… this can take ~1 min.")
    try:
        result = await asyncio.to_thread(run_analysis, s.symbol, profile, True, s.model or None)
        sig = result["signal"]
        if sig is not None:
            db.save_signal(update.effective_chat.id, sig.to_dict())
        await _edit(msg, F.fmt_signal_full(sig))
        research_text = F.fmt_research(result.get("research", {}))
        if research_text:
            await _reply(update, research_text)
        await _maybe_send_ticket(update, context, result, profile)
    except Exception as e:  # noqa: BLE001
        log.exception("analyze failed")
        await _edit(msg, f"⚠️ Analysis failed: {F.esc(e)}")


# ── switching commands ──────────────────────────────────────────────────────────
async def cmd_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    # /asset XAU/USD → set directly; otherwise show menu.
    if context.args:
        sym = context.args[0].upper()
        base, _, quote = sym.partition("/")
        s = _settings(update)
        s.base = base or s.base
        s.quote = quote or s.quote
        db.save_settings(s)
        return await _reply(update, f"✅ Asset set to <b>{F.esc(s.symbol)}</b>.")
    await _reply(update, "Pick an asset to track:", reply_markup=K.asset_keyboard())


async def cmd_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if context.args:
        s = _settings(update)
        s.quote = context.args[0].upper()
        db.save_settings(s)
        return await _reply(update, f"✅ Quote currency set to <b>{F.esc(s.quote)}</b> → <b>{F.esc(s.symbol)}</b>.")
    await _reply(update, "Pick a quote currency:", reply_markup=K.currency_keyboard())


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    profile = get_profile(s.risk)
    # Optional timeframe arg, e.g. /backtest H4 (default H1).
    label = (context.args[0].upper() if context.args else "H1")
    interval = LABEL_TO_INTERVAL.get(label)
    if interval is None:
        return await _reply(update, "Usage: <code>/backtest [M15|H1|H4|D1]</code>")
    msg = await _reply(update, f"📈 Backtesting <b>{F.esc(s.symbol)} {F.esc(label)}</b> "
                               f"on the {F.esc(profile.name)} profile… (may take a moment)")
    try:
        stats = await asyncio.to_thread(backtest, s.symbol, profile, interval)
        await _edit(msg, F.fmt_backtest(stats))
    except Exception as e:  # noqa: BLE001
        log.exception("backtest failed")
        await _edit(msg, f"⚠️ Backtest failed: {F.esc(e)}")


async def cmd_optimize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    profile = get_profile(s.risk)
    label = (context.args[0].upper() if context.args else "H1")
    interval = LABEL_TO_INTERVAL.get(label)
    if interval is None:
        return await _reply(update, "Usage: <code>/optimize [M15|H1|H4|D1]</code>")
    msg = await _reply(update, f"🧬 Optimising parameters for <b>{F.esc(s.symbol)} {F.esc(label)}</b>… "
                               f"this can take 1–2 min (no AI calls).")
    try:
        result = await asyncio.to_thread(optimize, s.symbol, profile, interval)
        await _edit(msg, F.fmt_optimize(result))
    except Exception as e:  # noqa: BLE001
        log.exception("optimize failed")
        await _edit(msg, f"⚠️ Optimize failed: {F.esc(e)}")


async def cmd_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    mode = "all" if (context.args and context.args[0].lower() in ("all", "full")) else "fast"
    note = "ALL models (slower, ~1–2 min)" if mode == "all" else "fast models (~30s)"
    msg = await _reply(update, f"🏆 Model tournament on <b>{F.esc(s.symbol)}</b> — {note}…")
    try:
        result = await asyncio.to_thread(run_tournament, s.symbol, get_profile(s.risk), mode)
        await _edit(msg, F.fmt_tournament(result))
    except Exception as e:  # noqa: BLE001
        log.exception("tournament failed")
        await _edit(msg, f"⚠️ Tournament failed: {F.esc(e)}")


async def _send_long(update: Update, header: str, lines: list[str]):
    buf = header + "\n"
    for ln in lines:
        if len(buf) + len(ln) + 1 > 3800:
            await update.effective_message.reply_text(buf, parse_mode=HTML, disable_web_page_preview=True)
            buf = ""
        buf += ln + "\n"
    if buf.strip():
        await update.effective_message.reply_text(buf, parse_mode=HTML, disable_web_page_preview=True)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)

    if context.args:
        arg = " ".join(context.args).strip()
        # /model all → live list of every free model on the endpoint.
        if arg.lower() == "all":
            ids = await asyncio.to_thread(llm.list_model_ids)
            if not ids:
                return await _reply(update, "⚠️ Couldn't fetch the live model list (check NVIDIA_API_KEY).")
            lines = [f"<code>{F.esc(i)}</code>" for i in ids]
            return await _send_long(update, f"🗂️ <b>{len(ids)} free NVIDIA models</b> — set one with <code>/model &lt;id&gt;</code>:", lines)
        # /model <slug|id> → set directly.
        info = mc.by_slug(arg)
        model_id = info.id if info else arg
        s.model = model_id
        db.save_settings(s)
        nice = info.name if info else model_id
        return await _reply(update, f"✅ AI model set to <b>{F.esc(nice)}</b>.\n<code>{F.esc(model_id)}</code>")

    active = s.model or CONFIG.model_deep
    await _reply(update, F.fmt_models(active), reply_markup=K.model_keyboard())


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if context.args:
        level = context.args[0].lower()
        if level not in VALID_PROFILES:
            return await _reply(update, f"Unknown risk level. Choose: {', '.join(VALID_PROFILES)}.")
        s = _settings(update)
        s.risk = level
        db.save_settings(s)
        return await _reply(update, "✅ Risk updated — applies to all new signals.\n\n" + get_profile(level).describe())
    s = _settings(update)
    await _reply(update, "Choose your risk profile (applies instantly):\n\n" + get_profile(s.risk).describe(),
                 reply_markup=K.risk_keyboard())


# ── trade tracking ───────────────────────────────────────────────────────────────
def _parse_track(args: list[str], default_symbol: str):
    symbol, direction, nums = default_symbol, None, []
    for tok in args:
        t = tok.strip()
        tl = t.lower()
        if "/" in t:
            symbol = t.upper()
        elif tl in ("long", "buy"):
            direction = "long"
        elif tl in ("short", "sell"):
            direction = "short"
        else:
            try:
                nums.append(float(t.replace(",", "")))
            except ValueError:
                pass
    if direction is None or not nums:
        return None
    return (symbol, direction, nums[0],
            nums[1] if len(nums) > 1 else None,
            nums[2] if len(nums) > 2 else None)


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    parsed = _parse_track(context.args or [], s.symbol)
    if not parsed:
        return await _reply(update, (
            "Usage: <code>/track [SYMBOL] long|short ENTRY [STOP] [TP]</code>\n"
            "Examples:\n"
            "<code>/track long 2350 2340 2390</code>  (uses your current asset)\n"
            "<code>/track EUR/USD short 1.0850 1.0890 1.0780</code>"))
    symbol, direction, entry, stop, tp = parsed
    trade = Trade(chat_id=update.effective_chat.id, symbol=symbol, direction=direction,
                  entry=entry, stop=stop, take_profit=tp)
    trade_id = await asyncio.to_thread(db.add_trade, trade)
    await _reply(update, (
        f"✅ Tracking trade #{trade_id}: {'🟢' if direction == 'long' else '🔴'} "
        f"<b>{F.esc(symbol)}</b> {F.esc(direction)} @ <code>{entry}</code>.\n"
        f"I'll watch price and scan news, and alert you 🚨 if it's threatened."))


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    trades = await asyncio.to_thread(db.list_trades, update.effective_chat.id, "open")
    await _reply(update, F.fmt_trades(trades))


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    if not context.args or not context.args[0].isdigit():
        return await _reply(update, "Usage: <code>/untrack &lt;id&gt;</code> (see /trades).")
    tid = int(context.args[0])
    trade = await asyncio.to_thread(db.get_trade, tid)
    if not trade or trade.chat_id != update.effective_chat.id:
        return await _reply(update, "No such trade.")
    await asyncio.to_thread(db.set_trade_status, tid, "closed")
    await _reply(update, f"✅ Stopped monitoring trade #{tid}.")


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    arg = (context.args[0].lower() if context.args else "")
    if arg not in ("on", "off"):
        return await _reply(update, "Usage: <code>/digest on</code> or <code>/digest off</code>.")
    s = _settings(update)
    s.digests_enabled = (arg == "on")
    db.save_settings(s)
    await _reply(update, f"✅ Scheduled digests are now <b>{arg.upper()}</b>.")


def _parse_minutes(text: str):
    t = text.strip().lower()
    try:
        if t.endswith("h"):
            return int(float(t[:-1]) * 60)
        if t.endswith("m"):
            return int(float(t[:-1]))
        return int(float(t))
    except ValueError:
        return None


async def cmd_newsfreq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    if not context.args:
        cur = (f"{s.news_interval_min} min" if s.news_interval_min < 60
               else f"{s.news_interval_min / 60:g} h")
        return await _reply(update, (
            f"📰 You get breaking news every <b>{cur}</b>.\n"
            "Change it: <code>/newsfreq 15</code> · <code>/newsfreq 30m</code> · <code>/newsfreq 2h</code>\n"
            "<i>(min 5 min, max 24 h; use /digest off to mute entirely.)</i>"))
    mins = _parse_minutes(context.args[0])
    if mins is None:
        return await _reply(update, "Usage: <code>/newsfreq 15</code> | <code>/newsfreq 30m</code> | <code>/newsfreq 2h</code>")
    mins = max(5, min(1440, mins))
    s.news_interval_min = mins
    db.save_settings(s)
    nice = f"{mins} min" if mins < 60 else f"{mins / 60:g} h"
    await _reply(update, f"✅ Breaking-news cadence set to <b>{nice}</b>.")


async def cmd_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    s = _settings(update)
    items = await asyncio.to_thread(social_mod.fetch_social, s.symbol, 8)
    await _reply(update, F.fmt_news(items, s.symbol, header="💬 Social chatter"))


# ── owner / admin / channel ───────────────────────────────────────────────────────
async def cmd_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    admins = await asyncio.to_thread(db.list_admins)
    lines = [f"👑 <b>Owner:</b> <code>{CONFIG.owner_id}</code>", "", "🛡️ <b>Admins</b>"]
    if admins:
        lines += [f"• <code>{uid}</code> {F.esc(label)}".rstrip() for uid, label in admins]
    else:
        lines.append("• none yet")
    lines += ["", "Add: <code>/addadmin &lt;user_id&gt; [label]</code> · Remove: <code>/removeadmin &lt;user_id&gt;</code>"]
    await _reply(update, "\n".join(lines))


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    if not context.args or not context.args[0].lstrip("-").isdigit():
        return await _reply(update, "Usage: <code>/addadmin &lt;user_id&gt; [label]</code>")
    uid = int(context.args[0])
    label = " ".join(context.args[1:]).strip()
    added = await asyncio.to_thread(db.add_admin, uid, CONFIG.owner_id, label)
    await _reply(update, f"✅ {'Added' if added else 'Updated'} admin <code>{uid}</code>.")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    if not context.args or not context.args[0].lstrip("-").isdigit():
        return await _reply(update, "Usage: <code>/removeadmin &lt;user_id&gt;</code>")
    uid = int(context.args[0])
    removed = await asyncio.to_thread(db.remove_admin, uid)
    await _reply(update, f"✅ Removed admin <code>{uid}</code>." if removed else "No such admin.")


async def cmd_setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    if not context.args:
        current = db.get_config("channel_id") or CONFIG.channel_id or "(none)"
        return await _reply(update, (
            f"Current channel: <code>{F.esc(current)}</code>\n"
            "Set with <code>/setchannel @yourchannel</code> or <code>/setchannel -100123456789</code>, "
            "or <code>/setchannel off</code>.\n"
            "<i>Add the bot to the channel as an admin first.</i>"))
    target = context.args[0]
    if target.lower() == "off":
        prev = db.get_config("channel_id") or CONFIG.channel_id
        if prev and prev.lstrip("-").isdigit():  # stop auto-broadcasting to it
            cs = db.get_settings(int(prev))
            cs.digests_enabled = False
            db.save_settings(cs)
        await asyncio.to_thread(db.set_config, "channel_id", "")
        return await _reply(update, "✅ Channel broadcasting disabled.")

    await asyncio.to_thread(db.set_config, "channel_id", target)
    extra = ""
    if target.lstrip("-").isdigit():
        # Register the channel as a subscriber so it auto-receives content.
        owner_s = _settings(update)
        cs = db.get_settings(int(target))
        cs.digests_enabled = True
        cs.news_interval_min = owner_s.news_interval_min
        cs.base, cs.quote, cs.risk, cs.model = owner_s.base, owner_s.quote, owner_s.risk, owner_s.model
        db.save_settings(cs)
        extra = ("\nIt will now auto-receive <b>scheduled digests + breaking news + trade tickets</b> "
                 "for <b>" + F.esc(owner_s.symbol) + "</b>. Mute anytime with <code>/setchannel off</code>.")
    else:
        extra = ("\n<i>Tip: use the numeric -100… id (not @name) so I can also auto-broadcast "
                 "digests/news there. /post still works either way.</i>")
    await _reply(update, f"✅ Broadcast channel set to <code>{F.esc(target)}</code>.{extra}")


def _ai_numstr(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ai_intstr(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _ai_bool(v) -> str:
    return "1" if str(v).strip().lower() in ("on", "true", "1", "yes", "y") else "0"


async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage the autonomous AI trader (writes app_config; mt5_bot reads it live)."""
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    args = context.args
    if not args or args[0].lower() in ("status", "help"):
        def cur(key, default):
            v = db.get_config(key)
            return v if v is not None else str(default)
        lines = [
            "🤖 <b>Autonomous AI trader</b> (Kimi originates its own trades)",
            f"• enabled: <b>{cur('ai.enabled', CONFIG.ai_autonomous_enabled)}</b>",
            f"• mode: <b>{cur('ai.mode', CONFIG.ai_autonomous_mode)}</b> (fallback = only when engine finds nothing; both = every scan)",
            f"• risk/trade: <b>{cur('ai.risk_pct', CONFIG.ai_risk_pct)}%</b>",
            f"• evaluates every: <b>{cur('ai.scan_seconds', CONFIG.ai_scan_seconds)}s</b>",
            f"• kill-switch: <b>{cur('ai.kill_switch_enabled', CONFIG.ai_kill_switch_enabled)}</b> "
            f"(limit ${cur('ai.daily_loss_kill', CONFIG.ai_daily_loss_kill_usd)})",
            f"• max concurrent AI trades: <b>{cur('ai.max_concurrent', CONFIG.ai_max_concurrent)}</b>",
            f"• cooldown between trades: <b>{cur('ai.min_minutes', CONFIG.ai_min_minutes_between)}m</b> (0 = none)",
            f"• min confidence: <b>{cur('ai.min_confidence', CONFIG.ai_min_confidence)}</b>",
            f"• min R:R: <b>{cur('ai.min_rr', CONFIG.ai_min_rr)}</b>",
            f"• require approval: <b>{cur('ai.require_approval', CONFIG.ai_require_approval)}</b>",
            "",
            "<i>Change (applies LIVE to the running MT5 trader):</i>",
            "<code>/ai on</code> · <code>/ai off</code> · <code>/ai mode both|fallback</code> · "
            "<code>/ai risk 0.5</code> · <code>/ai scan 30</code> · <code>/ai killswitch on|off</code> · "
            "<code>/ai kill 200</code> · <code>/ai concurrent 1</code> · <code>/ai cooldown 0</code> · "
            "<code>/ai confidence 72</code> · <code>/ai rr 1.5</code> · <code>/ai approval on|off</code>",
            "<i>Approve a pending AI trade:</i> <code>/aiapprove &lt;id&gt;</code> · <code>/aireject &lt;id&gt;</code>",
        ]
        return await _reply(update, "\n".join(lines))

    sub = args[0].lower()
    val = args[1] if len(args) > 1 else None
    if sub in ("on", "off"):
        await asyncio.to_thread(db.set_config, "ai.enabled", "1" if sub == "on" else "0")
        return await _reply(update, f"✅ AI trader <b>{'ENABLED' if sub == 'on' else 'DISABLED'}</b> (live).")

    mapping = {
        "enabled": ("ai.enabled", _ai_bool),
        "mode": ("ai.mode", lambda v: v.lower() if v and v.lower() in ("fallback", "both", "primary") else None),
        "risk": ("ai.risk_pct", _ai_numstr),
        "scan": ("ai.scan_seconds", _ai_intstr),
        "killswitch": ("ai.kill_switch_enabled", _ai_bool),
        "kill": ("ai.daily_loss_kill", _ai_numstr),
        "concurrent": ("ai.max_concurrent", _ai_intstr),
        "cooldown": ("ai.min_minutes", _ai_intstr),
        "confidence": ("ai.min_confidence", _ai_intstr),
        "rr": ("ai.min_rr", _ai_numstr),
        "approval": ("ai.require_approval", _ai_bool),
    }
    if sub not in mapping or val is None:
        return await _reply(update, "Usage: <code>/ai status</code> · <code>/ai on|off</code> · "
                            "<code>/ai &lt;mode|risk|scan|killswitch|kill|concurrent|cooldown|confidence|rr|approval&gt; &lt;value&gt;</code>")
    key, conv = mapping[sub]
    cval = conv(val)
    if cval is None:
        return await _reply(update, f"⚠️ Bad value for <b>{sub}</b>: <code>{F.esc(str(val))}</code>")
    await asyncio.to_thread(db.set_config, key, str(cval))
    return await _reply(update, f"✅ AI <b>{sub}</b> → <b>{F.esc(str(cval))}</b> (live).")


async def cmd_aiapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    if not context.args:
        return await _reply(update, "Usage: <code>/aiapprove &lt;id&gt;</code>")
    pid = context.args[0].strip()
    await asyncio.to_thread(db.set_config, f"ai.approve.{pid}", "1")
    return await _reply(update, f"✅ Approved AI trade <b>#{F.esc(pid)}</b> — it will be placed on the "
                        "next scan (re-validated at the live price).")


async def cmd_aireject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    if not context.args:
        return await _reply(update, "Usage: <code>/aireject &lt;id&gt;</code>")
    pid = context.args[0].strip()
    await asyncio.to_thread(db.set_config, f"ai.reject.{pid}", "1")
    return await _reply(update, f"🚫 Rejected AI trade <b>#{F.esc(pid)}</b>.")


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle the MT5 trader's London/NY session filter (default OFF = trade 24h)."""
    if not _is_owner(update):
        return await _reply(update, "⛔ Owner only.")
    args = context.args
    v = db.get_config("session.filter_enabled")
    cur = v if v is not None else str(CONFIG.session_filter_enabled)
    on = str(cur).strip().lower() in ("1", "true", "yes", "on", "y")
    if not args or args[0].lower() == "status":
        return await _reply(update,
            f"🕗 <b>London/NY session filter</b>: <b>{'ON' if on else 'OFF'}</b>\n"
            "• <b>ON</b> = only take trades ~07:00–21:00 UTC (skips thin Asian hours).\n"
            "• <b>OFF</b> = trade 24h whenever the market is actually open (default).\n"
            "<code>/session on</code> · <code>/session off</code>\n"
            "<i>(The real market-closed check is separate and always on.)</i>")
    sub = args[0].lower()
    if sub in ("on", "off"):
        await asyncio.to_thread(db.set_config, "session.filter_enabled", "1" if sub == "on" else "0")
        return await _reply(update,
            f"✅ London/NY session filter <b>{'ON' if sub == 'on' else 'OFF'}</b> (live). "
            + ("Now only trading London/NY hours." if sub == "on"
               else "Now trading 24h whenever the market is open."))
    return await _reply(update, "Usage: <code>/session on|off|status</code>")


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (_is_owner(update) or db.is_admin(update.effective_user.id)):
        return await _reply(update, "⛔ Owner/admins only.")
    channel = _resolve_channel()
    if channel is None:
        return await _reply(update, "No channel set. Owner: use <code>/setchannel</code> first.")
    s = _settings(update)

    # /post <text> → broadcast custom text; /post (or /post signal) → broadcast a fresh signal.
    custom = " ".join(context.args).strip() if context.args else ""
    try:
        if custom and custom.lower() != "signal":
            await context.bot.send_message(channel, custom, parse_mode=HTML, disable_web_page_preview=True)
            return await _reply(update, "✅ Posted to the channel.")
        msg = await _reply(update, "📣 Generating a signal to post…")
        profile = get_profile(s.risk)
        result = await asyncio.to_thread(run_analysis, s.symbol, profile, False, s.model or None)
        sig = result["signal"]
        await context.bot.send_message(channel, F.fmt_signal(sig), parse_mode=HTML, disable_web_page_preview=True)
        ok, _reason = ticket_mod.should_emit_ticket(result, profile)
        if ok:
            t = await asyncio.to_thread(ticket_mod.build_ticket, result, profile, False)
            if t and t.valid:
                await context.bot.send_message(channel, F.fmt_ticket(t), parse_mode=HTML, disable_web_page_preview=True)
        await msg.edit_text("✅ Posted to the channel.", parse_mode=HTML)
    except Exception as e:  # noqa: BLE001
        log.exception("post failed")
        await _reply(update, f"⚠️ Couldn't post: {F.esc(e)}\n<i>Is the bot an admin of the channel?</i>")


# ── inline-button callbacks ──────────────────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        return await query.answer("Not authorised.", show_alert=True)
    await query.answer()
    data = query.data or ""
    s = db.get_settings(update.effective_chat.id)

    if data.startswith("asset:"):
        sym = data.split(":", 1)[1]
        base, _, quote = sym.partition("/")
        s.base, s.quote = base, quote or s.quote
        db.save_settings(s)
        await query.edit_message_text(f"✅ Asset set to <b>{F.esc(s.symbol)}</b>.", parse_mode=HTML)
    elif data.startswith("ccy:"):
        s.quote = data.split(":", 1)[1]
        db.save_settings(s)
        await query.edit_message_text(
            f"✅ Quote currency set to <b>{F.esc(s.quote)}</b> → <b>{F.esc(s.symbol)}</b>.", parse_mode=HTML)
    elif data.startswith("risk:"):
        level = data.split(":", 1)[1]
        s.risk = level
        db.save_settings(s)
        await query.edit_message_text(
            "✅ Risk updated — applies instantly.\n\n" + get_profile(level).describe(), parse_mode=HTML)
    elif data.startswith("model:"):
        info = mc.by_slug(data.split(":", 1)[1])
        if info:
            s.model = info.id
            db.save_settings(s)
            await query.edit_message_text(
                f"✅ AI model set to <b>{F.esc(info.name)}</b>\n<code>{F.esc(info.id)}</code>\n\n{F.esc(info.blurb)}",
                parse_mode=HTML)
