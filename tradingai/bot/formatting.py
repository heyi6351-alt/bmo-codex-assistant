"""Message formatting — Telegram HTML parse mode.

All dynamic text is escaped with ``esc`` before interpolation to avoid breaking
the HTML or injecting markup from news titles.
"""

from __future__ import annotations

import html
import re

from config import CONFIG
from core.models import DISCLAIMER, NewsItem, Signal, TradeTicket, Trade, UserSettings
from risk.profiles import RiskProfile


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _conf_bar(conf: float) -> str:
    filled = max(0, min(5, round(conf * 5)))
    return "▰" * filled + "▱" * (5 - filled)


def _sentiment_emoji(label: str) -> str:
    label = (label or "").lower()
    if "bull" in label:
        return "🟢" if label == "bullish" else "🟩"
    if "bear" in label:
        return "🔴" if label == "bearish" else "🟥"
    return "⚪️"


_DISPLAY = {"XAU": "GOLD", "XAG": "SILVER", "XPT": "PLATINUM", "XPD": "PALLADIUM", "WTI": "OIL"}


def _disp(symbol: str) -> str:
    """Friendly display name: XAU/USD → GOLD, EUR/USD → EURUSD, BTC/USD → BTC."""
    base, _, quote = symbol.partition("/")
    base, quote = base.upper(), (quote or "").upper()
    if base in _DISPLAY:
        return _DISPLAY[base]
    return f"{base}{quote}" if quote else base


# ── price ──────────────────────────────────────────────────────────────────────
def fmt_price(symbol: str, price: float) -> str:
    return f"💲 <b>{esc(symbol)}</b>: <code>{price}</code>"


# ── signal ──────────────────────────────────────────────────────────────────────
def fmt_signal(sig: Signal | None) -> str:
    """Compact signal card — used for /signal, scheduled digests and the channel."""
    if sig is None:
        return "⚠️ Not enough market data for a signal right now. Try again shortly."
    disp = _disp(sig.symbol)

    if sig.direction == "flat":
        rv = sig.llm_review
        why = (rv.summary if rv and rv.summary else sig.invalidation_reason) or "No high-conviction setup right now."
        return (f"⚪️ <b>{esc(disp)} — NO TRADE</b> <i>({esc(sig.timeframe)})</i>\n{esc(why[:240])}")

    # The card is the "read"; exact entry/SL/TP live in the ticket message that
    # follows (one source of levels → no confusing duplicate numbers).
    side = "🟢 BUY" if sig.direction == "long" else "🔴 SELL"
    i = sig.indicators
    trend = "up" if i.ema_fast > i.ema_slow else "down" if i.ema_fast < i.ema_slow else "flat"
    L = [
        f"{side} <b>{esc(disp)}</b> <i>({esc(sig.timeframe)} · {esc(sig.style)})</i> · conf {int(sig.confidence * 100)}%",
        f"📊 RSI {i.rsi14:.0f} · ADX {i.adx14:.0f} · trend {trend}",
    ]
    if sig.ml_prob_up >= 0:
        L.append(f"🤖 ML {esc(sig.ml_direction)} ({int(sig.ml_prob_up * 100)}%↑, acc {int(sig.ml_cv_acc * 100)}%)")
    rv = sig.llm_review
    if rv and rv.summary:
        L.append(f"🧠 {esc(rv.summary)}")
    if rv and rv.risk_verdict == "approve_reduced_size":
        L.append("⚠️ Elevated risk — consider smaller size.")
    elif rv and rv.risk_verdict == "veto":
        L.append("⚠️ Risk team flagged this — trade smaller or skip.")
    L.append("⚠️ Not financial advice.")
    return "\n".join(L)


