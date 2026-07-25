"""Inline keyboards for switching asset, quote currency, and risk profile."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Common assets (base/quote). The bot can track anything the data provider
# supports; these are quick-pick shortcuts.
ASSETS = [
    ("🪙 Gold", "XAU/USD"),
    ("🥈 Silver", "XAG/USD"),
    ("€ EUR/USD", "EUR/USD"),
    ("£ GBP/USD", "GBP/USD"),
    ("¥ USD/JPY", "USD/JPY"),
    ("₿ BTC/USD", "BTC/USD"),
    ("Ξ ETH/USD", "ETH/USD"),
    ("🛢️ WTI", "WTI/USD"),
]

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "CNY"]


def _grid(buttons, cols: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def asset_keyboard() -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(label, callback_data=f"asset:{sym}") for label, sym in ASSETS]
    return InlineKeyboardMarkup(_grid(btns, 2))


def currency_keyboard() -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(c, callback_data=f"ccy:{c}") for c in CURRENCIES]
    return InlineKeyboardMarkup(_grid(btns, 4))


def risk_keyboard() -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton("🛡️ Conservative", callback_data="risk:conservative"),
        InlineKeyboardButton("⚖️ Moderate", callback_data="risk:moderate"),
        InlineKeyboardButton("🔥 Aggressive", callback_data="risk:aggressive"),
    ]
    return InlineKeyboardMarkup(_grid(btns, 1))


def model_keyboard() -> InlineKeyboardMarkup:
    from analysis.model_catalog import CATALOG
    btns = [
        InlineKeyboardButton(("⭐ " if m.tier.startswith("⭐") else "") + m.name,
                             callback_data=f"model:{m.slug}")
        for m in CATALOG
    ]
    return InlineKeyboardMarkup(_grid(btns, 2))
