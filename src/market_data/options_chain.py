"""
Read-only options chains from the gex database (#572).

A chain is end-of-day: one snapshot per symbol and trading day, per strike and expiry, with
Greeks, open interest and volume. Strikes and prices are raw, as traded on that day, so they
aren't split-adjusted. Every query is a SELECT in a read-only session.

Each result says where its data came from, as the source changes the numbers:

- The vendor changed on 2026-10-12, from Alpha Vantage to Cboe delayed quotes, with a level
  break in GEX. Don't compare or join across it without saying so. ``after_vendor_break``
  flags it, and ``data_source`` is on every contract.
- ``greeks_source`` says whose Greeks a contract has. Index roots have no vendor Greeks, so
  theirs are computed. ``iv_status`` says why an IV is missing.
- ``warnings`` lists anything else to know: a day the collection log marks as incomplete, or
  a ticker that meant a different company then. The log starts in July 2026, so days before
  that have no such check.

This is end-of-day data, about 15 minutes delayed at capture, so it isn't for live trading.
"""

import importlib.util
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

import pandas as pd

logger = logging.getLogger(__name__)

DateLike = Union[date, str]

VENDOR_BREAK_DATE = date(2026, 10, 12)
VENDOR_BREAK_NOTE = (
    "From 2026-10-12 chains are Cboe delayed quotes (Alpha Vantage before), with a level break "
    "in GEX: don't compare across that date without saying so."
)

# gex migration 047: what the database's codes mean
GREEKS_SOURCES = {
    None: "vendor_legacy",
    0: "vendor",
    1: "bs_spot",
    2: "black76_forward",
    3: "vendor_cboe",
}
IV_STATUSES = {
    None: None,
    0: "ok",
    1: "no_converge",
    2: "no_quote",
    3: "below_intrinsic",
    4: "zero_vega",
    5: "unidentifiable",
    6: "expired",
}

# Tickers whose early options data is a different company (gex ADVISOR_ACCESS.md)
_EARLIER_COMPANY = {
    "SNDK": (date(2025, 2, 24), "the original SanDisk, acquired in 2016"),
    "SNOW": (date(2020, 9, 16), "Intrawest Resorts, not Snowflake"),
}

_SOURCE_NOTES = {
    "cboe_delayed_nextday": "Some contracts were taken the next morning, for what the close missed.",
    "cboe_delayed_final_rush": "Some contracts came from a separate rescue fetch after the close.",
}

_COLUMNS = (
    "expiration",
    "strike",
    "option_type",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "implied_volatility",
    "underlying_price",
    "days_to_expiration",
    "data_source",
    "greeks_source",
    "iv_status",
)
_CHAIN_SQL = (
    f"SELECT {', '.join(_COLUMNS)} FROM gex.options_chains_partitioned "
    "WHERE symbol = %s AND trading_date = %s"
)
_ORDER_SQL = " ORDER BY expiration, strike, option_type"
_LATEST_SQL = "SELECT MAX(trading_date) FROM gex.options_chains_partitioned WHERE symbol = %s"
_EXPIRATIONS_SQL = (
    "SELECT DISTINCT expiration FROM gex.options_chains_partitioned "
    "WHERE symbol = %s AND trading_date = %s ORDER BY expiration"
)

_STATUS_ROW = re.compile(r"^\|\s*([A-Z]+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|(.*)\|\s*$")


@dataclass(frozen=True)
class OptionsChain:
    """One symbol's chain on one trading day, and what to know about where it came from."""

    symbol: str
    trading_date: Optional[date]  # None if the symbol has no chains at all
    contracts: pd.DataFrame  # one row per contract; see _COLUMNS, with the codes as labels
    after_vendor_break: bool
    warnings: Tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return self.contracts.empty


def _as_date(value: DateLike) -> date:
    """A plain date: a datetime or pandas Timestamp can't be compared with one."""
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(value)


def _labels(codes: pd.Series, names: dict) -> List[Optional[str]]:
    """Database codes as names; a code that isn't known stays visible as unknown."""
    labels = []
    for code in codes:
        code = None if pd.isna(code) else int(code)
        labels.append(names.get(code, f"unknown_{code}"))
    return labels


