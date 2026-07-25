"""Launch the MT5 trader for MULTIPLE symbols at once, each in its own process.

Each symbol runs an isolated `mt5_bot.py` (own state file, own magic block, all
features — guardian/breakeven/TP-ladder/Kimi/autonomous-AI/weekend handling). If one
crashes it is auto-restarted without touching the others (fault isolation). Adding a
market later = add its symbol here (it must exist in instruments.py).

    python run_bots.py                       # gold + EURUSD
    python run_bots.py XAUUSD EURUSD BTCUSD  # explicit list

Stop everything with Ctrl+C.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Windows console/redirect is cp1252 — force UTF-8 so the ▶/■/⚠ status glyphs don't crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent
LOGDIR = ROOT / "storage"

# Symbols come from the BOT_SYMBOLS env / .env line (comma-separated) so a market can be
# enabled/disabled WITHOUT code changes — e.g. BOT_SYMBOLS=XAUUSD for gold-only focus,
# BOT_SYMBOLS=XAUUSD,EURUSD to bring EURUSD back. CLI args still override everything.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001 — dotenv optional; plain env still works
    pass
DEFAULT_SYMBOLS = [s.strip().upper() for s in
                   os.getenv("BOT_SYMBOLS", "XAUUSD,EURUSD").split(",") if s.strip()]
RESTART_BACKOFF = 15          # seconds before restarting a crashed process
MIN_UPTIME_FOR_RESET = 60     # a process that ran this long resets its backoff


def _spawn(symbol: str) -> subprocess.Popen:
    env = dict(os.environ, BOT_SYMBOL=symbol, PYTHONIOENCODING="utf-8")
    out = open(LOGDIR / f"bot_{symbol}.out.log", "a", buffering=1, encoding="utf-8")
    err = open(LOGDIR / f"bot_{symbol}.err.log", "a", buffering=1, encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(ROOT / "mt5_bot.py")],
                         cwd=str(ROOT), env=env, stdout=out, stderr=err)
    p._logfiles = (out, err)  # type: ignore  # keep handles alive
    print(f"  ▶ {symbol}: started (pid {p.pid}) → storage/bot_{symbol}.err.log")
    return p


def main() -> None:
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    LOGDIR.mkdir(parents=True, exist_ok=True)
    print(f"Launching MT5 traders for: {', '.join(symbols)}")
    procs: dict[str, subprocess.Popen] = {}
    started_at: dict[str, float] = {}
    for s in symbols:
        procs[s] = _spawn(s)
        started_at[s] = time.time()

    stopping = {"flag": False}

    def _stop(*_a):
        stopping["flag"] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print("All processes up. Ctrl+C to stop everything.\n")
    try:
        while not stopping["flag"]:
            for s in list(procs):
                p = procs[s]
                if p.poll() is not None:                     # it exited — restart it
                    ran = time.time() - started_at[s]
                    print(f"  ⚠ {s}: exited (code {p.returncode}) after {ran:.0f}s — restarting in {RESTART_BACKOFF}s")
                    time.sleep(RESTART_BACKOFF)
                    if stopping["flag"]:
                        break
                    procs[s] = _spawn(s)
                    started_at[s] = time.time()
            time.sleep(3)
    finally:
        print("\nStopping all traders…")
        for s, p in procs.items():
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        for s, p in procs.items():
            try:
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                p.kill()
            print(f"  ■ {s}: stopped")


if __name__ == "__main__":
    main()
