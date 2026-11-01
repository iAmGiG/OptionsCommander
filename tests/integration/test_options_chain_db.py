"""
The options chain accessor against the real gex database (#572): read-only SELECTs. These run
with `pytest -m db`, on dates fixed well inside each vendor's period.
"""

from datetime import date

import pytest

from src.market_data.options_chain import (
    _connect,
    get_options_chain,
    latest_trading_date,
    list_expirations,
)

pytestmark = pytest.mark.db


def test_the_session_is_read_only():
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            assert cursor.fetchone()[0] == "on"
    finally:
        conn.close()


def test_a_chain_before_the_vendor_break():
    chain = get_options_chain("SPY", "2026-10-09")

    assert not chain.empty
    assert chain.after_vendor_break is False
    assert set(chain.contracts["data_source"]) == {"alpha_vantage"}
    assert set(chain.contracts["greeks_source"]) == {"vendor_legacy"}
    assert set(chain.contracts["option_type"]) == {"call", "put"}
    assert chain.contracts["delta"].notna().all()


def test_a_chain_after_the_vendor_break():
    chain = get_options_chain("SPY", "2026-10-30")

    assert not chain.empty
    assert chain.after_vendor_break is True
    assert set(chain.contracts["data_source"]) == {"cboe_delayed"}
    assert set(chain.contracts["greeks_source"]) == {"vendor_cboe"}
    assert any("2026-10-12" in warning for warning in chain.warnings)


def test_an_index_root_has_computed_greeks():
    chain = get_options_chain("SPX", "2026-10-30")

    assert set(chain.contracts["greeks_source"]) == {"black76_forward"}


def test_a_missing_iv_says_why():
    contracts = get_options_chain("SPY", "2026-10-30").contracts

    missing = contracts[contracts["implied_volatility"].isna()]
    assert len(missing) > 0
    assert set(missing["iv_status"]) <= {"zero_vega", "no_quote"}


def test_one_expiry_and_the_latest_date():
    day = "2026-10-30"
    expiries = list_expirations("SPY", day)
    chain = get_options_chain("SPY", day, expiries[0])

    assert expiries == sorted(expiries)
    assert set(chain.contracts["expiration"]) == {expiries[0]}
    assert latest_trading_date("SPY") >= date(2026, 10, 30)


def test_the_known_gap_day_warns():
    chain = get_options_chain("SPY", "2026-09-14")

    assert any("PARTIAL" in warning for warning in chain.warnings)


def test_an_unknown_symbol_is_empty():
    assert get_options_chain("ZZZZZ").empty
