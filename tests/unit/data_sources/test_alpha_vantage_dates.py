"""
Alpha Vantage daily bars are keyed by exchange date only ("2026-11-12"). Localising that
midnight as UTC and converting to ET turned it into the evening before, so every AV bar was
cached under the previous day (E7-01). The bar must keep its date in ET, across DST changes.
The request is faked: nothing leaves the machine.
"""

from unittest.mock import MagicMock

import pytest

from src.cache.sqlite_cache import TradingCacheManager, _dated_rows
from src.market_data import alpha_vantage_market as module
from src.market_data.alpha_vantage_market import AlphaVantageMarketTool

ET = "America/New_York"

# Friday/Monday around the 2026 DST changes (clocks forward 03-08, back 11-01), plus the
# review's example date. AV returns newest first.
DATES = ["2026-11-12", "2026-11-02", "2026-10-30", "2026-03-09", "2026-03-06"]
START, END = "2026-03-01", "2026-11-20"


def _bar(close: float) -> dict:
    return {
        "1. open": f"{close - 1:.4f}",
        "2. high": f"{close + 1:.4f}",
        "3. low": f"{close - 2:.4f}",
        "4. close": f"{close:.4f}",
        "5. volume": "1000",
    }


PAYLOAD = {
    "Meta Data": {"2. Symbol": "SPY"},
    "Time Series (Daily)": {date: _bar(100.0 + i) for i, date in enumerate(DATES)},
}


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return PAYLOAD


@pytest.fixture
def make_tool(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "TESTKEY")
    monkeypatch.setenv("DEFAULT_TIMEZONE", ET)
    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: FakeResponse())

    def build(cache_manager):
        return AlphaVantageMarketTool(cache_manager=cache_manager)

    return build


@pytest.fixture
def mock_cache():
    return MagicMock(get=MagicMock(return_value=None))


def test_daily_bars_keep_their_exchange_date_in_et(make_tool, mock_cache):
    df = make_tool(mock_cache).fetch_stock_data("SPY", START, END)

    assert len(df) == len(DATES)
    assert str(df.index.tz) == ET
    assert df.index.strftime("%Y-%m-%d").tolist() == DATES
    assert (df.index.hour == 0).all()  # midnight ET, not the evening before


def test_dates_survive_both_dst_transitions(make_tool, mock_cache):
    df = make_tool(mock_cache).fetch_stock_data("SPY", START, END)
    by_date = {ts.strftime("%Y-%m-%d"): ts for ts in df.index}

    # Before and after clocks go forward (EST -> EDT)
    assert by_date["2026-03-06"].utcoffset().total_seconds() == -5 * 3600
    assert by_date["2026-03-09"].utcoffset().total_seconds() == -4 * 3600
    # Before and after clocks go back (EDT -> EST)
    assert by_date["2026-10-30"].utcoffset().total_seconds() == -4 * 3600
    assert by_date["2026-11-02"].utcoffset().total_seconds() == -5 * 3600


def test_the_cache_receives_the_right_trading_dates(make_tool, mock_cache):
    make_tool(mock_cache).fetch_stock_data("SPY", START, END)

    mock_cache.set.assert_called_once()
    args, kwargs = mock_cache.set.call_args
    assert args[0] == "SPY"
    assert kwargs["source"] == "alpha_vantage"
    stored = _dated_rows(args[1], "1Day")  # what cache.set writes as trading_date
    assert sorted(stored["date"].tolist()) == sorted(DATES)


def test_cached_rows_carry_the_exchange_date(make_tool, tmp_path):
    cache = TradingCacheManager(db_path=str(tmp_path / "cache.db"))
    make_tool(cache).fetch_stock_data("SPY", START, END)

    cached = cache.get("SPY", START, END, source="alpha_vantage")
    assert cached is not None
    assert sorted(cached.index.strftime("%Y-%m-%d").tolist()) == sorted(DATES)
    assert cached.loc["2026-11-12", "close"] == pytest.approx(100.0)
