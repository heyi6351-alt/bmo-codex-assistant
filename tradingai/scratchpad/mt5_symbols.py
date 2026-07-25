"""Read-only discovery: which candidate instruments does the broker offer, at what spread,
and how deep is their M5 history? Bare-attach, no trading. Guides the diversification basket."""
import MetaTrader5 as mt5

if not mt5.initialize():
    print("ATTACH FAIL:", mt5.last_error()); raise SystemExit(1)

CANDIDATES = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
              "EURJPY", "GBPJPY", "EURGBP", "XAGUSD",
              "US500", "SP500", "USTEC", "NAS100", "US30", "DJ30", "DE40", "GER40",
              "UK100", "JP225", "USOIL", "UKOIL", "WTI",
              "BTCUSD", "ETHUSD"]

all_syms = {s.name for s in (mt5.symbols_get() or [])}
print(f"broker exposes {len(all_syms)} symbols total\n")
print(f"{'symbol':10} {'exists':>6} {'spread(pt)':>10} {'point':>10} {'digits':>6} {'contract':>10} {'M5 bars':>9} {'from':>12}")
for c in CANDIDATES:
    if c not in all_syms:
        print(f"{c:10} {'no':>6}"); continue
    mt5.symbol_select(c, True)
    info = mt5.symbol_info(c)
    r = mt5.copy_rates_from_pos(c, mt5.TIMEFRAME_M5, 0, 200)   # tiny existence check (fast)
    n = 0 if r is None else len(r)
    frm = ""
    if n:
        import pandas as pd
        frm = str(pd.to_datetime(r[0][0], unit="s"))[:10]
    sp = getattr(info, "spread", 0); pt = getattr(info, "point", 0)
    cs = getattr(info, "trade_contract_size", 0); dg = getattr(info, "digits", 0)
    print(f"{c:10} {'YES':>6} {sp:>10} {pt:>10.5f} {dg:>6} {cs:>10.1f} {n:>9} {frm:>12}")