def fmt_signal_full(sig: Signal | None) -> str:
    """Full signal + multi-agent reasoning — used for /analyze (deep dive).
    Layout: a SUMMARY card on top, then the full breakdown below."""
    if sig is None:
        return "⚠️ Not enough market data to build a signal right now. Try again shortly."

    disp = _disp(sig.symbol)
    head = {"long": "🟢 BUY", "short": "🔴 SELL", "flat": "⚪️ NO TRADE"}.get(sig.direction, sig.direction)
    rv = sig.llm_review
    i = sig.indicators
    dec = 2 if sig.entry >= 100 else 4
    obv_arrow = "↑" if i.obv_slope > 0 else "↓" if i.obv_slope < 0 else "→"

    L = [f"<b>{esc(disp)} — {head}</b>  <i>({esc(sig.timeframe)} · {esc(sig.style)})</i>",
         f"Confidence: {_conf_bar(sig.confidence)} {int(sig.confidence * 100)}%"]
    if rv and rv.summary:
        L += ["", f"🧠 <b>Summary</b>: {esc(rv.summary)}"]

    if sig.direction != "flat":
        L += ["", "<b>📋 Trade plan</b>",
              f"🎯 Entry <code>{sig.entry}</code>",
              f"🛑 Stop <code>{sig.stop_loss}</code>  <i>(ATR {sig.atr})</i>"]
        for n, tp in enumerate(sig.take_profits, 1):
            L.append(f"✅ TP{n} <code>{tp.price}</code>  <i>({tp.r_multiple:g}R · close {tp.close_pct:g}%)</i>")
        L.append(f"⚖️ R:R 1:{sig.risk_reward:g}  ·  risk {sig.position_size.get('risk_pct')}%/trade")
        if sig.position_size.get("note"):
            L.append(f"<i>{esc(sig.position_size.get('note'))}</i>")

    L += ["", "<b>📊 Indicators</b>",
          f"RSI {i.rsi14:.0f} · ADX {i.adx14:.0f} · MACDh {i.macd_hist:+.3f}",
          f"Stoch {i.stoch_k:.0f}/{i.stoch_d:.0f} · PROC {i.proc:+.2f}% · OBV {obv_arrow}",
          f"EMA20 {i.ema_fast:.{dec}f} / EMA50 {i.ema_slow:.{dec}f}",
          f"Support {i.support:.{dec}f} · Resistance {i.resistance:.{dec}f}"]
    if sig.ml_prob_up >= 0:
        ml_arrow = "🟢" if sig.ml_direction == "long" else "🔴"
        L.append(f"🤖 ML {ml_arrow} {esc(sig.ml_direction)} (P↑ {sig.ml_prob_up:.0%}, acc {sig.ml_cv_acc:.0%})")

    if rv and (rv.technical or rv.bull_case or rv.bear_case):
        L.append("")
        L.append("<b>🧠 AI read</b>")
        if rv.technical:
            L.append(f"📐 {esc(rv.technical)}")
        if rv.bull_case:
            L.append(f"🐂 {esc(rv.bull_case)}")
        if rv.bear_case:
            L.append(f"🐻 {esc(rv.bear_case)}")
        if rv.risk_verdict:
            note = f" — {esc(rv.portfolio_note)}" if rv.portfolio_note else ""
            L.append(f"🧮 Verdict: <b>{esc(rv.risk_verdict)}</b>{note}")

    L += ["", f"⛔ <b>Invalidation</b>: {esc(sig.invalidation_reason)}", "", DISCLAIMER]
    return "\n".join(L)


# ── news ─────────────────────────────────────────────────────────────────────────
def _news_time(published: str) -> str:
    """Pull a compact HH:MM out of any published-date format (best effort)."""
    m = re.search(r"\b(\d{1,2}:\d{2})\b", published or "")
    return m.group(1) if m else ""


def _mood_label(score: float) -> str:
    if score <= -0.35:
        return "Bearish"
    if score <= -0.15:
        return "Slightly bearish"
    if score < 0.15:
        return "Neutral"
    if score < 0.35:
        return "Slightly bullish"
    return "Bullish"


def fmt_news(items: list[NewsItem], symbol: str, limit: int = 6, header: str = "📰 Latest") -> str:
    if not items:
        return f"{header} — <b>{esc(symbol)}</b>\nNothing new right now."
    shown = items[:limit]

    scores = [it.sentiment_score for it in shown if it.sentiment_score is not None]
    mood = ""
    if scores:
        avg = sum(scores) / len(scores)
        mood = f"  <i>· mood {_sentiment_emoji(_mood_label(avg))} {_mood_label(avg)}</i>"

    L = [f"{header} — <b>{esc(symbol)}</b>{mood}", ""]
    for it in shown:
        title = it.title.strip()
        if len(title) > 100:
            title = title[:99].rstrip() + "…"
        title = esc(title)
        if it.url:
            title = f'<a href="{esc(it.url)}">{title}</a>'
        t = _news_time(it.published)
        meta = esc(it.source) + (f" · {t}" if t else "")
        L.append(f"{_sentiment_emoji(it.sentiment_label)} {title}")
        L.append(f"   <i>{meta}</i>")
    return "\n".join(L)


# ── deep-research briefing ───────────────────────────────────────────────────────
def fmt_research(research_out: dict) -> str:
    if not research_out:
        return ""
    if research_out.get("note"):
        return f"🔬 <i>{esc(research_out['note'])}</i>"
    report = research_out.get("report", "")
    if not report:
        return ""
    L = ["🔬 <b>Deep research</b>", esc(report)]
    sources = research_out.get("sources", [])
    if sources:
        L.append("")
        L.append("<i>Sources:</i> " + " · ".join(
            f'<a href="{esc(u)}">[{n}]</a>' for n, u in enumerate(sources[:6], 1)))
    return "\n".join(L)


