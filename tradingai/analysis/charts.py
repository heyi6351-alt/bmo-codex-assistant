"""Annotated trade-chart images for Telegram — candles + EMAs + support/resistance
+ entry/SL/TP lines, rendered headless (Agg) to PNG bytes. So the user SEES the setup,
not just numbers. Rendering happens only on trade events (open/close), never on the
5-second management loop, so it can't delay stop management.
"""
from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")            # headless backend (VPS/Docker safe) — set before pyplot
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf         # noqa: E402
import pandas as pd              # noqa: E402

log = logging.getLogger("mt5_bot.charts")


def render_trade_chart(candles, *, entry, sl, tps, direction, support=None,
                       resistance=None, digits=2, title="", n=90) -> bytes | None:
    """Render the last ``n`` candles with the trade's levels overlaid.
    Returns PNG bytes, or None if rendering isn't possible (never raises)."""
    try:
        rows = candles[-n:]
        if len(rows) < 10:
            return None
        df = pd.DataFrame([{
            "Date": pd.to_datetime(c.dt), "Open": c.open, "High": c.high,
            "Low": c.low, "Close": c.close, "Volume": c.volume,
        } for c in rows]).set_index("Date")

        up = direction == "long"
        # Level lines: entry (blue), SL (red), each TP (green). S/R zones (grey dashed).
        hlines, colors, styles, widths = [], [], [], []
        def add(v, color, style="--", w=1.1):
            if v:
                hlines.append(float(v)); colors.append(color); styles.append(style); widths.append(w)
        add(entry, "#1f77b4", "-", 1.4)
        add(sl, "#d62728")
        for tp in (tps or [])[:8]:   # dynamic ladder: up to RUNG_MAX rungs
            add(tp, "#2ca02c")
        add(support, "#7f7f7f", ":")
        add(resistance, "#7f7f7f", ":")

        style = mpf.make_mpf_style(base_mpf_style="charles", gridstyle=":", facecolor="#0e1117",
                                   edgecolor="#30363d", figcolor="#0e1117",
                                   rc={"axes.labelcolor": "#c9d1d9", "xtick.color": "#c9d1d9",
                                       "ytick.color": "#c9d1d9", "axes.titlecolor": "#c9d1d9"})
        buf = io.BytesIO()
        mpf.plot(
            df, type="candle", style=style, mav=(20, 50), volume=False,
            hlines=dict(hlines=hlines, colors=colors, linestyle=styles, linewidths=widths),
            title=f"\n{title}", ylabel="", figsize=(9, 5), tight_layout=True,
            savefig=dict(fname=buf, dpi=110, bbox_inches="tight", facecolor="#0e1117"),
        )
        plt.close("all")
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001 — charts must NEVER break trading
        log.debug("chart render failed: %s", e)
        return None
