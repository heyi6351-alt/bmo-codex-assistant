"""SAFE read-only probe of MT5 history for XAUUSD. Bare attach to the running terminal,
copy rates (read-only, same as the bot's get_candles), report how much history is available.
Does NOT log in, does NOT trade, does NOT shutdown the terminal. Zero footprint on the live bot."""
import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():                       # bare attach to the ALREADY-RUNNING terminal
    print("ATTACH FAIL:", mt5.last_error()); raise SystemExit(1)
ti = mt5.terminal_info()
print("attached:", bool(ti), "connected:", getattr(ti, "connected", None))
for n in (5000, 60000, 300000):
    r = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, n)
    if r is None:
        print(f"  request {n}: None ({mt5.last_error()})"); continue
    df = pd.DataFrame(r)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    print(f"  request {n}: got {len(df)} M5 bars | {df['ts'].iloc[0]} -> {df['ts'].iloc[-1]}")
# NOTE: intentionally NOT calling mt5.shutdown() — process exit releases only OUR handle.
