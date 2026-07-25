"""Market data provider.

Primary: Twelve Data (XAU/USD spot, intraday candles, history; same API for
FX / metals / crypto / indices). Fallback: yfinance for history when Twelve
Data is unavailable or rate-limited.

Everything here is synchronous/blocking by design — callers run it via
``asyncio.to_thread`` so the bot's event loop stays responsive.
"""

from __future__ import annotations

import logging

import requests

from config import CONFIG
from core.models import Candle

log = logging.getLogger(__name__)

TD_BASE = "https://api.twelvedata.com"

# Canonical intervals the app uses (mixed timeframes).
CANON_INTERVALS = ["1min", "5min", "15min", "30min", "1h", "4h", "1day"]

# yfinance interval + lookback period mapping.
_YF_INTERVAL = {
    "1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
    "1h": "60m", "4h": "60m", "1day": "1d",
}
_YF_PERIOD = {"1m": "5d", "5m": "30d", "15m": "45d", "30m": "45d", "60m": "60d", "1d": "1y"}

_METALS = {"XAU": "GC=F", "XAG": "SI=F", "XPT": "PL=F", "XPD": "PA=F"}
_CRYPTOS = {"BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "BNB", "AVAX", "DOT"}


def _yf_symbol(symbol: str) -> str:
    """Map a 'BASE/QUOTE' symbol to a Yahoo Finance ticker."""
    base, _, quote = symbol.partition("/")
    base, quote = base.upper(), (quote or "USD").upper()
    if base in _METALS and quote == "USD":
        return _METALS[base]
    if base in _CRYPTOS:
        return f"{base}-{quote}"
    return f"{base}{quote}=X"  # FX pair, e.g. EURUSD=X


class MarketData:
    def __init__(self) -> None:
        self.td_key = CONFIG.twelvedata_api_key

    # ── public API ──────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> float:
        """Latest spot price for the symbol."""
        if self.td_key:
            try:
                r = requests.get(
                    f"{TD_BASE}/price",
                    params={"symbol": symbol, "apikey": self.td_key},
                    timeout=12,
                )
                j = r.json()
                if isinstance(j, dict) and "price" in j:
                    return float(j["price"])
                log.warning("Twelve Data price error for %s: %s", symbol, j)
            except Exception as e:  # noqa: BLE001
                log.warning("Twelve Data price fetch failed: %s", e)
        # Fallback: last close from daily candles.
        candles = self.get_candles(symbol, "1day", outputsize=2)
        if candles:
            return candles[-1].close
        raise RuntimeError(f"Could not fetch a price for {symbol}")

    def get_candles(self, symbol: str, interval: str = "1h", outputsize: int = 200) -> list[Candle]:
        """OHLC candles, oldest-first. Falls back to yfinance."""
        if interval not in CANON_INTERVALS:
            interval = "1h"
        if self.td_key:
            candles = self._td_candles(symbol, interval, outputsize)
            if candles:
                return candles
        return self._yf_candles(symbol, interval, outputsize)

    # ── Twelve Data ─────────────────────────────────────────────────────
    def _td_candles(self, symbol: str, interval: str, outputsize: int) -> list[Candle]:
        try:
            r = requests.get(
                f"{TD_BASE}/time_series",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "outputsize": min(outputsize, 5000),
                    "order": "ASC",          # oldest first
                    "apikey": self.td_key,
                },
                timeout=15,
            )
            j = r.json()
            values = j.get("values") if isinstance(j, dict) else None
            if not values:
                if isinstance(j, dict) and j.get("status") == "error":
                    log.warning("Twelve Data candles error for %s: %s", symbol, j.get("message"))
                return []
            out = []
            for v in values:
                out.append(Candle(
                    dt=v["datetime"],
                    open=float(v["open"]), high=float(v["high"]),
                    low=float(v["low"]), close=float(v["close"]),
                    volume=float(v.get("volume") or 0),
                ))
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("Twelve Data candles fetch failed: %s", e)
            return []

    # ── yfinance fallback ───────────────────────────────────────────────
    def _yf_candles(self, symbol: str, interval: str, outputsize: int) -> list[Candle]:
        try:
            import yfinance as yf
        except ImportError:
            log.error("yfinance not installed; no market-data fallback available.")
            return []
        try:
            yf_sym = _yf_symbol(symbol)
            yf_int = _YF_INTERVAL[interval]
            period = _YF_PERIOD.get(yf_int, "1mo")
            df = yf.download(yf_sym, period=period, interval=yf_int,
                             auto_adjust=False, progress=False)
            if df is None or df.empty:
                return []
            # Flatten possible MultiIndex columns (single-ticker downloads).
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            if interval == "4h":
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max", "Low": "min",
                    "Close": "last", "Volume": "sum",
                }).dropna()
            out = []
            for idx, row in df.tail(outputsize).iterrows():
                out.append(Candle(
                    dt=str(idx),
                    open=float(row["Open"]), high=float(row["High"]),
                    low=float(row["Low"]), close=float(row["Close"]),
                    volume=float(row["Volume"]) if "Volume" in row else 0.0,
                ))
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance fetch failed for %s: %s", symbol, e)
            return []


# Module-level singleton for convenience.
MARKET = MarketData()
