"""Per-instrument registry — the SINGLE source of truth for trading any market.

Each tradeable symbol (gold, EUR/USD, BTC, …) is one frozen ``Instrument`` entry
that fully describes its trading identity, price scale, magic offset, news sources,
economic-calendar currencies, session window and weekend behaviour. The live trader
(``mt5_bot.py``) is launched per-symbol (``BOT_SYMBOL=EURUSD python mt5_bot.py``) and
reads its instrument from here — so **adding BTC later is literally one entry below**,
no engine changes.

Design from the market-hours + multi-symbol-news research (wf_51a6eb87):
  • price digits + contract size are FALLBACKS — the bot reads them LIVE from
    ``mt5.symbol_info`` at connect and only uses these if the terminal is unavailable.
  • ``magic_base`` values are spaced far apart so 12 combos/symbol never collide.
  • ``is_crypto`` ⇒ 24/7, weekend-exempt, no central-bank calendar.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    mt5_symbol: str              # broker order symbol as in Market Watch ("XAUUSD")
    sig_symbol: str              # slash form the signal/data/news engines expect ("XAU/USD")
    display: str                 # short label shown in Telegram ("GOLD", "EURUSD", "BTC")
    emoji: str                   # instrument emoji for messages
    instrument_name: str         # natural phrase for the Kimi prompt ("gold (XAU/USD)")
    news_query: str              # web/research/social search phrase
    calendar_currencies: tuple   # Forex-Factory 'country' (=currency) codes that trigger a blackout
    digits: int = 2              # price decimals FALLBACK (live value from symbol_info wins)
    contract_size: float = 100.0  # units/lot FALLBACK (live value from symbol_info wins)
    magic_base: int = 20260626   # per-symbol MAGIC offset (12 combos → base..base+11)
    is_crypto: bool = False      # 24/7, weekend-exempt, no central-bank calendar
    session_start_utc: int = 7   # active London/NY window (ignored when is_crypto)
    session_end_utc: int = 21
    # ── M5 cost-clearing floors (research: on M5 a stop/TP inside the spread bleeds) ──
    min_stop_price: float = 2.5  # absolute minimum stop distance in PRICE units
    min_tp1_price: float = 1.5   # absolute minimum TP1 distance (must clear ~3× spread)
    # ABSOLUTE spread ceiling (price units): refuse NEW entries when the live spread is
    # above it — a thin-edge instrument (EURUSD backtest: PF 1.23 → 0.85 at a 1.3-pip
    # spread, breakeven ≈0.8 pip) only trades when costs clear. 0 = no absolute cap
    # (the ATR-relative SPREAD_VETO still applies to every symbol).
    max_spread_price: float = 0.0

    @property
    def base(self) -> str:
        return self.sig_symbol.partition("/")[0]

    @property
    def quote(self) -> str:
        return self.sig_symbol.partition("/")[2]


# ── the registry (add a new market by adding ONE entry) ─────────────────────────
INSTRUMENTS: dict[str, Instrument] = {
    "XAUUSD": Instrument(
        mt5_symbol="XAUUSD", sig_symbol="XAU/USD", display="GOLD", emoji="🟡",
        instrument_name="gold (XAU/USD)",
        news_query="gold XAU/USD safe-haven real yields Fed dollar geopolitics",
        calendar_currencies=("USD",),
        digits=2, contract_size=100.0, magic_base=20260626,
        is_crypto=False, session_start_utc=7, session_end_utc=21,
        min_stop_price=2.5, min_tp1_price=1.5,   # gold: ~$2.5 stop, TP1 ~$1.5 (clears spread)
        # SPREAD VETO (4-regime Dukascopy walk-forward, 2026-07-03): the M5 gold edge is
        # THIN + spread-sensitive — secondentry PF 1.31 at 0.13 spread but 0.91 at ~0.67
        # (break-even ≈0.45 spread). Refuse new entries above 0.35 (safety margin below
        # break-even) so the bot only trades when the spread lets the edge clear costs.
        max_spread_price=0.35,
    ),
    "EURUSD": Instrument(
        mt5_symbol="EURUSD", sig_symbol="EUR/USD", display="EURUSD", emoji="🇪🇺",
        instrument_name="EUR/USD (euro vs US dollar)",
        news_query="EUR/USD ECB eurozone inflation vs Fed dollar rate divergence",
        calendar_currencies=("EUR", "USD"),
        digits=5, contract_size=100000.0, magic_base=20260700,
        is_crypto=False, session_start_utc=7, session_end_utc=21,
        min_stop_price=0.0009, min_tp1_price=0.0005,   # EURUSD: ~9-pip stop, ~5-pip TP1
        # EURUSD's second-entry edge is spread-fragile (backtest: net-negative ≥~0.8 pip)
        # → only trade when the spread is genuinely tight.
        max_spread_price=0.00008,                      # 0.8 pip ceiling
    ),
    # Ready for the future — the user asked that adding BTC be trivial. Uncomment /
    # launch `BOT_SYMBOL=BTCUSD python mt5_bot.py` once the demo quotes it.
    "BTCUSD": Instrument(
        mt5_symbol="BTCUSD", sig_symbol="BTC/USD", display="BTC", emoji="₿",
        instrument_name="bitcoin (BTC/USD)",
        news_query="bitcoin BTC price ETF flows halving regulation crypto",
        calendar_currencies=("USD",),   # macro-only; crypto has no central-bank calendar
        digits=2, contract_size=1.0, magic_base=20260800,
        is_crypto=True, session_start_utc=0, session_end_utc=24,
        min_stop_price=40.0, min_tp1_price=25.0,   # BTC: wide absolute floors (rough; tune on live)
    ),
}


def get_instrument(mt5_symbol: str) -> Instrument:
    """Look up an instrument by broker symbol (defaults to gold if unknown)."""
    return INSTRUMENTS.get((mt5_symbol or "").upper().strip(), INSTRUMENTS["XAUUSD"])
