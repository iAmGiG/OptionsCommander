"""
Selling a position from the CLI: the shares a broker stop holds are released first, and the
position is never left without its stop by a failure. A fake order manager stands in for
the broker: nothing here sends an order.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.trading.broker import alpaca_execution_manager as module
from src.trading.broker.alpaca_execution_manager import AlpacaExecutionManager

STOP = {
    "id": "stop-1",
    "side": "sell",
    "status": "new",
    "order_type": "stop",
    "stop_price": 744.47,
    "qty": 14.0,
    "legs": [],
}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(module, "_RELEASE_WAIT_SECONDS", 0)
    monkeypatch.setattr(module, "is_market_hours", lambda: True)


@pytest.fixture
def broker():
    """A broker holding 14 SPY under one stop; a cancel takes effect straight away."""
    fake = MagicMock()
    fake.open = [dict(STOP)]
    fake.get_positions.return_value = [
        {"symbol": "SPY", "qty": "14", "side": "long", "qty_available": "0"}
    ]
    fake.get_orders.side_effect = lambda **kwargs: list(fake.open)

    def cancel(order_id):
        fake.open = [o for o in fake.open if o["id"] != order_id]
        return {"status": "cancelled", "order_id": order_id}

    fake.cancel_order.side_effect = cancel
    fake.place_market_order.return_value = {"status": "submitted", "order_id": "sell-1"}
    fake.place_stop_order.return_value = {"status": "submitted", "order_id": "stop-2"}
    return fake


@pytest.fixture
def manager(broker):
    return AlpacaExecutionManager(order_manager=broker)


def close(manager, plan):
    return asyncio.run(manager.close_position(plan))


# The plan


def test_no_position_has_no_plan(manager, broker):
    broker.get_positions.return_value = []

    assert manager.plan_close("SPY") is None


def test_a_short_position_is_not_sold(manager, broker):
    broker.get_positions.return_value = [{"symbol": "SPY", "qty": "-14", "side": "short"}]

    assert manager.plan_close("SPY") is None


def test_the_plan_covers_the_whole_position_by_default(manager):
    plan = manager.plan_close("SPY")

    assert (plan["quantity"], plan["held"], plan["market_open"]) == (14, 14, True)
    assert [o["id"] for o in plan["orders_to_cancel"]] == ["stop-1"]


def test_a_quantity_is_capped_at_the_position(manager):
    assert manager.plan_close("SPY", 500)["quantity"] == 14
    assert manager.plan_close("SPY", 5)["quantity"] == 5


def test_free_shares_need_no_cancel(manager, broker):
    broker.get_positions.return_value = [
        {"symbol": "SPY", "qty": "14", "side": "long", "qty_available": "6"}
    ]

    assert manager.plan_close("SPY", 5)["orders_to_cancel"] == []
    assert len(manager.plan_close("SPY", 7)["orders_to_cancel"]) == 1


def test_without_qty_available_the_open_sells_count_as_held(manager, broker):
    broker.get_positions.return_value = [{"symbol": "SPY", "qty": "20", "side": "long"}]

    assert manager.plan_close("SPY", 6)["orders_to_cancel"] == []
    assert len(manager.plan_close("SPY", 7)["orders_to_cancel"]) == 1


def test_bracket_legs_count_and_buys_dont(manager, broker):
    leg = {**STOP, "id": "leg"}
    parent = {"id": "parent", "side": "buy", "status": "filled", "qty": 14.0, "legs": [leg]}
    buy = {"id": "buy", "side": "buy", "status": "new", "qty": 1.0, "legs": []}
    broker.open = [parent, buy]

    assert [o["id"] for o in manager.plan_close("SPY")["orders_to_cancel"]] == ["leg"]


def test_planning_changes_nothing(manager, broker):
    manager.plan_close("SPY")

    broker.cancel_order.assert_not_called()
    broker.place_market_order.assert_not_called()


# The sale


def test_the_stop_is_released_before_the_sale(manager, broker):
    result = close(manager, manager.plan_close("SPY"))

    assert result.success is True
    assert (result.ticker, result.quantity, result.entry_order_id) == ("SPY", 14, "sell-1")
    names = [call[0] for call in broker.method_calls]
    assert names.index("cancel_order") < names.index("place_market_order")
    broker.place_market_order.assert_called_once_with(symbol="SPY", qty=14, side="sell")
    broker.place_stop_order.assert_not_called()


def test_it_waits_for_alpaca_to_release_the_shares(manager, broker):
    answers = iter([[dict(STOP)], [dict(STOP)], [dict(STOP)], []])
    plan = manager.plan_close("SPY")
    broker.get_orders.side_effect = lambda **kwargs: next(answers)
    broker.cancel_order.side_effect = lambda order_id: {"status": "cancelled"}

    assert close(manager, plan).success is True


def test_a_cancel_that_never_lands_sells_nothing(manager, broker):
    plan = manager.plan_close("SPY")
    broker.cancel_order.side_effect = lambda order_id: {"status": "error", "message": "no"}

    result = close(manager, plan)

    assert result.success is False
    assert "still open" in result.message
    broker.place_market_order.assert_not_called()
    broker.place_stop_order.assert_not_called()


def test_a_stop_still_being_cancelled_gets_no_duplicate(manager, broker):
    plan = manager.plan_close("SPY")
    broker.cancel_order.side_effect = lambda order_id: {"status": "cancelled"}  # stays open

    result = close(manager, plan)

    assert result.success is False
    broker.place_market_order.assert_not_called()
    broker.place_stop_order.assert_not_called()


def test_an_already_gone_order_is_not_a_failure(manager, broker):
    """Cancelling one leg of an OCO cancels the other, so its own cancel is refused."""
    plan = manager.plan_close("SPY")
    broker.open = []
    broker.cancel_order.side_effect = lambda order_id: {"status": "error", "message": "gone"}

    assert close(manager, plan).success is True


@pytest.mark.parametrize(
    "refusal", [{"status": "error", "message": "insufficient qty"}, {"status": "dry_run"}]
)
def test_a_refused_sale_puts_the_stop_back(manager, broker, refusal):
    broker.place_market_order.return_value = refusal

    result = close(manager, manager.plan_close("SPY"))

    assert result.success is False
    assert "stop is back in place" in result.message
    broker.place_stop_order.assert_called_once_with(
        symbol="SPY", qty=14, side="sell", stop_price=744.47, time_in_force="gtc"
    )


def test_a_sale_that_raises_puts_the_stop_back(manager, broker):
    broker.place_market_order.side_effect = RuntimeError("connection reset")

    result = close(manager, manager.plan_close("SPY"))

    assert result.success is False
    assert "connection reset" in result.message
    broker.place_stop_order.assert_called_once()


def test_a_stop_that_cant_go_back_is_said_loudly(manager, broker):
    broker.place_market_order.return_value = {"status": "error", "message": "refused"}
    broker.place_stop_order.return_value = {"status": "error", "message": "refused too"}

    result = close(manager, manager.plan_close("SPY"))

    assert "COULD NOT BE PUT BACK" in result.message


def test_a_partial_sale_gives_the_rest_a_new_stop(manager, broker):
    result = close(manager, manager.plan_close("SPY", 5))

    assert result.success is True
    assert "other 9 shares" in result.message
    broker.place_market_order.assert_called_once_with(symbol="SPY", qty=5, side="sell")
    broker.place_stop_order.assert_called_once_with(
        symbol="SPY", qty=9, side="sell", stop_price=744.47, time_in_force="gtc"
    )


def test_a_partial_sale_says_when_the_rest_has_no_stop(manager, broker):
    broker.place_stop_order.return_value = {"status": "error", "message": "refused"}

    result = close(manager, manager.plan_close("SPY", 5))

    assert result.success is True
    assert "OTHER 9 SHARES HAVE NO STOP" in result.message


def test_free_shares_are_sold_without_touching_the_stop(manager, broker):
    broker.get_positions.return_value = [
        {"symbol": "SPY", "qty": "14", "side": "long", "qty_available": "6"}
    ]

    assert close(manager, manager.plan_close("SPY", 5)).success is True

    broker.cancel_order.assert_not_called()
    broker.place_stop_order.assert_not_called()


def test_no_order_manager_refuses(broker):
    with pytest.raises(ValueError, match="No order manager"):
        AlpacaExecutionManager(order_manager=None).plan_close("SPY")
