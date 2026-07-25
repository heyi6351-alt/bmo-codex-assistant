"""Application factory: builds the bot, registers handlers, schedules jobs."""

from __future__ import annotations

import logging

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import CONFIG
from core import db

from . import handlers as H
from . import jobs as J

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    ("price", "Current price of your asset"),
    ("news", "Latest news + sentiment"),
    ("social", "Reddit/X chatter + sentiment"),
    ("signal", "Full multi-timeframe signal"),
    ("ticket", "Trade ticket: BUY/SELL + SL + TPs"),
    ("analyze", "Deep web research + AI read"),
    ("backtest", "Backtest the signal logic on history"),
    ("optimize", "Tune strategy params (out-of-sample tested)"),
    ("tournament", "Several AI models vote on the trade"),
    ("asset", "Switch tracked asset"),
    ("currency", "Switch quote currency"),
    ("model", "Switch the AI model"),
    ("risk", "Set risk profile"),
    ("track", "Monitor an open trade"),
    ("trades", "List monitored trades"),
    ("untrack", "Stop monitoring a trade"),
    ("digest", "Toggle scheduled digests"),
    ("newsfreq", "Set how often you get breaking news"),
    ("settings", "Show your settings"),
    ("id", "Show your Telegram ID & role"),
    ("help", "Show help"),
]

# Owner-only extras — surfaced in the command menu for the owner's chat only.
OWNER_EXTRA_COMMANDS = [
    ("post", "Publish a message/signal to the channel"),
    ("setchannel", "Link the broadcast channel"),
    ("admins", "List authorised admins"),
    ("addadmin", "Grant a user access"),
    ("removeadmin", "Revoke a user's access"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
    # Give the owner a richer command menu (their private chat id == their user id).
    if CONFIG.owner_id:
        try:
            await app.bot.set_my_commands(
                [BotCommand(c, d) for c, d in BOT_COMMANDS + OWNER_EXTRA_COMMANDS],
                scope=BotCommandScopeChat(chat_id=CONFIG.owner_id),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Could not set owner command menu: %s", e)
    jq = app.job_queue
    jq.run_repeating(J.monitor_job, interval=CONFIG.monitor_interval_min * 60, first=25,
                     name="monitor")
    jq.run_repeating(J.digest_job, interval=CONFIG.digest_interval_min * 60, first=20,
                     name="digest")
    # Startup ping so the owner immediately knows the bot is live and can send.
    if CONFIG.owner_id:
        try:
            await app.bot.send_message(
                CONFIG.owner_id,
                f"✅ Gold bot online. I analyse every {CONFIG.digest_interval_min} min and broadcast to "
                f"all subscribers + the channel when the setup changes, plus breaking news as it hits. "
                f"Try /signal or /ticket now.")
        except Exception as e:  # noqa: BLE001
            log.warning("Could not message owner %s — send /start to the bot in DM first. (%s)",
                        CONFIG.owner_id, e)
    # Auto-subscribe the configured channel (first run only) so it broadcasts
    # scheduled digests + breaking news. Owner can mute via /setchannel off.
    ch = CONFIG.channel_id
    if ch and ch.lstrip("-").isdigit() and not db.has_settings(int(ch)):
        cs = db.get_settings(int(ch))
        cs.digests_enabled = True
        cs.news_interval_min = CONFIG.default_news_interval_min
        cs.base, cs.quote, cs.risk = CONFIG.default_base, CONFIG.default_quote, CONFIG.default_risk
        db.save_settings(cs)
        log.info("Registered channel %s for auto-broadcast (digests + hourly news).", ch)

    # 1-minute base tick; each chat fires on its own /newsfreq interval.
    jq.run_repeating(J.news_job, interval=60, first=140, name="news")
    log.info("Scheduled: monitor %dm, digest %dm, news base-tick 1m (default %dm/user).",
             CONFIG.monitor_interval_min, CONFIG.digest_interval_min,
             CONFIG.default_news_interval_min)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error while processing update", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong handling that — it's been logged. Please try again.")
    except Exception:  # noqa: BLE001
        pass  # never let the error handler itself raise


def build_application() -> Application:
    db.init_db()
    app = ApplicationBuilder().token(CONFIG.telegram_bot_token).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", H.cmd_start))
    app.add_handler(CommandHandler("help", H.cmd_help))
    app.add_handler(CommandHandler("id", H.cmd_id))
    app.add_handler(CommandHandler("settings", H.cmd_settings))
    app.add_handler(CommandHandler("price", H.cmd_price))
    app.add_handler(CommandHandler("news", H.cmd_news))
    app.add_handler(CommandHandler("social", H.cmd_social))
    app.add_handler(CommandHandler("signal", H.cmd_signal))
    app.add_handler(CommandHandler("ticket", H.cmd_ticket))
    app.add_handler(CommandHandler("analyze", H.cmd_analyze))
    app.add_handler(CommandHandler("backtest", H.cmd_backtest))
    app.add_handler(CommandHandler("optimize", H.cmd_optimize))
    app.add_handler(CommandHandler("tournament", H.cmd_tournament))
    app.add_handler(CommandHandler("asset", H.cmd_asset))
    app.add_handler(CommandHandler("currency", H.cmd_currency))
    app.add_handler(CommandHandler("model", H.cmd_model))
    app.add_handler(CommandHandler("risk", H.cmd_risk))
    app.add_handler(CommandHandler("track", H.cmd_track))
    app.add_handler(CommandHandler("trades", H.cmd_trades))
    app.add_handler(CommandHandler("untrack", H.cmd_untrack))
    app.add_handler(CommandHandler("digest", H.cmd_digest))
    app.add_handler(CommandHandler("newsfreq", H.cmd_newsfreq))
    # Owner / admin / channel
    app.add_handler(CommandHandler("setchannel", H.cmd_setchannel))
    app.add_handler(CommandHandler("post", H.cmd_post))
    app.add_handler(CommandHandler("addadmin", H.cmd_addadmin))
    app.add_handler(CommandHandler("removeadmin", H.cmd_removeadmin))
    app.add_handler(CommandHandler("admins", H.cmd_admins))
    # Autonomous AI trader control (writes app_config; the MT5 bot reads it live).
    app.add_handler(CommandHandler("ai", H.cmd_ai))
    app.add_handler(CommandHandler("aitrader", H.cmd_ai))
    app.add_handler(CommandHandler("aiapprove", H.cmd_aiapprove))
    app.add_handler(CommandHandler("aireject", H.cmd_aireject))
    app.add_handler(CommandHandler("session", H.cmd_session))
    app.add_handler(CallbackQueryHandler(H.on_callback))
    app.add_error_handler(_on_error)
    return app
