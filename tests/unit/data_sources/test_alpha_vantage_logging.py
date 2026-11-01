"""
The Alpha Vantage key travels in the URL, and a requests error's text includes the URL, so
logging the error would log the key (#638). The request is faked: nothing leaves the machine.
"""

import logging

import pytest
import requests

from src.market_data import alpha_vantage_market as module
from src.market_data.alpha_vantage_market import AlphaVantageMarketTool, _safe_error

KEY = "SECRETKEY12345"
ERROR_TEXT = (
    "HTTPSConnectionPool(host='www.alphavantage.co', port=443): Max retries exceeded with url: "
    f"/query?function=TIME_SERIES_DAILY&symbol=SPY&apikey={KEY}&outputsize=full"
)


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", KEY)
    instance = AlphaVantageMarketTool()
    assert instance.api_key == KEY

    def refuse(*args, **kwargs):
        raise requests.exceptions.ConnectionError(ERROR_TEXT)

    monkeypatch.setattr(module.requests, "get", refuse)
    return instance


@pytest.mark.parametrize(
    ("fetch", "args"),
    [
        ("fetch_stock_data", ("SPY",)),
        ("fetch_company_overview", ("SPY",)),
        ("fetch_intraday_data", ("SPY",)),
        ("fetch_fx_data", ("EUR", "USD")),
    ],
)
def test_a_connection_error_doesnt_log_the_key(tool, caplog, fetch, args):
    with caplog.at_level(logging.DEBUG, logger="AlphaVantageMarketTool"):
        getattr(tool, fetch)(*args)

    assert "Error fetching" in caplog.text  # the failure is still logged
    assert "www.alphavantage.co" in caplog.text
    assert KEY not in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        f"url: /query?apikey={KEY}",
        f"url: /query?apikey={KEY}&outputsize=full",
        f"url: '/query?apikey={KEY}'",
        f"url: /query?APIKEY={KEY} failed",
    ],
)
def test_the_key_is_hidden_wherever_it_sits(text):
    cleaned = _safe_error(Exception(text))

    assert KEY not in cleaned
    assert "<hidden>" in cleaned


def test_other_text_is_kept():
    assert _safe_error(Exception("timed out")) == "timed out"