def _connect():
    """A read-only connection to the gex database."""
    import psycopg2

    from src.backtesting.research_backtester import DB_PARAMS

    conn = psycopg2.connect(**DB_PARAMS)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _query(conn: Any, sql: str, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
    """Run a query on the given connection, or on a read-only one opened for it."""
    own = conn is None
    conn = _connect() if own else conn
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        if own:
            conn.close()


def latest_trading_date(symbol: str, conn: Any = None) -> Optional[date]:
    """The symbol's most recent stored chain date, or None if it has none."""
    rows = _query(conn, _LATEST_SQL, (symbol.upper(),))
    return rows[0][0] if rows else None


def list_expirations(
    symbol: str, trading_date: Optional[DateLike] = None, conn: Any = None
) -> List[date]:
    """The expiries in a symbol's chain on a day (default: its latest), soonest first."""
    symbol = symbol.upper()
    day = _as_date(trading_date) if trading_date is not None else latest_trading_date(symbol, conn)
    if day is None:
        return []
    return [row[0] for row in _query(conn, _EXPIRATIONS_SQL, (symbol, day))]


def _status_log_path() -> Path:
    """The gex collection log: beside the installed gex package's src/, else beside this repo."""
    spec = importlib.util.find_spec("gex_db_infrastructure")
    if spec and spec.origin:
        beside_package = Path(spec.origin).parents[2] / "DAILY_STATUS.md"
        if beside_package.exists():
            return beside_package
    return Path(__file__).resolve().parents[3] / "gex-database-infrastructure/DAILY_STATUS.md"


def _collection_warning(day: date, status_log: Path) -> Optional[str]:
    """What the gex collection log says about a day, if it wasn't a clean one."""
    try:
        lines = status_log.read_text().splitlines()
    except OSError as e:
        logger.warning("Could not read the gex collection log: %s", e)
        return f"Collection status not checked: {status_log.name} could not be read."
    for line in lines:
        row = _STATUS_ROW.match(line)
        if row and row.group(2) == day.isoformat() and row.group(1) != "OK":
            notes = row.group(3).split("|")[-1].strip()
            return f"Collection log marks {day} as {row.group(1)}" + (f": {notes}" if notes else "")
    return None


def _warnings(symbol: str, day: date, contracts: pd.DataFrame, status_log: Path) -> Tuple[str, ...]:
    found = []
    if day >= VENDOR_BREAK_DATE:
        found.append(VENDOR_BREAK_NOTE)
    earlier = _EARLIER_COMPANY.get(symbol)
    if earlier and day < earlier[0]:
        found.append(f"{symbol} before {earlier[0]} is {earlier[1]}: a different company.")
    collection = _collection_warning(day, status_log)
    if collection:
        found.append(collection)
    if not contracts.empty:
        sources = set(contracts["data_source"])
        found.extend(note for source, note in _SOURCE_NOTES.items() if source in sources)
    return tuple(found)


def get_options_chain(
    symbol: str,
    trading_date: Optional[DateLike] = None,
    expiration: Optional[DateLike] = None,
    conn: Any = None,
    status_log: Optional[Path] = None,
) -> OptionsChain:
    """
    A symbol's stored chain, read-only.

    Args:
        symbol: Underlying symbol, such as SPY or SPX
        trading_date: The snapshot's day; default: the symbol's latest
        expiration: Only this expiry; default: all of them
        conn: An open connection to reuse (it's the caller's to make read-only and close)
        status_log: The gex collection log, read for the day's status; default: found
            through the installed gex package

    Returns:
        The chain. A day with no data gives an empty chain, not an error, and its warnings
        still say what's known about that day.
    """
    symbol = symbol.upper()
    day = _as_date(trading_date) if trading_date is not None else latest_trading_date(symbol, conn)
    if day is None:
        return OptionsChain(symbol, None, pd.DataFrame(columns=_COLUMNS), False, ())

    sql, params = _CHAIN_SQL, (symbol, day)
    if expiration is not None:
        sql, params = sql + " AND expiration = %s", (symbol, day, _as_date(expiration))
    contracts = pd.DataFrame(_query(conn, sql + _ORDER_SQL, params), columns=_COLUMNS)
    contracts["greeks_source"] = _labels(contracts["greeks_source"], GREEKS_SOURCES)
    contracts["iv_status"] = _labels(contracts["iv_status"], IV_STATUSES)
    return OptionsChain(
        symbol,
        day,
        contracts,
        day >= VENDOR_BREAK_DATE,
        _warnings(symbol, day, contracts, status_log or _status_log_path()),
    )
