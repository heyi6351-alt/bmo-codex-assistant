"""Pull 60k XAUUSD M5 bars (~10 months) from the running MT5 terminal, read-only, save to CSV.
Non-disruptive: bare attach, copy_rates only, no login/trade/shutdown."""
import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print("ATTACH FAIL:", mt5.last_error()); raise SystemExit(1)
r = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 60000)
df = pd.DataFrame(r)
df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
out = df[["ts", "open", "high", "low", "close", "tick_volume"]].rename(columns={"tick_volume": "volume"})
out.to_csv("storage/mt5_xau_hist_m5.csv", index=False)
print(f"saved {len(out)} M5 bars | {out['ts'].iloc[0]} -> {out['ts'].iloc[-1]}")
print(f"price {out['low'].min():.2f}-{out['high'].max():.2f}")
