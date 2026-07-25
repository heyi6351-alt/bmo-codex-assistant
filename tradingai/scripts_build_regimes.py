"""Download 4 multi-regime XAUUSD M5 windows from Dukascopy for walk-forward validation.
Each window is a distinct market regime the current 6-week single-regime backtest never
saw (research 2026-07-03). Run in the background; each writes one CSV to storage/.
"""
from data.dukascopy import build

REGIMES = [
    ("range2021", "2021-06-01", "2021-08-01"),   # low-vol RANGE year
    ("bear2022",  "2022-05-01", "2022-07-01"),   # rate-hike GRIND DOWN
    ("bull2025",  "2025-02-01", "2025-04-01"),   # SUPER-BULL leg
    ("chop2026",  "2026-05-01", "2026-07-01"),   # current CORRECTION/chop
]

if __name__ == "__main__":
    for name, s, e in REGIMES:
        out = f"duka_{name}_m5.csv"
        print(f"\n===== {name.upper()} {s}..{e} =====", flush=True)
        try:
            build("XAUUSD", s, e, "5min", out)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name} FAILED: {exc}", flush=True)
    print("\nALL REGIME DOWNLOADS DONE.", flush=True)
