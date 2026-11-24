"""
Download hourly bars for the WK-means replication (#673), read-only.

The paper (Horvath, Issa and Muguruza, arXiv:2110.11848) clusters hourly SPY log returns from
2005 to 2020. The gex database has no SPY bars before 2026, so these come from Alpaca's market
data API (SIP feed, raw prices), whose history starts in 2016: 2016-01-01 to 2020-12-31 by
default. One gzipped CSV per (symbol, year), named with the dates it covers
(SPY_20160101_20170101.csv.gz: start, end exclusive), so a part-year file is never taken for the
whole year; existing files are skipped, and an empty answer is not saved, so it is retried.
Bars are stored as Alpaca returns them, extended hours included, with the bar start in ET; the
runner keeps the regular session. Nothing is ordered, no trading client is built, and
credentials are read through ConfigLoader (fetch_premarket_bars.make_client) and never printed.
The bars stay in the folder given; they are not committed.

Usage:
    python -s scripts/research/fetch_hourly_bars.py --out <folder> [--symbols SPY]
        [--first 2016-01-01] [--last 2020-12-31]
"""

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # fetch_premarket_bars sits beside this

ET = "America/New_York"
FIRST_DAY = dt.date(2016, 1, 1)
LAST_DAY = dt.date(2020, 12, 31)
PAUSE_SECONDS = 0.35
COLUMNS = ["et", "open", "high", "low", "close", "volume", "trade_count", "vwap"]


def years(first: dt.date, last: dt.date):
    """(year, start, end_exclusive) for each calendar year in first..last."""
    for year in range(first.year, last.year + 1):
        start = max(dt.date(year, 1, 1), first)
        end = min(dt.date(year + 1, 1, 1), last + dt.timedelta(days=1))
        yield year, start, end


def file_name(symbol: str, start: dt.date, end: dt.date) -> str:
    """The cache file for one request: the dates it covers are in the name."""
    return f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}.csv.gz"


def fetch_year(client, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Hourly bars from start to end (exclusive), the bar start as a naive ET timestamp."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    utc = dt.timezone.utc
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(1, TimeFrameUnit.Hour),
        start=dt.datetime(start.year, start.month, start.day, tzinfo=utc),
        end=dt.datetime(end.year, end.month, end.day, tzinfo=utc),
        feed=DataFeed.SIP,
        adjustment=Adjustment.RAW,
    )
    df = client.get_stock_bars(request).df
    if len(df) == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = df.droplevel(0)
    df.insert(0, "et", df.index.tz_convert(ET).tz_localize(None))
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = float("nan")
    return df.reset_index(drop=True)[COLUMNS]


def main():
    parser = argparse.ArgumentParser(description="Hourly bars for the WK-means replication")
    parser.add_argument("--out", required=True, help="folder for the per-year CSV files")
    parser.add_argument("--symbols", nargs="+", default=["SPY"])
    parser.add_argument("--first", type=dt.date.fromisoformat, default=FIRST_DAY)
    parser.add_argument("--last", type=dt.date.fromisoformat, default=LAST_DAY)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from fetch_premarket_bars import make_client

    client = make_client()
    jobs = [(s, y) for s in args.symbols for y in years(args.first, args.last)]
    for done, (symbol, (year, start, end)) in enumerate(jobs, start=1):
        path = out / file_name(symbol, start, end)
        if path.exists():
            continue
        for attempt in range(5):
            try:
                frame = fetch_year(client, symbol, start, end)
                break
            except Exception as exc:  # network or rate limit: back off, retry
                print(f"  {symbol} {year} attempt {attempt + 1}: {type(exc).__name__}", flush=True)
                time.sleep(5 * (attempt + 1))
        else:
            print(f"  {symbol} {year}: gave up, will be retried on the next run", flush=True)
            continue
        if frame.empty:  # not saved, so the next run asks again
            print(f"  {symbol} {year}: no bars returned, not saved", flush=True)
            continue
        frame.to_csv(path, index=False)  # CSV: no pyarrow needed (not a dependency)
        print(f"  {symbol} {year}: {len(frame)} bars  ({done}/{len(jobs)})", flush=True)
        time.sleep(PAUSE_SECONDS)
    print(f"finished: {len(list(out.glob('*.csv.gz')))} files in {out}")


if __name__ == "__main__":
    main()
