"""Strategy paper-trading COMPETITION (forward test).

Runs N virtual $10k accounts side-by-side on LIVE XAU/USD candles, each using a
different (strategy x risk-profile) combo from the existing signal engine. Every
account opens/manages/closes its own paper positions with realistic laddered TPs,
breakeven-after-TP1, and round-trip spread+slippage cost — then we rank them by
return so you can see which strategy actually performs.

Each strategy trades on the timeframe it was designed for:
    secondentry -> M5   ·   pullback -> M15   ·   trend -> H1

Same engine powers two modes:
  * `python compete.py`            -> LIVE forward test (trades only future candles)
  * `python compete.py --selftest` -> instant BACKTEST preview over recent history

State persists to storage/compete_state.json (survives restarts) and every closed
trade is appended to storage/compete_trades.csv. Hourly leaderboards + a final
report are posted to the dedicated results channel.

NOT financial advice — this is a research/measurement harness on paper money.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Optional

import requests

from config import CONFIG, STORAGE_DIR
from core.models import Candle
from data.market import MARKET
from risk.profiles import PROFILES
from analysis.signals import generate_signal

# Windows consoles default to cp1252 and choke on ·/≈/emoji — force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | compete | %(message)s",
)
log = logging.getLogger("compete")

# ── competition configuration ────────────────────────────────────────────────
SYMBOL = CONFIG.default_symbol            # e.g. "XAU/USD"
# Each strategy trades on the timeframe it was designed for.
STRATEGY_INTERVAL = {"secondentry": "5min", "pullback": "15min", "trend": "1h"}
INTERVAL_SECONDS = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800,
                    "1h": 3600, "4h": 14400, "1day": 86400}
TICK_SECONDS = 300                         # base poll cadence
START_BALANCE = 10_000.0
OUTPUTSIZE = 320                           # candles fetched per request
WIN = 280                                  # indicator window fed to generate_signal
WARMUP = 210                               # need >=200 bars before EMA200 is valid
COMPETE_CHANNEL = os.getenv("RESULTS_CHANNEL_ID", "") or os.getenv("TELEGRAM_CHANNEL_ID", "") or "-1003998490583"  # "Results of bot"
HOURLY_POST_SECONDS = 1980  # 33-minute leaderboard cadence

STRATEGIES = ("secondentry", "pullback", "trend")
RISKS = ("conservative", "moderate", "aggressive")

STATE_PATH = STORAGE_DIR / "compete_state.json"
CSV_PATH = STORAGE_DIR / "compete_trades.csv"
CONFIG_VERSION = 3  # bump to invalidate an incompatible saved state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _round_cost(price: float) -> float:
    """Round-trip spread+slippage in price units (gold ~$0.6 on a $4000 quote)."""
    return max(0.3, price * 0.00015)


# ── data models ──────────────────────────────────────────────────────────────
@dataclass
class Leg:
    price: float
    units: float
    filled: bool = False


@dataclass
class Position:
    direction: str            # "long" | "short"
    entry: float
    stop: float               # mutable (moves to breakeven after TP1)
    init_stop: float
    risk_per_unit: float
    risk_amount: float        # $ risked at entry == R denominator
    units_total: float
    units_open: float
    cost_price: float
    legs: list                # list[Leg]
    opened_dt: str
    realized_pnl: float = 0.0
    be_moved: bool = False

    def unrealized(self, price: float) -> float:
        if self.units_open <= 0 or price <= 0:
            return 0.0
        gross = ((price - self.entry) if self.direction == "long"
                 else (self.entry - price)) * self.units_open
        return gross - self.units_open * self.cost_price


@dataclass
class Account:
    name: str
    mode: str
    risk: str
    interval: str
    balance: float = START_BALANCE
    last_price: float = 0.0
    peak_equity: float = START_BALANCE
    max_dd_pct: float = 0.0
    n_trades: int = 0
    n_wins: int = 0
    gross_profit: float = 0.0     # sum of winning trade $ (positive)
    gross_loss: float = 0.0       # sum of losing trade $ (positive)
    total_r: float = 0.0
    best_pnl: float = 0.0
    worst_pnl: float = 0.0
    n_signals: int = 0            # how many times the strategy fired
    position: Optional[Position] = None

    # ── derived metrics ──
    def equity(self) -> float:
        unreal = self.position.unrealized(self.last_price) if self.position else 0.0
        return self.balance + unreal

    @property
    def return_pct(self) -> float:
        return (self.balance - START_BALANCE) / START_BALANCE * 100.0

    @property
    def win_rate(self) -> float:
        return (self.n_wins / self.n_trades * 100.0) if self.n_trades else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss <= 0:
            return float("inf") if self.gross_profit > 0 else 0.0
        return self.gross_profit / self.gross_loss

    @property
    def avg_r(self) -> float:
        return (self.total_r / self.n_trades) if self.n_trades else 0.0


def build_accounts() -> list[Account]:
    """9 accounts = 3 strategies x 3 risk profiles, each on its native timeframe."""
    accts = []
    for mode in STRATEGIES:
        for risk in RISKS:
            accts.append(Account(name=f"{mode}-{risk[:4]}", mode=mode, risk=risk,
                                 interval=STRATEGY_INTERVAL[mode]))
    return accts


# ── position engine ──────────────────────────────────────────────────────────
def _close_units(acct: Account, pos: Position, units: float, price: float) -> None:
    """Book P&L for `units` exiting at `price` (charges round-trip cost)."""
    gross = ((price - pos.entry) if pos.direction == "long"
             else (pos.entry - price)) * units
    net = gross - units * pos.cost_price
    pos.realized_pnl += net
    acct.balance += net
    pos.units_open -= units


def _finalize(acct: Account, pos: Position, candle: Candle, reason: str) -> None:
    """Record a fully-closed position into the account's stats + the trade CSV."""
    net = pos.realized_pnl
    r = net / pos.risk_amount if pos.risk_amount else 0.0
    acct.n_trades += 1
    if net > 0:
        acct.n_wins += 1
        acct.gross_profit += net
    else:
        acct.gross_loss += -net
    acct.total_r += r
    acct.best_pnl = max(acct.best_pnl, net)
    acct.worst_pnl = min(acct.worst_pnl, net)
    _append_trade_csv(acct, pos, candle, net, r, reason)
    acct.position = None