# ── settings ───────────────────────────────────────────────────────────────────
def _model_name(model_id: str) -> str:
    from analysis.model_catalog import by_id
    info = by_id(model_id)
    return info.name if info else model_id


def fmt_settings(settings: UserSettings, profile: RiskProfile) -> str:
    active_model = settings.model or CONFIG.model_deep
    return "\n".join([
        "⚙️ <b>Your settings</b>",
        f"• Asset: <b>{esc(settings.symbol)}</b>",
        f"• Base / quote: <b>{esc(settings.base)}</b> / <b>{esc(settings.quote)}</b>",
        f"• AI model: <b>{esc(_model_name(active_model))}</b>",
        f"• Scheduled digests: <b>{'ON' if settings.digests_enabled else 'OFF'}</b>",
        (f"• Breaking news every: <b>{settings.news_interval_min} min</b>"
         if settings.news_interval_min < 60
         else f"• Breaking news every: <b>{settings.news_interval_min / 60:g} h</b>"),
        "",
        profile.describe(),
    ])


def fmt_models(active_id: str) -> str:
    from analysis.model_catalog import CATALOG
    L = ["🧠 <b>AI analysis model</b> — the brain behind /signal &amp; /analyze", ""]
    for m in CATALOG:
        mark = "✅ " if m.id == active_id else ""
        caps = " · ".join([t for t, ok in (("reasoning", m.reasoning), ("tool-calling", m.tools)) if ok])
        L.append(f"{mark}{m.tier} <b>{esc(m.name)}</b> — <i>{esc(caps)}</i>")
        L.append(f"   {esc(m.blurb)}")
        L.append(f"   <code>{esc(m.id)}</code>")
        L.append("")
    L.append("👉 Tap a button below, set any id with <code>/model &lt;id&gt;</code>, "
             "or list every free model with <code>/model all</code>.")
    return "\n".join(L)


# ── trades ─────────────────────────────────────────────────────────────────────
def fmt_trades(trades: list[Trade]) -> str:
    if not trades:
        return "📭 No tracked trades. Add one with:\n<code>/track XAU/USD long 2350 2340 2390</code>"
    L = ["📋 <b>Tracked trades</b>", ""]
    for t in trades:
        arrow = "🟢" if t.direction == "long" else "🔴"
        bits = [f"#{t.id} {arrow} <b>{esc(t.symbol)}</b> {esc(t.direction)} @ <code>{t.entry}</code>"]
        if t.stop:
            bits.append(f"SL <code>{t.stop}</code>")
        if t.take_profit:
            bits.append(f"TP <code>{t.take_profit}</code>")
        L.append("  ".join(bits))
    L.append("")
    L.append("Stop monitoring one with <code>/untrack &lt;id&gt;</code>.")
    return "\n".join(L)


# ── backtest ─────────────────────────────────────────────────────────────────
def fmt_backtest(stats: dict) -> str:
    if stats.get("error"):
        return f"⚠️ {esc(stats['error'])}"
    head = f"📈 <b>Backtest — {esc(stats['symbol'])} {esc(stats['interval'])}</b> ({stats['bars']} bars)"
    if stats.get("n_trades", 0) == 0:
        return head + "\n" + esc(stats.get("note", "No trades."))
    def _pf(s):
        pf = s["profit_factor"]
        return "∞" if pf == float("inf") else f"{pf:.2f}"

    def _line(s):
        if s.get("n_trades", 0) == 0:
            return "no trades"
        return (f"{s['n_trades']} trades · WR <b>{s['win_rate'] * 100:.0f}%</b> · "
                f"avg <b>{s['avg_r']:+.2f}R</b> · PF <b>{_pf(s)}</b>")

    L = [
        head,
        "",
        f"• Trades: <b>{stats['n_trades']}</b>",
        f"• Win rate: <b>{stats['win_rate'] * 100:.0f}%</b>",
        f"• Avg result: <b>{stats['avg_r']:+.2f}R</b> · Total: <b>{stats['total_r']:+.1f}R</b>",
        f"• Profit factor: <b>{_pf(stats)}</b>",
        f"• Max drawdown: <b>{stats['max_drawdown_r']:.1f}R</b>",
        f"• Best/worst: <b>{stats['best_r']:+.1f}R</b> / <b>{stats['worst_r']:+.1f}R</b>",
    ]
    if "out_of_sample" in stats:
        L += [
            "",
            "<b>Robustness check</b>",
            f"• In-sample (70%): {_line(stats['in_sample'])}",
            f"• Out-of-sample (30%): {_line(stats['out_of_sample'])}",
        ]
    L += [
        "",
        "<i>Exit = stop or first take-profit; stop assumed first if both hit in one bar. "
        "Past performance ≠ future results.</i>",
    ]
    return "\n".join(L)


