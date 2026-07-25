"""Self-contained Dukascopy historical-data pipeline for multi-regime backtesting.

Dukascopy serves free tick history for XAU/USD back to 2003 as hourly ``.bi5`` files
(LZMA-compressed big-endian tick records). This module downloads a date range, parses
the ticks, and resamples to OHLC candles (BID side) + the average spread per bar — so
``mt5_backtest.py`` can walk-forward the SAME engine over years of DEEP, MULTI-REGIME
history instead of the ~6 weeks the MetaQuotes demo server holds (research 2026-07-03).

Deps: stdlib only (requests + lzma + struct) + pandas. No external dukascopy library.

.bi5 tick record (20 bytes, big-endian '>IIIff'):
  ms_since_hour(u32), ask_points(u32), bid_points(u32), ask_vol(f32), bid_vol(f32)
Prices are integers in "points"; divide by POINT_FACTOR (XAUUSD = 1000, 3 decimals).

CLI:
  python -m data.dukascopy XAUUSD 2026-04-01 2026-07-01 5min storage/duka_xau_2026chop_m5.csv
"""

from __future__ import annotations

import lzma
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from config import STORAGE_DIR

BASE = "https://datafeed.dukascopy.com/datafeed"
POINT_FACTOR = {"XAUUSD": 1000.0, "EURUSD": 100000.0, "BTCUSD": 1000.0}
_HDRS = {"User-Agent": "Mozilla/5.0 (research backtest data pull)"}
_REC = struct.Struct(">IIIff")


def _decompress(raw: bytes) -> bytes:
    if not raw:
        return b""
    for fmt in (lzma.FORMAT_AUTO, lzma.FORMAT_ALONE):
        try:
            return lzma.LZMADecompressor(format=fmt).decompress(raw)
        except Exception:  # noqa: BLE001
            continue
    return b""


def _fetch_hour(instrument: str, dt: datetime, retries: int = 3) -> list[tuple]:
    """One hour of ticks: [(epoch_ms, bid, ask), ...]. Empty on a market-closed hour."""
    url = (f"{BASE}/{instrument}/{dt.year:04d}/{dt.month - 1:02d}/{dt.day:02d}/"
           f"{dt.hour:02d}h_ticks.bi5")           # NOTE: month is 0-indexed at Dukascopy
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_HDRS, timeout=30)
            if r.status_code == 404 or not r.content:
                return []                          # weekend / holiday / no data
            r.raise_for_status()
            data = _decompress(r.content)
            factor = POINT_FACTOR.get(instrument, 100000.0)
            base_ms = int(dt.replace(minute=0, second=0, microsecond=0,
                                     tzinfo=timezone.utc).timestamp() * 1000)
            out = []
            for i in range(0, len(data) - len(data) % 20, 20):
                ms, ask, bid, _av, _bv = _REC.unpack_from(data, i)
                out.append((base_ms + ms, bid / factor, ask / factor))
            return out
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    return []


def download_ticks(instrument: str, start: datetime, end: datetime,
                   progress: bool = True) -> pd.DataFrame:
    """All ticks in [start, end) as a DataFrame(index=UTC ts, bid, ask). Skips
    weekends up front to save requests."""
    rows: list[tuple] = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    total_h = max(1, int((end - cur).total_seconds() // 3600))
    done = 0
    while cur < end:
        if cur.weekday() < 5 or (cur.weekday() == 6 and cur.hour >= 21) or \
           (cur.weekday() == 4 and cur.hour < 21):        # skip the Sat + most of Sun/Fri close
            rows.extend(_fetch_hour(instrument, cur))
        done += 1
        if progress and done % 200 == 0:
            print(f"  … {done}/{total_h} hours, {len(rows):,} ticks", flush=True)
        cur += timedelta(hours=1)
    if not rows:
        return pd.DataFrame(columns=["bid", "ask"])
    df = pd.DataFrame(rows, columns=["ms", "bid", "ask"])
    df["ts"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    return df.set_index("ts").drop(columns="ms").sort_index()


def resample(ticks: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Ticks → OHLC candles (BID) + mean spread per bar. tf: 1min/5min/15min/1h/1day."""
    rule = {"1min": "1min", "5min": "5min", "15min": "15min",
            "30min": "30min", "1h": "1h", "1day": "1D"}[tf]
    o = ticks["bid"].resample(rule)
    bars = pd.DataFrame({
        "open": o.first(), "high": o.max(), "low": o.min(), "close": o.last(),
        "volume": ticks["bid"].resample(rule).count(),
        "spread": (ticks["ask"] - ticks["bid"]).resample(rule).mean(),
    }).dropna(subset=["open"])
    return bars


def _month_starts(s: datetime, e: datetime):
    cur = s.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur < e:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(cur, s), min(nxt, e)
        cur = nxt


def build(instrument: str, start: str, end: str, tf: str, out: str) -> str:
    """Download [start,end) MONTH BY MONTH (bounded memory), resample each chunk to `tf`,
    concatenate the small bar frames, and write one CSV. Handles multi-year spans safely."""
    s = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    e = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    print(f"Dukascopy {instrument} {start}->{end} {tf} (monthly chunks)...", flush=True)
    chunks = []
    for ms, me in _month_starts(s, e):
        ticks = download_ticks(instrument, ms, me, progress=False)
        if not ticks.empty:
            chunks.append(resample(ticks, tf))
            print(f"  {ms:%Y-%m}: {len(ticks):,} ticks -> {len(chunks[-1]):,} {tf} bars", flush=True)
        del ticks
    if not chunks:
        print("  NO TICKS returned (check dates / instrument).", flush=True)
        return ""
    bars = pd.concat(chunks).sort_index()
    bars = bars[~bars.index.duplicated(keep="last")]
    outp = Path(out) if Path(out).is_absolute() else (STORAGE_DIR / out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(outp)
    print(f"  DONE {len(bars):,} {tf} bars -> {outp}", flush=True)
    print(f"    price {bars['low'].min():.2f}-{bars['high'].max():.2f} | "
          f"mean spread {bars['spread'].mean():.4f}", flush=True)
    return str(outp)


if __name__ == "__main__":
    a = sys.argv
    if len(a) < 6:
        print("usage: python -m data.dukascopy INSTRUMENT START END TF OUT.csv")
        raise SystemExit(2)
    build(a[1], a[2], a[3], a[4], a[5])