def _on_candle(acct: Account, candle: Candle) -> None:
    """Resolve an open position against a freshly-closed candle.

    Conservative ordering: if both the stop and a TP fall inside the same candle,
    the stop is assumed to trigger first.
    """
    pos = acct.position
    if pos is None:
        return
    hi, lo, d = candle.high, candle.low, pos.direction

    stop_hit = (d == "long" and lo <= pos.stop) or (d == "short" and hi >= pos.stop)
    if stop_hit:
        _close_units(acct, pos, pos.units_open, pos.stop)
        _finalize(acct, pos, candle, "be-stop" if pos.be_moved else "stop")
        return

    for leg in pos.legs:
        if leg.filled:
            continue
        reached = (d == "long" and hi >= leg.price) or (d == "short" and lo <= leg.price)
        if reached:
            _close_units(acct, pos, leg.units, leg.price)
            leg.filled = True
            if not pos.be_moved:                 # lock risk after the first target
                pos.stop = pos.entry
                pos.be_moved = True

    if all(leg.filled for leg in pos.legs):
        _finalize(acct, pos, candle, "all-tp")


def _maybe_enter(acct: Account, candles: list[Candle], i: int) -> None:
    """If flat, ask this account's strategy for a signal and open a paper trade."""
    window = candles[max(0, i - WIN + 1): i + 1]
    profile = replace(PROFILES[acct.risk], strategy_mode=acct.mode)
    sig = generate_signal(SYMBOL, window, profile, acct.interval)
    if sig.direction not in ("long", "short") or not sig.take_profits or not sig.stop_loss:
        return

    entry, stop = sig.entry, sig.stop_loss
    rpu = abs(entry - stop)
    if rpu <= 0:
        return
    acct.n_signals += 1
    risk_amount = acct.balance * profile.risk_pct / 100.0
    units_total = risk_amount / rpu

    spct = sum(tp.close_pct for tp in sig.take_profits) or 100.0
    legs, allocated = [], 0.0
    for k, tp in enumerate(sig.take_profits):
        u = (units_total - allocated) if k == len(sig.take_profits) - 1 \
            else units_total * (tp.close_pct / spct)
        allocated += u
        legs.append(Leg(price=tp.price, units=u))

    acct.position = Position(
        direction=sig.direction, entry=entry, stop=stop, init_stop=stop,
        risk_per_unit=rpu, risk_amount=risk_amount, units_total=units_total,
        units_open=units_total, cost_price=_round_cost(entry), legs=legs,
        opened_dt=candles[i].dt,
    )


