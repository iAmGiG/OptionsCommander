"""
Typed cancel commands (#633): they reach the broker through the routines' order manager,
ask first, cancel what they listed, and aren't set off by ordinary wording. Everything is
mocked: no test here sends a cancel.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from alpaca.trading.client import TradingClient

from src.cli.cli_session import CLISession
from src.cli.tools import order_tools
from src.cli.utils.input_parser import (
    extract_ticker_from_query,
    is_cancel_request,
    parse_cancel_request,
)

SPY_STOP = {
    "id": "b24b3273-dfdc-4acb-81bc-ef7e4dd0a03d",
    "symbol": "SPY",
    "side": "sell",
    "type": "stop",
    "stop_price": 744.47,
    "qty": 14,
}
TSLA_LIMIT = {
    "id": "5e60f4f0-39fe-4261-a02b-5259b1fd41b9",
    "symbol": "TSLA",
    "side": "buy",
    "type": "limit",
    "limit_price": 200.0,
    "qty": 1,
}


@pytest.fixture
def manager():
    """A fake order manager that the tools get; it succeeds unless told otherwise."""
    fake = MagicMock()
    fake.get_orders.return_value = [dict(SPY_STOP), dict(TSLA_LIMIT)]
    fake.cancel_order.side_effect = lambda order_id: {"status": "cancelled", "order_id": order_id}
    with patch.object(order_tools, "_get_order_manager", return_value=fake):
        yield fake


def cancelled_ids(fake):
    return [call.args[0] for call in fake.cancel_order.call_args_list]


def run_typed(typed, answer):
    """Send typed text through the handler, answering any prompt; returns the prompts."""
    session = CLISession.__new__(CLISession)  # only the cancel handler is exercised
    with patch("builtins.input", return_value=answer) as prompt:
        asyncio.run(session._handle_cancel_request(typed))
    return prompt.call_count


# The wrapper mistake that made every cancel fail (#633)


def test_a_cancel_reaches_the_sdk_client():
    """The real manager over an SDK-shaped client: a method it lacks would raise here."""
    sdk = create_autospec(TradingClient, instance=True)
    config = MagicMock()
    with (
        patch("src.trading.broker.alpaca_trading_client.ConfigLoader", return_value=config),
        patch("src.trading.broker.alpaca_trading_client.TradingClient", return_value=sdk),
        patch("src.trading.broker.alpaca_trading_client.ALPACA_TRADING_AVAILABLE", True),
        patch.dict("src.trading.broker.alpaca_trading_client.AlpacaTradingClient._instances", {}),
        patch(
            "src.trading.broker.alpaca_trading_client.AlpacaOrderManager.get_orders",
            return_value=[dict(SPY_STOP)],
        ),
    ):
        result = order_tools.cancel_order("b24b3273")

    assert result["status"] == "success"
    assert result["cancelled"] is True
    sdk.cancel_order_by_id.assert_called_once_with(SPY_STOP["id"])


# What the tools report


def test_cancel_order_matches_a_partial_id(manager):
    result = order_tools.cancel_order("b24b")

    assert result == {
        "status": "success",
        "order_id": SPY_STOP["id"],
        "cancelled": True,
        "symbol": "SPY",
    }
    assert cancelled_ids(manager) == [SPY_STOP["id"]]


def test_cancel_order_reports_a_broker_refusal(manager):
    manager.cancel_order.side_effect = None
    manager.cancel_order.return_value = {"status": "error", "message": "already filled"}

    result = order_tools.cancel_order("b24b")

    assert result["status"] == "error"
    assert result["cancelled"] is False
    assert result["error"] == "already filled"


def test_cancel_order_not_found_and_ambiguous_cancel_nothing(manager):
    missing = order_tools.cancel_order("ffff")
    assert missing["status"] == "not_found"
    assert missing["cancelled"] is False
    manager.get_orders.return_value = [dict(SPY_STOP), {**TSLA_LIMIT, "id": "b24b-other"}]
    assert order_tools.cancel_order("b24b")["status"] == "ambiguous"
    manager.cancel_order.assert_not_called()


def test_cancel_all_counts_what_happened(manager):
    result = order_tools.cancel_all_orders()

    assert (result["status"], result["cancelled_count"], result["failed_count"]) == (
        "success",
        2,
        0,
    )
    assert cancelled_ids(manager) == [SPY_STOP["id"], TSLA_LIMIT["id"]]


def test_cancel_all_partial_and_total_failure_are_not_success(manager):
    outcomes = iter([{"status": "cancelled"}, {"status": "error", "message": "refused"}])
    manager.cancel_order.side_effect = lambda order_id: next(outcomes)
    partial = order_tools.cancel_all_orders()
    assert (partial["status"], partial["cancelled_count"], partial["failed_count"]) == (
        "partial",
        1,
        1,
    )
    assert partial["error"] == "refused"

    manager.cancel_order.side_effect = lambda order_id: {"status": "error", "message": "down"}
    assert order_tools.cancel_all_orders()["status"] == "error"


def test_cancel_symbol_only_touches_that_symbol(manager):
    result = order_tools.cancel_symbol_orders("tsla")

    assert result["symbol"] == "TSLA"
    assert result["cancelled_count"] == 1
    assert cancelled_ids(manager) == [TSLA_LIMIT["id"]]


def test_a_failed_order_lookup_is_an_error_not_no_orders(manager):
    manager.get_orders.side_effect = RuntimeError("broker down")

    assert order_tools.cancel_all_orders()["status"] == "error"
    assert order_tools.cancel_symbol_orders("SPY")["status"] == "error"
    manager.cancel_order.assert_not_called()


# Wording


@pytest.mark.parametrize(
    "typed",
    [
        "delete all my alerts",
        "remove the small one",
        "cancel my small TSLA position",
        "I recalled a reordering",
        "cancel",
    ],
)
def test_ordinary_wording_is_not_a_cancel(typed):
    assert not is_cancel_request(typed)


@pytest.mark.parametrize(
    ("typed", "target"),
    [
        ("cancel all orders", {"all": "all"}),
        ("cancel all my orders", {"all": "all"}),
        ("cancel all", {"all": "all"}),
        ("cancel my small TSLA order", {"symbol": "TSLA"}),
        ("cancel my small tsla order", {"symbol": "TSLA"}),
        ("cancel orders for ALLY", {"symbol": "ALLY"}),
        ("cancel my orders for aapl", {"symbol": "AAPL"}),
        ("cancel all AAPL orders", {"symbol": "AAPL"}),
        ("remove orders on $NOW", {"symbol": "NOW"}),
        ("cancel order b24b3273", {"order_id": "b24b3273"}),
        ("cancel order B24B3273-DFDC", {"order_id": "b24b3273-dfdc"}),
        ("cancel AAPL orders from 2026-10-30", {"symbol": "AAPL"}),
        ("cancel both orders", {}),
        ("cancel the order", {}),
        ("how do I cancel an order?", {}),
    ],
)
def test_what_a_cancel_applies_to(typed, target):
    assert is_cancel_request(typed)
    assert parse_cancel_request(typed) == target


def test_a_hex_looking_word_is_not_an_order_id():
    assert "order_id" not in parse_cancel_request("delete the defaced order")


@pytest.mark.parametrize(
    ("typed", "ticker"),
    [
        ("show orders for AAPL", "AAPL"),
        ("what's the stop on $TSLA?", "TSLA"),
        ("what's the stop on tsla", "TSLA"),
        ("show me the orders", None),
        ("orders of my NVDA", "NVDA"),
    ],
)
def test_extract_ticker(typed, ticker):
    assert extract_ticker_from_query(typed) == ticker


def test_routing_sends_only_real_cancels_to_the_handler():
    session = CLISession.__new__(CLISession)
    session._handle_cancel_request = AsyncMock()
    session._handle_trade_or_status_request = AsyncMock()

    asyncio.run(session._process_request("delete all my alerts"))
    session._handle_cancel_request.assert_not_called()
    session._handle_trade_or_status_request.assert_called_once()

    asyncio.run(session._process_request("cancel all orders"))
    session._handle_cancel_request.assert_called_once()


# The typed flow


def test_declining_cancels_nothing(manager, capsys):
    assert run_typed("cancel all orders", answer="no") == 1

    manager.cancel_order.assert_not_called()
    assert "Nothing cancelled" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "y", "yes please", "cancel"])
def test_only_yes_confirms(manager, answer):
    run_typed("cancel all orders", answer=answer)

    manager.cancel_order.assert_not_called()


def test_the_listing_marks_stops_and_warns(manager, capsys):
    run_typed("cancel all orders", answer="no")

    out = capsys.readouterr().out
    assert "This would cancel 2 open order(s)" in out
    spy, tsla = [line for line in out.splitlines() if "SPY" in line or "TSLA" in line]
    assert "STOP" in spy and "744.47" in spy
    assert "STOP" not in tsla
    assert "Stops in this list: 1" in out


def test_a_bracket_stop_leg_counts_as_a_stop(manager, capsys):
    bracket = {
        **TSLA_LIMIT,
        "legs": [{**SPY_STOP, "symbol": "TSLA", "id": "leg"}, {**TSLA_LIMIT, "id": "tp"}],
    }
    manager.get_orders.return_value = [bracket]

    run_typed("cancel all orders", answer="no")

    out = capsys.readouterr().out
    assert "has a STOP leg" in out
    assert "Stops in this list: 1" in out


def test_cancel_all_asks_for_every_open_order(manager):
    order_tools.cancel_all_orders()

    assert manager.get_orders.call_args.kwargs == {"status": "open", "limit": 500}


def test_no_stop_warning_without_a_stop(manager, capsys):
    run_typed("cancel orders for TSLA", answer="no")

    assert "Stops in this list" not in capsys.readouterr().out


def test_confirming_cancels_what_was_listed_and_says_so(manager, capsys):
    assert run_typed("cancel all orders", answer=" YES ") == 1

    assert cancelled_ids(manager) == [SPY_STOP["id"], TSLA_LIMIT["id"]]
    assert "Cancelled 2 order(s)" in capsys.readouterr().out


def test_an_order_placed_after_the_listing_is_not_cancelled(manager):
    """A routine can place a new stop while the prompt waits."""

    def answer(_prompt):
        manager.get_orders.return_value = [
            dict(SPY_STOP),
            dict(TSLA_LIMIT),
            {**SPY_STOP, "id": "new-stop"},
        ]
        return "yes"

    session = CLISession.__new__(CLISession)
    with patch("builtins.input", side_effect=answer):
        asyncio.run(session._handle_cancel_request("cancel all orders"))

    assert "new-stop" not in cancelled_ids(manager)


def test_the_small_tsla_order_leaves_the_spy_stop_alone(manager):
    run_typed("cancel my small TSLA order", answer="yes")

    assert cancelled_ids(manager) == [TSLA_LIMIT["id"]]


def test_auto_mode_still_asks(manager):
    session = CLISession.__new__(CLISession)
    session.autonomy_mode = "auto"
    with patch("builtins.input", return_value="no") as prompt:
        asyncio.run(session._handle_cancel_request("cancel all orders"))

    prompt.assert_called_once()
    manager.cancel_order.assert_not_called()


def test_a_symbol_with_no_orders_says_so_without_asking(manager, capsys):
    assert run_typed("cancel orders for ALLY", answer="yes") == 0

    assert "No open orders for ALLY" in capsys.readouterr().out
    manager.cancel_order.assert_not_called()


def test_a_failed_lookup_is_not_reported_as_no_orders(manager, capsys):
    manager.get_orders.side_effect = RuntimeError("broker down")

    assert run_typed("cancel orders for SPY", answer="yes") == 0

    out = capsys.readouterr().out
    assert "broker down" in out
    assert "No open orders" not in out


def test_failures_are_reported(manager, capsys):
    manager.cancel_order.side_effect = lambda order_id: {"status": "error", "message": "refused"}

    run_typed("cancel order b24b3273", answer="yes")

    out = capsys.readouterr().out
    assert "Failed to cancel 1 order(s): refused" in out
    assert "Cancelled" not in out


def test_an_unknown_id_says_so_without_asking(manager, capsys):
    assert run_typed("cancel order abcd1234", answer="yes") == 0

    assert "abcd1234" in capsys.readouterr().out


def test_no_target_shows_usage(manager, capsys):
    assert run_typed("cancel the order", answer="yes") == 0

    assert "Could not determine what to cancel" in capsys.readouterr().out
    manager.get_orders.assert_not_called()
