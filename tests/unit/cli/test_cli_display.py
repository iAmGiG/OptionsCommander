"""What the order and watchlist views print. The data behind them is mocked."""

from unittest.mock import patch

from src.cli.tools import order_tools, watchlist_tools


def orders_view(open_orders, local_state):
    result = {
        "status": "success",
        "ticker": "SPY",
        "entry_orders": [],
        "open_orders": open_orders,
        "local_state": local_state,
    }
    with patch.object(order_tools, "get_position_orders", return_value=result):
        return order_tools.show_position_orders("SPY")


STOP = {"type": "stop", "price": 744.47}
TARGET = {"type": "limit", "price": 800.0}
LOCAL = {"stop_price": 700.0, "target_price": 790.0}


def test_a_stop_at_the_broker_is_shown_once_at_its_own_price():
    view = orders_view([STOP], {"stop_price": 700.0})

    assert view.count("STOP LOSS") == 1
    assert "$744.47" in view
    assert "$700.00" not in view
    assert "from local state" not in view


def test_any_kind_of_stop_at_the_broker_replaces_the_saved_one():
    view = orders_view([{"type": "stop_limit", "price": 744.47}], {"stop_price": 700.0})

    assert "$700.00" not in view
    assert "from local state" not in view


def test_saved_levels_show_when_the_broker_has_no_orders():
    view = orders_view([], LOCAL)

    assert "Exit Orders (from local state)" in view
    assert "STOP LOSS: $700.00" in view
    assert "TAKE PROFIT: $790.00" in view


def test_only_the_level_the_broker_lacks_comes_from_local_state():
    view = orders_view([TARGET], LOCAL)

    assert "TAKE PROFIT: $800.00" in view
    assert "$790.00" not in view
    assert "from local state" in view
    assert "STOP LOSS: $700.00" in view


def test_no_watchlists_names_the_real_command():
    with patch.object(
        watchlist_tools, "list_watchlists", return_value={"status": "success", "watchlists": []}
    ):
        assert "/watchlist create NAME" in watchlist_tools.show_watchlists()
