"""SQLite persistence.

A single shared connection guarded by a lock (WAL mode) — enough for a
single-user / small-group bot whose blocking I/O runs in worker threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from config import CONFIG
from core.models import Trade, UserSettings, utcnow_iso

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    chat_id           INTEGER PRIMARY KEY,
    base              TEXT NOT NULL,
    quote             TEXT NOT NULL,
    risk              TEXT NOT NULL,
    digests_enabled   INTEGER NOT NULL DEFAULT 1,
    model             TEXT NOT NULL DEFAULT '',
    news_interval_min INTEGER NOT NULL DEFAULT 5,
    last_news_at      TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_seen_news (
    chat_id    INTEGER NOT NULL,
    hash       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (chat_id, hash)
);
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    direction     TEXT NOT NULL,
    entry         REAL NOT NULL,
    stop          REAL,
    take_profit   REAL,
    size_note     TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',
    opened_at     TEXT NOT NULL,
    last_alert_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS seen_news (
    hash       TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    direction  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
    user_id  INTEGER PRIMARY KEY,
    label    TEXT NOT NULL DEFAULT '',
    added_by INTEGER,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(CONFIG.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        conn.executescript(_SCHEMA)
        # Lightweight migrations for pre-existing databases.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_settings)")}
        if "model" not in cols:
            conn.execute("ALTER TABLE user_settings ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        if "news_interval_min" not in cols:
            conn.execute("ALTER TABLE user_settings ADD COLUMN news_interval_min INTEGER NOT NULL DEFAULT 5")
        if "last_news_at" not in cols:
            conn.execute("ALTER TABLE user_settings ADD COLUMN last_news_at TEXT NOT NULL DEFAULT ''")
        conn.commit()


# ── user settings ────────────────────────────────────────────────────────────
def get_settings(chat_id: int) -> UserSettings:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM user_settings WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if row is None:
            s = UserSettings(
                chat_id=chat_id,
                base=CONFIG.default_base,
                quote=CONFIG.default_quote,
                risk=CONFIG.default_risk,
                news_interval_min=CONFIG.default_news_interval_min,
            )
            conn.execute(
                "INSERT INTO user_settings(chat_id,base,quote,risk,digests_enabled,model,"
                "news_interval_min,last_news_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (s.chat_id, s.base, s.quote, s.risk, int(s.digests_enabled), s.model,
                 s.news_interval_min, s.last_news_at, s.updated_at),
            )
            conn.commit()
            return s
        keys = row.keys()
        return UserSettings(
            chat_id=row["chat_id"],
            base=row["base"],
            quote=row["quote"],
            risk=row["risk"],
            digests_enabled=bool(row["digests_enabled"]),
            model=row["model"] if "model" in keys else "",
            news_interval_min=row["news_interval_min"] if "news_interval_min" in keys else CONFIG.default_news_interval_min,
            last_news_at=row["last_news_at"] if "last_news_at" in keys else "",
            updated_at=row["updated_at"],
        )


def save_settings(s: UserSettings) -> None:
    s.updated_at = utcnow_iso()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO user_settings(chat_id,base,quote,risk,digests_enabled,model,"
            "news_interval_min,last_news_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(chat_id) DO UPDATE SET"
            " base=excluded.base, quote=excluded.quote, risk=excluded.risk,"
            " digests_enabled=excluded.digests_enabled, model=excluded.model,"
            " news_interval_min=excluded.news_interval_min, last_news_at=excluded.last_news_at,"
            " updated_at=excluded.updated_at",
            (s.chat_id, s.base, s.quote, s.risk, int(s.digests_enabled), s.model,
             s.news_interval_min, s.last_news_at, s.updated_at),
        )
        conn.commit()


def chats_with_digests() -> list[int]:
    with _lock:
        conn = _connect()
        return [r["chat_id"] for r in conn.execute(
            "SELECT chat_id FROM user_settings WHERE digests_enabled=1"
        )]


def has_settings(chat_id: int) -> bool:
    with _lock:
        conn = _connect()
        return conn.execute(
            "SELECT 1 FROM user_settings WHERE chat_id=?", (chat_id,)
        ).fetchone() is not None


def set_last_news(chat_id: int) -> None:
    with _lock:
        conn = _connect()
        conn.execute("UPDATE user_settings SET last_news_at=? WHERE chat_id=?",
                     (utcnow_iso(), chat_id))
        conn.commit()


def chat_news_seen(chat_id: int, news_hash: str) -> bool:
    with _lock:
        conn = _connect()
        return conn.execute(
            "SELECT 1 FROM chat_seen_news WHERE chat_id=? AND hash=?",
            (chat_id, news_hash),
        ).fetchone() is not None


def mark_chat_news_seen(chat_id: int, news_hash: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR IGNORE INTO chat_seen_news(chat_id, hash, first_seen) VALUES(?,?,?)",
            (chat_id, news_hash, utcnow_iso()),
        )
        conn.commit()


# ── trades ────────────────────────────────────────────────────────────────────
def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        chat_id=row["chat_id"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry=row["entry"],
        stop=row["stop"],
        take_profit=row["take_profit"],
        size_note=row["size_note"] or "",
        status=row["status"],
        opened_at=row["opened_at"],
        last_alert_at=row["last_alert_at"] or "",
    )


def add_trade(t: Trade) -> int:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO trades(chat_id,symbol,direction,entry,stop,take_profit,size_note,status,opened_at,last_alert_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (t.chat_id, t.symbol, t.direction, t.entry, t.stop, t.take_profit,
             t.size_note, t.status, t.opened_at, t.last_alert_at),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_trades(chat_id: int, status: str = "open") -> list[Trade]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM trades WHERE chat_id=? AND status=? ORDER BY id",
            (chat_id, status),
        ).fetchall()
        return [_row_to_trade(r) for r in rows]


def all_open_trades() -> list[Trade]:
    """Across every chat — used by the monitoring job."""
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT * FROM trades WHERE status='open' ORDER BY id").fetchall()
        return [_row_to_trade(r) for r in rows]


def get_trade(trade_id: int) -> Optional[Trade]:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return _row_to_trade(row) if row else None


def set_trade_status(trade_id: int, status: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute("UPDATE trades SET status=? WHERE id=?", (status, trade_id))
        conn.commit()


def touch_trade_alert(trade_id: int) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE trades SET last_alert_at=? WHERE id=?", (utcnow_iso(), trade_id)
        )
        conn.commit()


# ── news dedup ──────────────────────────────────────────────────────────────
def news_seen(news_hash: str) -> bool:
    with _lock:
        conn = _connect()
        return conn.execute(
            "SELECT 1 FROM seen_news WHERE hash=?", (news_hash,)
        ).fetchone() is not None


def mark_news_seen(news_hash: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR IGNORE INTO seen_news(hash, first_seen) VALUES(?,?)",
            (news_hash, utcnow_iso()),
        )
        conn.commit()


# ── admins (runtime-managed by the owner) ────────────────────────────────────────
def add_admin(user_id: int, added_by: int = 0, label: str = "") -> bool:
    """Grant a user access. Returns True if newly added, False if already an admin."""
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT OR IGNORE INTO admins(user_id, label, added_by, added_at)"
            " VALUES(?,?,?,?)",
            (user_id, label, added_by, utcnow_iso()),
        )
        if cur.rowcount and label:
            conn.execute("UPDATE admins SET label=? WHERE user_id=?", (label, user_id))
        conn.commit()
        return bool(cur.rowcount)


def remove_admin(user_id: int) -> bool:
    """Revoke a user's access. Returns True if a row was removed."""
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        conn.commit()
        return bool(cur.rowcount)


def list_admins() -> list[tuple[int, str]]:
    """All admins as (user_id, label), oldest first."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT user_id, label FROM admins ORDER BY added_at"
        ).fetchall()
        return [(r["user_id"], r["label"] or "") for r in rows]


def is_admin(user_id: int) -> bool:
    with _lock:
        conn = _connect()
        return conn.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone() is not None


# ── key/value app config (runtime overrides, e.g. the channel id) ────────────────
def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT value FROM app_config WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO app_config(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


# ── signals history ────────────────────────────────────────────────────────────
def save_signal(chat_id: int, signal_dict: dict) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO signals(chat_id,symbol,timeframe,direction,payload,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (
                chat_id,
                signal_dict.get("symbol", ""),
                signal_dict.get("timeframe", ""),
                signal_dict.get("direction", ""),
                json.dumps(signal_dict),
                utcnow_iso(),
            ),
        )
        conn.commit()
