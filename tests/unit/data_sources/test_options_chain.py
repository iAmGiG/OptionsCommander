"""
The options chain accessor (#572). A fake connection stands in for the database, so these run
in CI; tests/integration/test_options_chain_db.py reads the real one.
"""

from datetime import date, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.market_data import options_chain as module
from src.market_data.options_chain import (
    VENDOR_BREAK_DATE,
    get_options_chain,
    latest_trading_date,
    list_expirations,
)

EXPIRY = date(2026, 11, 20)


def _row(strike=600.0, kind="call", source="alpha_vantage", greeks=None, iv=None):
    return (
        EXPIRY,
        strike,
        kind,
        1.0,
        1.1,
        1.05,
        10,
        100,
        0.5,
        0.01,
        -0.2,
        0.3,
        0.1,
        0.2,
        601.0,
        20,
        source,
        greeks,
        iv,
    )


class FakeConnection:
    """Answers with queued rows, and records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.queries = []
        self.closed = False

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                connection.queries.append((sql, params))

            def fetchall(self):
                return connection.answers.pop(0)

        return Cursor()

    def close(self):
        self.closed = True


@pytest.fixture
def status_log(tmp_path):
    log = tmp_path / "DAILY_STATUS.md"
    log.write_text(
        "| Status | Date | DOW | Raw | Notes |\n"
        "| OK | 2026-10-09 | Fri | 4.4M | idx=1.2M |\n"
        "| PARTIAL | 2026-09-14 | Mon | 665K | INC-005: processes killed |\n"
    )
    return log


def chain(conn, status_log, day="2026-10-09", **kwargs):
    return get_options_chain("spy", day, conn=conn, status_log=status_log, **kwargs)


# Read-only


def test_a_connection_it_opens_is_read_only_and_closed(monkeypatch, status_log):
    psycopg2 = pytest.importorskip("psycopg2")
    conn = FakeConnection([_row()])
    conn.set_session = MagicMock()
    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: conn)

    get_options_chain("SPY", "2026-10-09", status_log=status_log)

    conn.set_session.assert_called_once_with(readonly=True, autocommit=True)
    assert conn.closed is True


def test_a_connection_it_is_given_stays_open(status_log):
    conn = FakeConnection([_row()])

    chain(conn, status_log)

    assert conn.closed is False


@pytest.mark.parametrize("sql", [module._CHAIN_SQL, module._LATEST_SQL, module._EXPIRATIONS_SQL])
def test_every_query_is_a_select(sql):
    assert sql.startswith("SELECT ")
    assert not any(word in sql.upper() for word in ("INSERT", "UPDATE", "DELETE", "DROP"))


# What comes back


def test_the_chain_for_a_day(status_log):
    conn = FakeConnection([_row(600.0, "call"), _row(600.0, "put")])

    result = chain(conn, status_log)

    assert result.symbol == "SPY"
    assert result.trading_date == date(2026, 10, 9)
    assert len(result.contracts) == 2
    assert list(result.contracts["option_type"]) == ["call", "put"]
    assert result.contracts["delta"].iloc[0] == 0.5
    assert conn.queries[0][1] == ("SPY", date(2026, 10, 9))
    assert "ORDER BY expiration, strike" in conn.queries[0][0]


@pytest.mark.parametrize(
    "day",
    [date(2026, 10, 30), datetime(2026, 10, 30, 16, 0), pd.Timestamp("2026-10-30"), "2026-10-30"],
)
def test_any_date_form_works(status_log, day):
    result = chain(FakeConnection([_row()]), status_log, day)

    assert result.trading_date == date(2026, 10, 30)
    assert type(result.trading_date) is date
    assert result.after_vendor_break is True


def test_an_empty_date_is_an_error_not_the_latest(status_log):
    with pytest.raises(ValueError):
        chain(FakeConnection([_row()]), status_log, "")


def test_an_empty_expiry_is_an_error_not_all_of_them(status_log):
    with pytest.raises(ValueError):
        chain(FakeConnection([_row()]), status_log, expiration="")


def test_an_expiry_narrows_it(status_log):
    conn = FakeConnection([_row()])

    chain(conn, status_log, expiration="2026-11-20")

    sql, params = conn.queries[0]
    assert "AND expiration = %s" in sql
    assert params == ("SPY", date(2026, 10, 9), EXPIRY)


def test_no_date_uses_the_latest(status_log):
    conn = FakeConnection([(date(2026, 10, 9),)], [_row()])

    result = get_options_chain("SPY", conn=conn, status_log=status_log)

    assert result.trading_date == date(2026, 10, 9)
    assert len(result.contracts) == 1


def test_a_symbol_with_no_chains_is_empty_not_an_error(status_log):
    conn = FakeConnection([(None,)])

    result = get_options_chain("ZZZZ", conn=conn, status_log=status_log)

    assert result.empty
    assert result.trading_date is None
    assert result.warnings == ()


def test_a_day_with_no_data_is_empty_and_still_warns(status_log):
    conn = FakeConnection([])

    result = chain(conn, status_log, "2026-09-14")

    assert result.empty
    assert any("PARTIAL" in warning for warning in result.warnings)


def test_latest_and_expirations(status_log):
    assert latest_trading_date("spy", FakeConnection([(date(2026, 10, 30),)])) == date(2026, 10, 30)
    conn = FakeConnection([(EXPIRY,), (date(2026, 12, 18),)])
    assert list_expirations("spy", "2026-10-09", conn) == [EXPIRY, date(2026, 12, 18)]
    assert list_expirations("ZZZZ", conn=FakeConnection([(None,)])) == []


# Where the data came from


@pytest.mark.parametrize(
    ("day", "flagged"),
    [("2026-10-09", False), ("2026-10-12", True), ("2026-10-30", True)],
)
def test_the_vendor_break_is_flagged(status_log, day, flagged):
    result = chain(FakeConnection([_row()]), status_log, day)

    assert result.after_vendor_break is flagged
    assert any("2026-10-12" in warning for warning in result.warnings) is flagged
    assert VENDOR_BREAK_DATE == date(2026, 10, 12)


@pytest.mark.parametrize(
    ("code", "label"),
    [
        (None, "vendor_legacy"),
        (0, "vendor"),
        (1, "bs_spot"),
        (2, "black76_forward"),
        (3, "vendor_cboe"),
        (9, "unknown_9"),
    ],
)
def test_greeks_source_is_exposed_as_a_label(status_log, code, label):
    result = chain(FakeConnection([_row(greeks=code)]), status_log)

    assert result.contracts["greeks_source"].iloc[0] == label


@pytest.mark.parametrize(
    ("code", "label"),
    [(None, None), (0, "ok"), (4, "zero_vega"), (2, "no_quote"), (9, "unknown_9")],
)
def test_iv_status_is_exposed_as_a_label(status_log, code, label):
    result = chain(FakeConnection([_row(iv=code)]), status_log)

    assert result.contracts["iv_status"].iloc[0] == label


def test_mixed_nulls_and_codes_stay_correct(status_log):
    """pandas turns a column with None and numbers into floats and NaN."""
    rows = [_row(greeks=None, iv=None), _row(greeks=3, iv=4)]

    result = chain(FakeConnection(rows), status_log)

    assert list(result.contracts["greeks_source"]) == ["vendor_legacy", "vendor_cboe"]
    assert result.contracts["iv_status"].iloc[1] == "zero_vega"
    assert result.contracts["iv_status"].isna().iloc[0]


# Warnings


def test_a_clean_day_has_no_warnings(status_log):
    assert chain(FakeConnection([_row()]), status_log).warnings == ()


def test_a_day_the_log_marks_incomplete_warns(status_log):
    result = chain(FakeConnection([_row()]), status_log, "2026-09-14")

    assert result.warnings == (
        "Collection log marks 2026-09-14 as PARTIAL: INC-005: processes killed",
    )


def test_a_status_with_no_notes_reads_cleanly(tmp_path):
    log = tmp_path / "log.md"
    log.write_text("| PARTIAL | 2026-10-09 | Fri |  |\n")

    result = chain(FakeConnection([_row()]), log)

    assert result.warnings == ("Collection log marks 2026-10-09 as PARTIAL",)


def test_a_day_the_log_doesnt_cover_has_no_warning(status_log):
    assert chain(FakeConnection([_row()]), status_log, "2015-03-02").warnings == ()


def test_the_log_is_found_through_the_gex_package(monkeypatch):
    origin = "/somewhere/gex-repo/src/gex_db_infrastructure/__init__.py"
    spec = MagicMock(origin=origin)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: spec)
    monkeypatch.setattr(module.Path, "exists", lambda self: True)

    assert str(module._status_log_path()) == "/somewhere/gex-repo/DAILY_STATUS.md"


def test_without_the_gex_package_it_looks_beside_the_repo(monkeypatch):
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)

    assert module._status_log_path().name == "DAILY_STATUS.md"
    assert module._status_log_path().parent.name == "gex-database-infrastructure"


def test_an_unreadable_log_is_said_not_ignored(tmp_path):
    result = chain(FakeConnection([_row()]), tmp_path / "missing.md")

    assert "Collection status not checked" in result.warnings[0]


@pytest.mark.parametrize(
    ("symbol", "day", "warned"),
    [
        ("SNDK", "2015-03-02", True),
        ("SNDK", "2025-02-24", False),
        ("SNOW", "2016-06-01", True),
        ("SNOW", "2021-01-04", False),
        ("SPY", "2015-03-02", False),
    ],
)
def test_a_ticker_that_was_another_company_warns(status_log, symbol, day, warned):
    result = get_options_chain(symbol, day, conn=FakeConnection([_row()]), status_log=status_log)

    assert any("different company" in warning for warning in result.warnings) is warned


@pytest.mark.parametrize("source", ["cboe_delayed_nextday", "cboe_delayed_final_rush"])
def test_an_unusual_source_is_noted(status_log, source):
    result = chain(FakeConnection([_row(source=source)]), status_log, "2026-10-29")

    assert len(result.warnings) == 2  # the vendor break, and this


def test_the_polygon_era_tool_is_gone():
    assert module.Path(module.__file__).with_name("unified_options_tool.py").exists() is False
