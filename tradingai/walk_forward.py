"""WALK-FORWARD validation — separates a REAL edge from a lucky backtest (research: >90% of
strategies that look good in ONE backtest fail live). Splits each Dukascopy regime into N
sequential OUT-OF-SAMPLE windows, replays the LIVE engine (mt5_backtest → generate_signal +
real gate + cost) on each, per strategy, and reports the DISTRIBUTION of OOS profit factors:

  ROBUST   = wins in most windows across regimes  -> trust it, size up
  FRAGILE  = coin-flip window to window           -> regime/luck, keep small
  NEGATIVE = loses across windows                 -> don't trade it

Runs one SINGLE-strategy subprocess per (regime, window, strategy) — the fast, proven path.
Usage:  python walk_forward.py [n_windows]   (default 3; STRATS/WF_COST env override)
"""
from __future__ import annotations

import collections
import glob
import os
import re
import statistics
import subprocess
import sys

NWIN = int(sys.argv[1]) if len(sys.argv) > 1 else 3
COST = os.getenv("WF_COST", "0.20")
STRATS = os.getenv("WF_STRATS", "secondentry,pullback,squeeze,range_fade,trend").split(",")
PY = sys.executable
ROOT = os.path.dirname(os.path.abspath(__file__))
REGIMES = [f for f in sorted(glob.glob(os.path.join(ROOT, "storage", "duka_*_m5.csv")))
           if "test_week" not in f]
LINE = re.compile(r"trades\s+(\d+)\s+\|\s+win\s+\d+%\s+\|\s+avgR\s+\S+\s+\|\s+PF\s+(inf|[\d.]+)\s+\|\s+totalR\s+([+-][\d.]+)")

res: dict[str, list[dict]] = collections.defaultdict(list)

def run(csv: str, idx: int, strat: str) -> tuple[int, float, float] | None:
    env = dict(os.environ, BACKTEST_CSV=csv, BACKTEST_COST_PX=COST,
               BT_N_WINDOWS=str(NWIN), BT_WINDOW_IDX=str(idx), PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([PY, os.path.join(ROOT, "mt5_backtest.py"), "--strategy", strat,
                            "--tf", "5min", "--no-session", "--risk", "moderate"],
                           capture_output=True, text=True, env=env, cwd=ROOT, timeout=400)
    except Exception:  # noqa: BLE001
        return None
    for line in p.stdout.splitlines():
        m = LINE.search(line)
        if m:
            return int(m.group(1)), (99.0 if m.group(2) == "inf" else float(m.group(2))), float(m.group(3))
    return None

def main() -> None:
    print(f"WALK-FORWARD | {len(REGIMES)} regimes x {NWIN} OOS windows | {len(STRATS)} strategies "
          f"| cost {COST}px", flush=True)
    for csv in REGIMES:
        regime = os.path.basename(csv).replace("duka_", "").replace("_m5.csv", "")
        for idx in range(NWIN):
            row = []
            for strat in STRATS:
                r = run(csv, idx, strat)
                if r:
                    res[strat].append({"regime": regime, "win": idx, "trades": r[0], "pf": r[1], "R": r[2]})
                    row.append(f"{strat[:4]}:{r[1]:.2f}")
            print(f"  {regime:10s} w{idx+1}/{NWIN}: {'  '.join(row)}", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("PER-STRATEGY OUT-OF-SAMPLE VERDICT (windows with >=4 trades)", flush=True)
    print("=" * 70, flush=True)
    print(f"{'strategy':13s} {'win':>3s} {'%PF>=1':>7s} {'medPF':>6s} {'sumR':>7s}  verdict", flush=True)
    for strat in STRATS:
        ws = [w for w in res.get(strat, []) if w["trades"] >= 4]
        if not ws:
            print(f"{strat:13s}  (too few trades per window to judge)", flush=True); continue
        pfs = [w["pf"] for w in ws]
        pct = sum(1 for p in pfs if p >= 1.0) / len(ws) * 100
        med = statistics.median(pfs)
        sumR = sum(w["R"] for w in res[strat])
        verdict = ("ROBUST  -> trust/size up" if pct >= 60 and med >= 1.10 else
                   "FRAGILE -> regime/luck, keep small" if pct >= 45 and med >= 1.0 else
                   "NEGATIVE -> don't trade")
        print(f"{strat:13s} {len(ws):>3d} {pct:>6.0f}% {med:>6.2f} {sumR:>+7.1f}  {verdict}", flush=True)
    print("\nDONE", flush=True)

if __name__ == "__main__":
    main()
