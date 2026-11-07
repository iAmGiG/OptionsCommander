"""
The market-data cache uses the ET date and never keeps a daily bar that is still forming
(#620). At 21:00 ET the host's UTC date is already tomorrow, so a request ending today used
to count as history and get the cached mid-session bar back. No API is called.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.cache import sqlite_cache
from src.cache.sqlite_cache import TradingCacheManager
from src.market_data import unified_market_tool as tool
from src.utils import market_hours

ET = ZoneInfo("America/New_York")


@pytest.fixture
def at(monkeypatch):
    """Set the market clock (an ET wall time); the cache's own clock stays real."""
    monkeypatch.setattr(market_hours, "_get_alpaca_calendar", lambda: None)  # NYSE rules
    monkeypatch.setattr(market_hours, "_CALENDAR_CACHE", {})

    def set_time(*args):
        now = datetime(*args, tzinfo=ET)
        monkeypatch.setattr(market_hours, "get_datetime_now", lambda tz=None: now.astimezone(tz))
        return now

    return set_time


# market_today and the forming day


def test_market_today_is_the_et_date_in_the_evening(at):
    at(2026, 11, 6, 21, 0)  # 02:00 UTC on 11-07

    assert market_hours.market_today() == "2026-11-06"


@pytest.mark.parametrize(
    "when, forming",
    [
        ((2026, 11, 6, 10, 0), "2026-11-06"),  # in the session
        ((2026, 11, 6, 8, 0), "2026-11-06"),  # before the open: nothing finished yet
        ((2026, 11, 6, 16, 0), None),  # at the close
        ((2026, 11, 6, 21, 0), None),
        ((2026, 11, 7, 12, 0), None),  # Saturday
        ((2026, 11, 26, 12, 0), None),  # Thanksgiving
        ((2026, 11, 27, 13, 30), None),  # after the 13:00 early close
    ],
)
def test_the_forming_day(at, when, forming):
    at(*when)

    assert market_hours.unfinished_session_day() == forming


# The unified tool's cache skip


@pytest.fixture
def sources(monkeypatch):
    cache = MagicMock()
    cache.get_market_data.return_value = None
    monkeypatch.setattr(tool, "cache_adapter", cache)
    monkeypatch.setattr(tool, "_fetch_from_alpaca", MagicMock(return_value=pd.DataFrame()))
    return cache


def test_a_request_ending_today_skips_the_cache_in_the_evening(at, sources):
    at(2026, 11, 6, 21, 0)  # the host's date is 11-07

    tool.fetch_unified_market_data("SPY", "2026-10-01", "2026-11-06")

    sources.get_market_data.assert_not_called()


def test_a_request_ending_yesterday_uses_the_cache(at, sources):
    at(2026, 11, 6, 21, 0)

    tool.fetch_unified_market_data("SPY", "2026-10-01", "2026-11-05")

    sources.get_market_data.assert_called_once()


# What the cache keeps


def bars(*days, source=None):
    frame = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100},
        index=pd.DatetimeIndex([pd.Timestamp(day, tz="UTC") + timedelta(hours=5) for day in days]),
    )
    if source:
        frame["source"] = source
    return frame


@pytest.fixture
def cache(tmp_path):
    return TradingCacheManager(db_path=str(tmp_path / "cache.db"))


def cached_days(cache):
    rows = cache.get("SPY", "2026-11-01", "2026-11-30", source="alpaca")
    return [] if rows is None else sorted(pd.to_datetime(rows.index).strftime("%Y-%m-%d"))


def test_todays_bar_isnt_cached_while_the_session_is_open(at, cache):
    at(2026, 11, 6, 11, 0)

    cache.set("SPY", bars("2026-11-05", "2026-11-06"), source="alpaca")

    assert cached_days(cache) == ["2026-11-05"]


def test_todays_bar_is_cached_after_the_close(at, cache):
    at(2026, 11, 6, 16, 30)

    cache.set("SPY", bars("2026-11-05", "2026-11-06"), source="alpaca")

    assert cached_days(cache) == ["2026-11-05", "2026-11-06"]


def test_a_synthetic_last_trade_bar_is_never_cached(at, cache):
    at(2026, 11, 6, 16, 30)

    cache.set("SPY", bars("2026-11-06", source="alpaca_live"), source="alpaca")

    assert cached_days(cache) == []


def test_intraday_bars_are_not_filtered(at, cache):
    at(2026, 11, 6, 11, 0)
    frame = bars("2026-11-06")

    cache.set("SPY", frame, source="alpaca", timeframe="1Hour")

    rows = cache.get("SPY", "2026-11-06", "2026-11-07", source="alpaca", timeframe="1Hour")
    assert rows is not None and len(rows) == 1


def test_expiry_counts_days_by_the_et_date(at, cache, monkeypatch):
    """Two ET days back is recent (24 hours), even when the host's date is a day ahead."""
    now = at(2026, 11, 6, 21, 0)
    # the host runs on UTC, so its own clock reads 11-07 in the evening
    monkeypatch.setattr(sqlite_cache, "get_datetime_now", lambda tz=None: now.astimezone(UTC))

    expires = cache._calculate_expiration("2026-11-04", "2026-11-04")

    assert expires - now == timedelta(hours=24)
