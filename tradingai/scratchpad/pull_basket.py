"""Read-only deep-pull of a diversification basket from MT5 (bare-attach, no trade). For each
candidate that exists with data: pull 60k M5, bad-tick filter, save storage/inst_<SYM>_m5.csv,
record real cost (spread*point). Writes storage/inst_params.json + a flushed progress log."""
import json
import numpy as np, pandas as pd
import MetaTrader5 as mt5

LOG = open("storage/inst_pull_summary.txt", "w", encoding="utf-8")
def log(m): print(m); LOG.write(m + "\n"); LOG.flush()

log("STARTING pull_basket…")
if not mt5.initialize():
    log("ATTACH FAIL: " + str(mt5.last_error())); raise SystemExit(1)
log("attached OK")

# FX majors FIRST (reliably cached, pull fast) — indices/crypto later (may need weekend download)
CANDIDATES = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
              "XAGUSD", "EURJPY", "GBPJPY", "BTCUSD", "US500", "USTEC", "DE40"]
params = {}

# XAUUSD benchmark: reuse the already-pulled fresh file
params["XAUUSD"] = {"cost": 0.20, "min_stop": 0.60, "min_tp1": 0.60,
                    "sig": "XAU/USD", "csv": "storage/mt5_xau_hist_m5.csv"}

for sym in CANDIDATES:
  try:
    if not mt5.symbol_select(sym, True):
        log(f"{sym:9} not available"); continue
    info = mt5.symbol_info(sym)
    if info is None:
        log(f"{sym:9} no info"); continue
    point = info.point or 0.0
    cost = (info.spread or 0) * point                      # round-trip proxy = 1 spread
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 20000)
    n = 0 if r is None else len(r)
    if n < 4000:
        log(f"{sym:9} only {n} bars — skip"); continue
    df = pd.DataFrame(r)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[["ts", "open", "high", "low", "close", "tick_volume"]].rename(columns={"tick_volume": "volume"})
    # bad-tick filter
    c = df["close"].to_numpy(float)
    rng = (df["high"].to_numpy(float) - df["low"].to_numpy(float)) / np.maximum(c, 1e-9)
    jump = np.abs(np.concatenate([[0.0], np.diff(c)]) / np.maximum(c, 1e-9))
    df = df[(rng < 0.05) & (jump < 0.05)].reset_index(drop=True)
    csv = f"storage/inst_{sym}_m5.csv"; df.to_csv(csv, index=False)
    if cost <= 0:                                           # some feeds report 0 spread; floor it
        cost = 2 * point if point else 0.0001
    sig = f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 and sym.isalpha() else sym
    params[sym] = {"cost": round(cost, 6), "min_stop": round(3 * cost, 6),
                   "min_tp1": round(3 * cost, 6), "sig": sig, "csv": csv}
    json.dump(params, open("storage/inst_params.json", "w"), indent=2)   # incremental save
    log(f"{sym:9} OK  {len(df):>6} bars  spread {info.spread} pts  cost {cost:.6f}  "
        f"{str(df['ts'].iloc[0])[:10]}..{str(df['ts'].iloc[-1])[:10]}")
  except Exception as e:
    log(f"{sym:9} ERROR {type(e).__name__}: {e}")

json.dump(params, open("storage/inst_params.json", "w"), indent=2)
log(f"\nDONE — {len(params)} instruments ready: {list(params)}")
