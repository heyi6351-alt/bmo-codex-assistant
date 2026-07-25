"""Print the strategy-competition results from the saved state.

Run this anytime (e.g. tomorrow morning) to see which strategy is winning:

    python report.py

It reads storage/compete_state.json (written live by compete.py) and prints the
full leaderboard + per-account stats. Raw per-trade rows live in
storage/compete_trades.csv.
"""

from __future__ import annotations

from compete import CSV_PATH, full_report, load_state, restore_accounts


def main() -> None:
    data = load_state()
    if not data:
        print("No competition state found yet.\n"
              "Start it with:  python compete.py\n"
              "Quick preview:  python compete.py --selftest")
        return
    accounts = restore_accounts(data)
    print(full_report(accounts, data.get("last_price", 0.0), data.get("started_at", "")))
    print(f"\nPer-trade log: {CSV_PATH}")
    print(f"Last updated : {data.get('updated_at', '?')} UTC")


if __name__ == "__main__":
    main()