def _mark_equity(acct: Account, price: float) -> None:
    acct.last_price = price
    eq = acct.equity()
    acct.peak_equity = max(acct.peak_equity, eq)
    if acct.peak_equity > 0:
        dd = (acct.peak_equity - eq) / acct.peak_equity * 100.0
        acct.max_dd_pct = max(acct.max_dd_pct, dd)


# ── engine driver (multi-timeframe) ──────────────────────────────────────────
class Stream:
    """A per-interval candle buffer with an independent processing cursor."""
    def __init__(self) -> None:
        self.candles: list[Candle] = []
        self.seen: set[str] = set()
        self.cursor = 0


class Engine:
    def __init__(self, accounts: list[Account]) -> None:
        self.accounts = accounts
        self.by_interval: dict[str, list[Account]] = {}
        for a in accounts:
            self.by_interval.setdefault(a.interval, []).append(a)
        self.intervals = list(self.by_interval.keys())
        self.streams = {iv: Stream() for iv in self.intervals}

    def seed(self, iv: str, candles: list[Candle], trade_history: bool) -> None:
        s = self.streams[iv]
        s.candles = list(candles)
        s.seen = {c.dt for c in candles}
        s.cursor = WARMUP if trade_history else len(candles)

    def add_closed(self, iv: str, candles: list[Candle]) -> int:
        s = self.streams[iv]
        added = 0
        for c in candles:
            if c.dt not in s.seen:
                s.candles.append(c)
                s.seen.add(c.dt)
                added += 1
        if len(s.candles) > 6000:
            drop = len(s.candles) - 5000
            s.candles = s.candles[drop:]
            s.cursor = max(0, s.cursor - drop)
        return added

    def step(self, iv: str) -> int:
        s = self.streams[iv]
        accts = self.by_interval[iv]
        processed = 0
        n = len(s.candles)
        while s.cursor < n:
            i = s.cursor
            c = s.candles[i]
            for a in accts:
                if a.position is not None:
                    _on_candle(a, c)
                elif i >= WARMUP:
                    _maybe_enter(a, s.candles, i)
                _mark_equity(a, c.close)
            s.cursor += 1
            processed += 1
        return processed

    def last_price(self, iv: str) -> float:
        s = self.streams[iv]
        return s.candles[-1].close if s.candles else 0.0

    @property
    def overall_price(self) -> float:
        return max((self.last_price(iv) for iv in self.intervals), default=0.0)


# ── persistence ──────────────────────────────────────────────────────────────
def _append_trade_csv(acct: Account, pos: Position, candle: Candle,
                      net: float, r: float, reason: str) -> None:
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", encoding="utf-8") as f:
        if new:
            f.write("closed_at,account,mode,risk,interval,direction,entry,units,"
                    "pnl_usd,R,reason,balance_after\n")
        f.write(f"{_now_iso()},{acct.name},{acct.mode},{acct.risk},{acct.interval},"
                f"{pos.direction},{pos.entry:.2f},{pos.units_total:.4f},{net:.2f},"
                f"{r:.3f},{reason},{acct.balance:.2f}\n")


def save_state(engine: Engine, started_at: str) -> None:
    data = {
        "version": CONFIG_VERSION,
        "symbol": SYMBOL,
        "start_balance": START_BALANCE,
        "started_at": started_at,
        "updated_at": _now_iso(),
        "overall_price": engine.overall_price,
        "accounts": [asdict(a) for a in engine.accounts],
    }
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def load_state() -> Optional[dict]:
    if not STATE_PATH.exists():
        return None
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("Could not read saved state: %s", e)
        return None
    if data.get("version") != CONFIG_VERSION:
        log.info("Saved state is from an older config version — starting fresh.")
        return None
    return data


def restore_accounts(data: dict) -> list[Account]:
    accts = []
    for ad in data.get("accounts", []):
        ad = dict(ad)
        pos = None
        if ad.get("position"):
            pd = dict(ad["position"])
            pd["legs"] = [Leg(**lg) for lg in pd.get("legs", [])]
            pos = Position(**pd)
        ad.pop("position", None)
        a = Account(**ad)
        a.position = pos
        accts.append(a)
    return accts


# ── reporting / formatting ───────────────────────────────────────────────────
def _fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def leaderboard_rows(accounts: list[Account]) -> list[Account]:
    return sorted(accounts, key=lambda a: a.equity(), reverse=True)


