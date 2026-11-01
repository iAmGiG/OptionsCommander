"""
Download pre-market 5-minute bars for the #574 universe (#623), read-only.

The morning routine raises stops at 09:20 ET from the broker's price then. Daily bars have no such
price, so this fetches Alpaca's extended-hours 5-minute bars (market data API, SIP feed, which the
paper account can read for data older than 15 minutes; IEX has no pre-market). Raw (unadjusted)
prices are stored; the runner back-adjusts them with the same split calendar as the daily series.
Only bars that start between 04:00 and 09:20 ET are kept (the 09:15 bar is the last that ends at or
before 09:20).

Resumable: one parquet file per (symbol, calendar quarter) in the output folder; existing files
are skipped. A request is made about every 0.35 s (limit about 200 a minute). Nothing is ordered,
no trading client is built, and credentials are read through ConfigLoader and never printed.

Usage:
    python -s scripts/research/fetch_premarket_bars.py --out <folder> [--symbols AAPL ...]
"""

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIRST_DAY = dt.date(2016, 1, 1)
LAST_DAY = dt.date(2026, 10, 9)  # the gex vendor break; the study stops here
ET = "America/New_York"
PAUSE_SECONDS = 0.35


def watchlist_symbols() -> list:
    with open(REPO_ROOT / "config_defaults/scanner_config.yaml") as f:
        cfg = yaml.safe_load(f)
    groups = cfg["default_watchlist"].values()
    return [s for group in groups if isinstance(group, list) for s in group if isinstance(s, str)]


def quarters(first: dt.date, last: dt.date):
    """(label, start, end_exclusive) calendar quarters covering first..last."""
    year, q = first.year, (first.month - 1) // 3
    while dt.date(year, 3 * q + 1, 1) <= last:
        start = dt.date(year, 3 * q + 1, 1)
        ny, nq = (year + 1, 0) if q == 3 else (year, q + 1)
        end = dt.date(ny, 3 * nq + 1, 1)
        yield f"{year}Q{q + 1}", max(start, first), min(end, last + dt.timedelta(days=1))
        year, q = ny, nq


def make_client():
    from alpaca.data import StockHistoricalDataClient

    from src.utils.config_loader import ConfigLoader

    cfg = ConfigLoader()
    return StockHistoricalDataClient(
        cfg.get("ALPACA_PAPER_API_KEY"), cfg.get("ALPACA_PAPER_SECRET")
    )


def fetch_quarter(client, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    utc = dt.timezone.utc
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=dt.datetime(start.year, start.month, start.day, tzinfo=utc) - dt.timedelta(hours=6),
        end=dt.datetime(end.year, end.month, end.day, tzinfo=utc),
        feed=DataFeed.SIP,
        adjustment=Adjustment.RAW,
    )
    df = client.get_stock_bars(request).df
    if len(df) == 0:
        return pd.DataFrame(columns=["et", "open", "high", "low", "close", "volume", "trade_count"])
    df = df.droplevel(0)
    et = df.index.tz_convert(ET)
    minutes = et.hour * 60 + et.minute
    keep = (minutes >= 4 * 60) & (minutes < 9 * 60 + 20)
    df = df[keep].copy()
    df.insert(0, "et", et[keep].tz_localize(None))
    cols = ["et", "open", "high", "low", "close", "volume", "trade_count"]
    return df.reset_index(drop=True)[cols]


def main():
    parser = argparse.ArgumentParser(description="Pre-market 5-minute bars (#623)")
    parser.add_argument("--out", required=True, help="folder for the per-quarter parquet files")
    parser.add_argument("--symbols", nargs="+", help="default: the scanner watchlist")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = make_client()
    symbols = args.symbols or watchlist_symbols()
    jobs = [(s, q) for s in symbols for q in quarters(FIRST_DAY, LAST_DAY)]
    done = 0
    for symbol, (label, start, end) in jobs:
        path = out / f"{symbol}_{label}.parquet"
        if path.exists():
            done += 1
            continue
        for attempt in range(5):
            try:
                frame = fetch_quarter(client, symbol, start, end)
                break
            except Exception as exc:  # network or rate limit: back off, retry
                print(f"  {symbol} {label} attempt {attempt + 1}: {type(exc).__name__}", flush=True)
                time.sleep(5 * (attempt + 1))
        else:
            print(f"  {symbol} {label}: gave up, will be retried on the next run", flush=True)
            continue
        frame.to_parquet(path)
        done += 1
        print(f"  {symbol} {label}: {len(frame)} bars  ({done}/{len(jobs)})", flush=True)
        time.sleep(PAUSE_SECONDS)
    print(f"finished: {done}/{len(jobs)} files in {out}")


if __name__ == "__main__":
    main()
