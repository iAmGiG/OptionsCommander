"""
/records (#602): equity, fills and realized P&L, read from the daily records. The records are
real, in the test's temp state dir (tests/unit/conftest.py), and nothing reaches a broker.
"""

import asyncio
import socket
from unittest.mock import MagicMock

import pytest
import yaml

from src.cli.commands import slash_commands  # noqa: F401  registers the commands
from src.cli.commands.records_commands import USAGE, records_command, show_records
from src.cli.commands.registry import CommandRegistry
from src.trading.state.daily_records import DailyRecords


def _order(order_id, symbol, side, qty, price, at, order_type="market"):
    return {
        "id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "filled_qty": qty,
        "filled_avg_price": price,
        "filled_at": at,
        "order_type": order_type,
        "order_class": "simple",
        "legs": [],
    }


def _day(records, day, equity, last_equity, positions=None):
    at = f"{day}T19:50:00+00:00"
    account = {"equity": equity, "last_equity": last_equity}
    records.record_account(day, "evening", account, at)
    records.record_positions(day, "evening", positions or {}, at)


@pytest.fixture
def records():
    book = DailyRecords()
    _day(book, "2026-10-28", 100_000.0, 99_000.0)
    qqq = {"quantity": 3, "entry_price": 616.80, "current_price": 714.47, "unrealized_pl": 293.0}
    _day(book, "2026-10-29", 100_500.0, 100_000.0, {"QQQ": qqq})
    _day(book, "2026-10-30", 100_250.0, 100_500.0)
    book.record_fills(
        [
            _order("b1", "QQQ", "buy", 3, 616.80, "2025-11-28T15:36:02+00:00"),
            _order("s1", "QQQ", "sell", 3, 696.00, "2026-10-30T13:32:32+00:00", "stop"),
        ]
    )
    return book


def test_no_records_says_so():
    assert "No daily records yet" in show_records()


def test_equity_by_day_with_day_pnl(records):
    text = show_records()

    assert "2026-10-28" in text
    assert "$100,500.00" in text
    assert "+500.00" in text
    assert "-250.00" in text


def test_the_stop_out_shows_as_a_fill_and_realized_pnl(records):
    text = show_records()

    assert "QQQ    sell 3 @ $696.00 (stop)" in text
    assert "QQQ    3 sold: +237.60" in text
    assert "Total: +237.60 over 1 sale(s)" in text


def test_days_limits_equity_and_fills_to_that_period(records):
    records.record_fills([_order("s2", "SPY", "sell", 1, 700.0, "2026-10-28T15:00:00+00:00")])

    text = show_records(2)

    assert "2026-10-28" not in text
    assert "2026-10-29" in text
    assert "SPY" not in text
    assert "last 2 recorded days" in text


def test_all_includes_fills_from_before_records_began(records):
    text = show_records(None)

    assert "2025-11-28" in text
    assert "QQQ    buy  3 @ $616.80" in text
    assert "all recorded days" in text


def test_a_period_with_no_fills_says_so(records):
    _day(records, "2026-11-02", 100_300.0, 100_250.0)

    text = show_records(1)

    assert "None in this period" in text
    assert "No sales in this period" in text


def test_an_unknown_cost_basis_is_said_not_guessed():
    book = DailyRecords()
    _day(book, "2026-10-30", 100_000.0, 100_000.0)
    book.record_fills([_order("s1", "AAPL", "sell", 2, 200.0, "2026-10-30T15:00:00+00:00")])

    text = show_records()

    assert "AAPL   2 sold: cost basis unknown" in text
    assert "Total: +0.00 over 0 sale(s); 1 with unknown cost basis" in text


def test_a_missing_equity_shows_a_dash():
    book = DailyRecords()
    _day(book, "2026-10-30", None, None)

    assert " -" in show_records()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (None, "last 3 recorded days"),
        ("", "last 3 recorded days"),
        ("2", "last 2 recorded days"),
        (" 2 ", "last 2 recorded days"),
        ("99", "last 3 recorded days"),
        ("all", "all recorded days"),
        ("ALL", "all recorded days"),
    ],
)
def test_arguments(records, args, expected):
    assert expected in records_command(args)


@pytest.mark.parametrize("args", ["0", "-1", "abc", "1.5", "2 3"])
def test_a_bad_argument_shows_usage(records, args):
    assert records_command(args) == USAGE


def test_it_reaches_no_network_and_changes_no_record(records, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    before = (records.equity_series(), records.fills_after(None))

    show_records()

    assert (records.equity_series(), records.fills_after(None)) == before


def test_the_command_is_registered_and_runs(records, capsys):
    asyncio.run(CommandRegistry.execute("/records 2", MagicMock()))

    assert "last 2 recorded days" in capsys.readouterr().out


def test_help_lists_it():
    with open("config_defaults/help_commands.yaml") as f:
        assert "/records" in yaml.safe_load(f)