def console_table(accounts: list[Account]) -> str:
    rows = leaderboard_rows(accounts)
    out = [f"{'#':>2} {'ACCOUNT':<20} {'TF':>4} {'EQUITY':>10} {'RET%':>7} "
           f"{'TR':>3} {'SIG':>4} {'WIN%':>6} {'PF':>5} {'avgR':>6} {'DD%':>6} {'OPN':>4}"]
    out.append("-" * 92)
    tf = {"5min": "M5", "15min": "M15", "1h": "H1"}
    for i, a in enumerate(rows, 1):
        openp = a.position.direction[:1].upper() if a.position else "-"
        out.append(f"{i:>2} {a.name:<20} {tf.get(a.interval, a.interval):>4} "
                   f"{a.equity():>10.2f} {a.return_pct:>+7.2f} {a.n_trades:>3} "
                   f"{a.n_signals:>4} {a.win_rate:>5.0f}% {_fmt_pf(a.profit_factor):>5} "
                   f"{a.avg_r:>+6.2f} {a.max_dd_pct:>5.1f}% {openp:>4}")
    return "\n".join(out)


def telegram_leaderboard(accounts: list[Account], price: float,
                         started_at: str, title: str) -> str:
    rows = leaderboard_rows(accounts)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = [f"<b>{title}</b>",
             f"XAU/USD · {len(accounts)} paper accounts · ${int(START_BALANCE):,} each",
             f"price <code>{price:.2f}</code> · since {started_at[:16].replace('T', ' ')} UTC",
             "<pre>"]
    lines.append(f"{'#':<2}{'ACCOUNT':<18}{'RET%':>7}{'TR':>4}{'WIN':>5}{'PF':>6}")
    for i, a in enumerate(rows, 1):
        tag = medals.get(i, f"{i} ")
        lines.append(f"{tag:<2}{a.name:<18}{a.return_pct:>+6.2f}%{a.n_trades:>4}"
                     f"{a.win_rate:>4.0f}%{_fmt_pf(a.profit_factor):>6}")
    lines.append("</pre>")
    lead = rows[0]
    total = sum(a.n_trades for a in accounts)
    lines.append(f"Leader: <b>{lead.name}</b> "
                 f"({lead.return_pct:+.2f}%, {lead.n_trades} trades, {lead.win_rate:.0f}% win)")
    if total < 10:
        lines.append("⚠️ Small sample — a first read, not a verdict.")
    lines.append("<i>Paper money — not financial advice.</i>")
    return "\n".join(lines)


def full_report(accounts: list[Account], price: float, started_at: str) -> str:
    rows = leaderboard_rows(accounts)
    lines = ["=" * 92,
             f"STRATEGY COMPETITION REPORT  ·  XAU/USD  ·  started {started_at} UTC",
             f"price now {price:.2f}  ·  generated {_now_iso()} UTC",
             "=" * 92,
             console_table(accounts), ""]
    total_trades = sum(a.n_trades for a in accounts)
    lines.append(f"Total closed trades across all accounts: {total_trades}")
    if rows:
        w = rows[0]
        lines.append(f"Best so far: {w.name}  "
                     f"({w.return_pct:+.2f}% · {w.n_trades} trades · "
                     f"{w.win_rate:.0f}% win · PF {_fmt_pf(w.profit_factor)} · "
                     f"avgR {w.avg_r:+.2f} · maxDD {w.max_dd_pct:.1f}%)")
    if total_trades < 10:
        lines.append("!  Small sample — treat the ranking as a first read, not a verdict.")
    lines.append("=" * 92)
    return "\n".join(lines)


