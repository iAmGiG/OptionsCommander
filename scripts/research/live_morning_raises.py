"""
A description of the live morning raises (#623, part 2), read-only.

For each morning routine in the paper account's records: the 09:20 price (position_daily), the
previous evening's price, the stop in force before and after the routine (from state/scheduler.log),
and the day's regular-session open, low and close (Alpaca daily bars, market data only). The stop
fills come from the fills table. The state database is opened with mode=ro and nothing is written
there or anywhere under the live checkout. The sample is a few weeks and two positions: it is a
description, not a test.

Usage:
    python -s scripts/research/live_morning_raises.py --live /path/to/autotrader/checkout
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

RESULTS_DIR = REPO_ROOT / "docs/08_research/03_strategy_research/results"
ADJ = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2},\d+ .*?state_reconciler - INFO - "
    r"(\w+): Stop adjustment - \$([\d.]+) → \$([\d.]+)"
)
ACCEPTED = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2},\d+ .*?trading_cycle - INFO - .*?(\w+): Stop adjusted to \$([\d.]+)"
)


def read_positions(db: Path) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(
            "select market_date, routine, symbol, quantity, avg_entry_price, current_price "
            "from position_daily where market_date >= '2026-10-15' order by market_date",
            con,
        )
    finally:
        con.close()


def read_fills(db: Path) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(
            "select symbol, side, order_type, filled_qty, filled_avg_price, filled_at "
            "from fills where market_date >= '2026-10-15' order by filled_at",
            con,
        )
    finally:
        con.close()


def read_raises(log: Path) -> pd.DataFrame:
    """Accepted stop raises of the log, with the routine from the UTC time (13:20 morning)."""
    adjustments, accepted = [], set()
    with open(log, errors="replace") as f:
        for line in f:
            m = ADJ.match(line)
            if m:
                adjustments.append(m.groups())
            m = ACCEPTED.match(line)
            if m:
                accepted.add((m.group(1), m.group(2), m.group(3), float(m.group(4))))
    rows = []
    for date, hhmm, symbol, old, new in adjustments:
        if symbol not in ("QQQ", "SPY"):
            continue
        rows.append(
            {
                "date": date,
                "utc": hhmm,
                "routine": "morning" if hhmm < "16:00" else "evening",
                "symbol": symbol,
                "from": float(old),
                "to": float(new),
                "accepted": (date, hhmm, symbol, float(new)) in accepted,
            }
        )
    return pd.DataFrame(rows)


def daily_bars(symbols, first, last) -> pd.DataFrame:
    import datetime as dt

    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from fetch_premarket_bars import make_client

    utc = dt.timezone.utc
    d0 = dt.datetime.fromisoformat(first).replace(tzinfo=utc)
    d1 = dt.datetime.fromisoformat(last).replace(tzinfo=utc)
    req = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame.Day,
        start=d0,
        end=d1,
        feed=DataFeed.SIP,
        adjustment=Adjustment.RAW,
    )
    df = make_client().get_stock_bars(req).df.reset_index()
    df["date"] = df["timestamp"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    return df.set_index(["symbol", "date"])[["open", "high", "low", "close"]]


def main():
    parser = argparse.ArgumentParser(description="Live morning raises, read-only (#623)")
    parser.add_argument("--live", required=True, help="the live checkout (state/, reports/)")
    args = parser.parse_args()
    live = Path(args.live)
    pos = read_positions(live / "state/user.db")
    fills = read_fills(live / "state/user.db")
    raises = read_raises(live / "state/scheduler.log")
    bars = daily_bars(("QQQ", "SPY"), "2026-10-14", "2026-11-01")

    import datetime as dt

    from fetch_premarket_bars import fetch_quarter, make_client

    client = make_client()
    sip = {}  # the study's 09:20 price (last pre-market 5-minute bar close) per symbol and day
    for symbol in ("QQQ", "SPY"):
        b = fetch_quarter(client, symbol, dt.date(2026, 10, 14), dt.date(2026, 10, 31))
        b["date"] = b["et"].dt.strftime("%Y-%m-%d")
        for day, g in b.groupby("date"):
            sip[(symbol, day)] = float(g.iloc[-1]["close"])

    rows = []
    mornings = pos[pos["routine"] == "morning"]
    evenings = pos[pos["routine"] == "evening"].set_index(["market_date", "symbol"])[
        "current_price"
    ]
    dates = sorted(pos["market_date"].unique())
    for _, m in mornings.iterrows():
        d, s = m["market_date"], m["symbol"]
        prev = [x for x in dates if x < d]
        prev_close_seen = evenings.get((prev[-1], s)) if prev else None
        r_today = raises[(raises["date"] == d) & (raises["symbol"] == s)]
        r_m = r_today[r_today["routine"] == "morning"]
        b = bars.loc[(s, d)] if (s, d) in bars.index else None
        rows.append(
            {
                "date": d,
                "symbol": s,
                "price_0920": m["current_price"],
                "previous_evening_price": prev_close_seen,
                "study_price_0920": sip.get((s, d)),
                "gap_pct": (
                    None
                    if prev_close_seen is None
                    else round((m["current_price"] / prev_close_seen - 1) * 100, 3)
                ),
                "morning_raise_from": None if r_m.empty else float(r_m.iloc[0]["from"]),
                "morning_raise_to": None if r_m.empty else float(r_m.iloc[0]["to"]),
                "morning_raise_accepted": None if r_m.empty else bool(r_m.iloc[0]["accepted"]),
                "open": None if b is None else float(b["open"]),
                "low": None if b is None else float(b["low"]),
                "close": None if b is None else float(b["close"]),
            }
        )
    out = {
        "morning_runs": rows,
        "stop_raises": raises.to_dict(orient="records"),
        "stop_fills": fills[fills["order_type"] == "stop"].to_dict(orient="records"),
        "note": "regular-session bars are Alpaca SIP daily bars (raw); read-only",
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / "live_morning_raises.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
        f.write("\n")
    print(pd.DataFrame(rows).to_string())
    print(raises.to_string())
    print(fills[fills["order_type"] == "stop"].to_string())


if __name__ == "__main__":
    main()