# ── optimizer ────────────────────────────────────────────────────────────────
def fmt_optimize(r: dict) -> str:
    if r.get("error"):
        return f"⚠️ {esc(r['error'])}"

    def _pf(s):
        pf = s.get("profit_factor", 0)
        return "∞" if pf == float("inf") else f"{pf:.2f}"

    def _line(s):
        if s.get("n_trades", 0) == 0:
            return "no trades"
        return (f"{s['n_trades']} trades · WR {s['win_rate'] * 100:.0f}% · "
                f"avg {s['avg_r']:+.2f}R · PF {_pf(s)}")

    return "\n".join([
        f"🧬 <b>Optimizer — {esc(r['symbol'])} {esc(r['interval'])}</b>",
        f"<i>Searched {r['tested']} parameter sets vs your '{esc(r['base_profile'])}' profile.</i>",
        "",
        "<b>Best parameters</b>",
        f"• Stop: <b>{r['atr_mult']:g}× ATR</b>",
        f"• Trend filter: ADX ≥ <b>{r['min_adx']:g}</b>",
        f"• Confidence floor: <b>{int(r['confidence_floor'] * 100)}%</b>",
        "",
        "<b>Out-of-sample (held-out 30%)</b>",
        f"• Optimised: {_line(r['oos'])}",
        f"• Your current: {_line(r['baseline_oos'])}",
        "",
        "<i>These are suggestions from history — not a guarantee. "
        "Apply by adjusting your /risk profile. Past performance ≠ future results.</i>",
    ])


# ── trade ticket (broker-style call) ──────────────────────────────────────────
def fmt_ticket(t: TradeTicket | None) -> str:
    """Plain, copy-pasteable BUY/SELL call. Empty string if not actionable."""
    if not t or not t.valid or not t.take_profits:
        return ""
    arrow = "🟢" if t.side == "BUY" else "🔴"
    L = [f"{arrow} <b>{esc(t.display)} {t.side} MANUAL AROUND {t.entry}</b>", ""]
    L.append(f"🛑 SL {t.stop}")
    L.append("")
    L += [f"🎯 TP {i}: {tp}" for i, tp in enumerate(t.take_profits, 1)]
    L.append("")
    foot = f"⚖️ R:R 1:{t.rr:g} · risk {t.risk_pct:g}%/trade · confidence {int(t.confidence * 100)}%"
    L.append(foot)
    if t.safety_note:
        L.append(f"🛡️ {esc(t.safety_note)}")
    L.append("⚠️ Not financial advice.")
    return "\n".join(L)


# ── tournament ───────────────────────────────────────────────────────────────
def fmt_tournament(r: dict) -> str:
    if r.get("error"):
        return f"⚠️ {esc(r['error'])}"
    emoji = {"long": "🟢", "short": "🔴", "flat": "⚪️", "error": "⚠️"}
    mode = "all models" if r.get("mode") == "all" else "fast models"
    L = [f"🏆 <b>Model tournament — {esc(r['symbol'])}</b> <i>({len(r['votes'])} {mode})</i>", ""]
    for v in r["votes"]:
        e = emoji.get(v["direction"], "⚪️")
        L.append(f"{e} <b>{esc(v['model'])}</b>: {esc(v['direction'])} ({int(v['confidence'] * 100)}%)")
        if v.get("reason"):
            L.append(f"   <i>{esc(v['reason'])}</i>")
    c = r["consensus"]
    L += ["", f"📣 <b>Consensus: {emoji.get(c, '⚪️')} {esc(c).upper()}</b> "
              f"({r['agree']}/{r['total']} agree)", "", DISCLAIMER]
    return "\n".join(L)


# ── guardian alert ───────────────────────────────────────────────────────────────
def fmt_trade_alert(trade: Trade, kind: str, detail: str, severity: str = "high") -> str:
    sev = {"high": "🚨🚨", "medium": "🚨", "low": "⚠️"}.get(severity, "🚨")
    arrow = "🟢 LONG" if trade.direction == "long" else "🔴 SHORT"
    L = [
        f"{sev} <b>OPEN-TRADE ALERT</b> — {kind}",
        f"#{trade.id} {arrow} <b>{esc(trade.symbol)}</b> from <code>{trade.entry}</code>",
        "",
        esc(detail),
    ]
    return "\n".join(L)