# ── telegram ─────────────────────────────────────────────────────────────────
def tg_send(text: str, channel: str = COMPETE_CHANNEL) -> bool:
    tok = CONFIG.telegram_bot_token
    if not tok or not channel:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": channel, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        j = r.json()
        if not j.get("ok"):
            log.warning("Telegram post failed: %s", j.get("description"))
        return bool(j.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram post error: %s", e)
        return False


# ── modes ────────────────────────────────────────────────────────────────────
def _finalize_open_to_market(engine: Engine) -> None:
    """Close any still-open positions at the last price (for a fair final equity)."""
    for iv in engine.intervals:
        s = engine.streams[iv]
        if not s.candles:
            continue
        for a in engine.by_interval[iv]:
            if a.position:
                _close_units(a, a.position, a.position.units_open, s.candles[-1].close)
                _finalize(a, a.position, s.candles[-1], "eod-mark")


def run_selftest() -> None:
    """Instant backtest preview: replay recent history through all accounts."""
    engine = Engine(build_accounts())
    for iv in engine.intervals:
        log.info("Fetching %d %s candles for %s …", OUTPUTSIZE, iv, SYMBOL)
        candles = MARKET.get_candles(SYMBOL, iv, outputsize=OUTPUTSIZE)
        if len(candles) < WARMUP + 10:
            log.warning("Only %d %s candles — accounts on %s may not trade.",
                        len(candles), iv, iv)
        engine.seed(iv, candles, trade_history=True)
        engine.step(iv)
    _finalize_open_to_market(engine)
    print("\n" + full_report(engine.accounts, engine.overall_price, "backtest-preview"))
    print(f"\n(Backtest preview over the last ~{OUTPUTSIZE} bars per timeframe. "
          f"Live mode trades only NEW candles going forward.)")


def run_live() -> None:
    started_at = _now_iso()
    saved = load_state()
    if saved:
        accounts = restore_accounts(saved)
        started_at = saved.get("started_at", started_at)
        log.info("Resumed competition from %s (%d accounts).",
                 saved.get("updated_at"), len(accounts))
    else:
        accounts = build_accounts()
        log.info("Starting a fresh competition: %d accounts.", len(accounts))

    engine = Engine(accounts)
    last_fetch: dict[str, float] = {}
    for iv in engine.intervals:
        log.info("Seeding %s history (%d candles) …", iv, OUTPUTSIZE)
        candles = MARKET.get_candles(SYMBOL, iv, outputsize=OUTPUTSIZE)
        engine.seed(iv, candles[:-1] if candles else [], trade_history=False)
        last_fetch[iv] = time.time()
    if all(not engine.streams[iv].candles for iv in engine.intervals):
        log.error("No market data — aborting. Check TWELVEDATA_API_KEY / network.")
        return

    intro = (f"🏁 <b>Strategy competition started</b>\n"
             f"{len(accounts)} paper accounts · XAU/USD · ${int(START_BALANCE):,} each.\n"
             f"3 strategies (Second-Entry M5, Pullback M15, Trend H1) × 3 risk profiles.\n"
             f"Leaderboard hourly + a final report when stopped.\n"
             f"<i>Paper money — not financial advice.</i>")
    tg_send(intro)
    log.info("Posted intro to results channel. Base poll %ds. Ctrl+C to stop.", TICK_SECONDS)

    last_hourly = time.time()
    try:
        while True:
            changed = False
            for iv in engine.intervals:
                # Only re-fetch an interval when a new candle could have closed.
                if time.time() - last_fetch[iv] < INTERVAL_SECONDS[iv] - 30:
                    continue
                try:
                    fresh = MARKET.get_candles(SYMBOL, iv, outputsize=OUTPUTSIZE)
                    last_fetch[iv] = time.time()
                    closed = fresh[:-1] if fresh else []
                    if engine.add_closed(iv, closed):
                        engine.step(iv)
                        changed = True
                except Exception as e:  # noqa: BLE001
                    log.warning("Fetch/step error on %s (continuing): %s", iv, e)

            if changed:
                save_state(engine, started_at)
                log.info("Leaderboard:\n%s", console_table(engine.accounts))
            else:
                log.info("No new candles this tick (market may be slow/closed).")

            if time.time() - last_hourly >= HOURLY_POST_SECONDS:
                tg_send(telegram_leaderboard(engine.accounts, engine.overall_price,
                                             started_at, "⏱ Hourly leaderboard"))
                last_hourly = time.time()

            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        log.info("Stopping — saving + posting final report.")
        save_state(engine, started_at)
        tg_send(telegram_leaderboard(engine.accounts, engine.overall_price,
                                     started_at, "🏆 FINAL leaderboard"))
        print("\n" + full_report(engine.accounts, engine.overall_price, started_at))


def main() -> None:
    ap = argparse.ArgumentParser(description="Strategy paper-trading competition.")
    ap.add_argument("--selftest", action="store_true",
                    help="Backtest preview over recent history (no live loop, no posting).")
    ap.add_argument("--report", action="store_true",
                    help="Print the current saved-state report and exit.")
    args = ap.parse_args()

    if args.selftest:
        run_selftest()
    elif args.report:
        data = load_state()
        if not data:
            print("No saved competition state yet. Run `python compete.py` first.")
            return
        accts = restore_accounts(data)
        print(full_report(accts, data.get("overall_price", 0.0), data.get("started_at", "")))
    else:
        run_live()


if __name__ == "__main__":
    main()
