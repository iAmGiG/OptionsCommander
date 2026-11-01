"""
GTT triggers (#635): an order trigger names its order, a gain or loss trigger can fire, and a
percent means a percent. The store is real, on a temp database; nothing sends an order.
"""

from unittest.mock import MagicMock

import pytest

from src.cli.commands import slash_commands
from src.cli.tools import get_cli_tool, gtt_tools
from src.trading.gtt import trailing_stop_bridge as bridge
from src.trading.gtt.action_executor import ActionExecutor
from src.trading.gtt.gtt_manager import ActionType, ConditionType, GTTManager
from src.trading.gtt.trigger_evaluator import TriggerEvaluator
from src.trading.orders.trailing_stop_manager import StopState


@pytest.fixture
def gtt(tmp_path, monkeypatch):
    manager = GTTManager(db_path=str(tmp_path / "gtt.db"))
    monkeypatch.setattr(gtt_tools, "_get_gtt_manager", lambda: manager)
    monkeypatch.setattr("src.trading.gtt.trigger_evaluator.get_gtt_manager", lambda: manager)
    monkeypatch.setattr(bridge, "get_gtt_manager", lambda: manager)
    monkeypatch.setattr(
        "src.trading.utils.unified_price_fetcher.get_current_price", lambda symbol: 100.0
    )
    return manager


def created(gtt, **kwargs):
    result = gtt_tools.create_gtt_trigger(**kwargs)
    assert result["status"] == "success", result
    return gtt.get_trigger(result["trigger_id"]), result


def refused(**kwargs):
    result = gtt_tools.create_gtt_trigger(**kwargs)
    assert result["status"] == "error"
    return result["message"]


# Order triggers


def test_an_order_trigger_carries_its_order_and_fires_once(gtt):
    trigger, result = created(
        gtt, symbol="SPY", condition="below", value=700, action="order", side="sell", quantity=5
    )

    assert trigger.action_type == ActionType.PLACE_ORDER.value
    assert trigger.action_config == {"order_type": "market", "side": "sell", "qty": 5}
    assert trigger.max_triggers == 1
    assert "sell 5 at market" in result["message"]


def test_an_explicit_firing_limit_is_kept(gtt):
    trigger, _ = created(
        gtt,
        symbol="SPY",
        condition="below",
        value=700,
        action="order",
        side="sell",
        quantity=5,
        max_triggers=3,
    )

    assert trigger.max_triggers == 3


@pytest.mark.parametrize(
    "order",
    [
        {},
        {"side": "sell"},
        {"quantity": 5},
        {"side": "hold", "quantity": 5},
        {"side": "sell", "quantity": 0},
        {"side": "sell", "quantity": -1},
    ],
)
def test_an_order_trigger_without_its_order_is_refused(gtt, order):
    message = refused(symbol="SPY", condition="below", value=700, action="order", **order)

    assert "needs a side" in message
    assert gtt.get_triggers(enabled_only=False, active_only=False) == []


@pytest.mark.parametrize("action", ["sell", "buy", "cancel", "cancel_order", "nonsense"])
def test_an_unknown_action_is_refused_not_turned_into_an_alert(gtt, action):
    assert "Unknown action" in refused(symbol="SPY", condition="below", value=700, action=action)


def test_the_schema_offers_side_and_quantity():
    properties = get_cli_tool("create_gtt_trigger").parameters()["properties"]

    assert "side" in properties
    assert "quantity" in properties


def test_an_alert_stays_an_alert(gtt):
    trigger, result = created(gtt, symbol="SPY", condition="above", value=800)

    assert trigger.action_type == ActionType.ALERT.value
    assert trigger.max_triggers is None
    assert "then" not in result["message"]


# The executor never guesses


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"side": "sell"},
        {"qty": 5},
        {"side": "short", "qty": 5},
        {"side": "sell", "qty": -5},
        {"side": "sell", "qty": "abc"},
    ],
)
def test_the_executor_refuses_a_trigger_with_no_order(gtt, config):
    trigger = gtt.create_trigger(
        symbol="SPY",
        condition_type=ConditionType.PRICE_BELOW,
        trigger_value=700.0,
        action_type=ActionType.PLACE_ORDER,
        action_config=config,
    )
    executor = ActionExecutor.__new__(ActionExecutor)
    manager = MagicMock()
    executor._get_order_manager_instance = lambda: manager

    result = executor._execute_place_order(trigger, 650.0)

    assert result.success is False
    assert "no order placed" in result.message
    manager.place_market_order.assert_not_called()
    manager.place_limit_order_gtc.assert_not_called()


# Gain and loss


@pytest.mark.parametrize(
    ("condition", "fires_at", "holds_at"),
    [("gain", 110.0, 109.0), ("loss", 90.0, 91.0)],
)
def test_a_percent_trigger_fires_at_that_percent_from_creation(gtt, condition, fires_at, holds_at):
    trigger, _ = created(gtt, symbol="SPY", condition=condition, value=10)
    assert trigger.action_config["reference_price"] == 100.0

    evaluator = TriggerEvaluator()

    assert evaluator.evaluate_trigger(trigger, fires_at) is True
    assert evaluator.evaluate_trigger(trigger, holds_at) is False


def test_no_price_means_no_trigger_that_could_never_fire(gtt, monkeypatch):
    monkeypatch.setattr(
        "src.trading.utils.unified_price_fetcher.get_current_price", lambda symbol: None
    )

    assert "Could not get SPY's price" in refused(symbol="SPY", condition="gain", value=10)
    assert gtt.get_triggers(enabled_only=False, active_only=False) == []


# One unit


def test_trailing_takes_a_percent_like_gain_and_loss(gtt):
    trigger, result = created(gtt, symbol="SPY", condition="trailing", value=5)

    assert trigger.trigger_value == pytest.approx(0.05)
    assert "trailing_stop 5%" in result["message"]
    assert "@ 5%" in gtt_tools._trigger_lines(trigger.to_dict())[0]


def test_a_saved_trailing_stop_shows_as_a_percent(gtt):
    trigger = gtt.create_trigger(
        symbol="SPY",
        condition_type=ConditionType.TRAILING_STOP,
        trigger_value=0.0375,
        action_type=ActionType.PLACE_ORDER,
        action_config={"current_stop": 744.0},
    )

    assert "@ 3.75%" in gtt_tools._trigger_lines(trigger.to_dict())[0]


def test_a_price_shows_as_a_price(gtt):
    trigger, _ = created(gtt, symbol="SPY", condition="above", value=620.5)

    assert "@ 620.5" in gtt_tools._trigger_lines(trigger.to_dict())[0]


@pytest.mark.parametrize(
    ("condition", "value"),
    [("gain", 0), ("gain", -5), ("gain", 100), ("trailing", 150), ("above", 0), ("below", -1)],
)
def test_a_value_out_of_range_is_refused(gtt, condition, value):
    assert "Invalid value" in refused(symbol="SPY", condition=condition, value=value)


def test_the_help_shows_percents():
    usage = slash_commands._gtt_create_trigger("")

    assert "gain 10 " in usage
    assert "trailing 5 " in usage
    assert "0.10" not in usage
    assert "for symbols you hold" in usage


def test_a_typed_gtt_create_can_only_make_an_alert(gtt):
    message = slash_commands._gtt_create_trigger("SPY below 700 sell it")

    assert message.startswith("[OK]")
    trigger = gtt.get_triggers(symbol="SPY")[0]
    assert trigger.action_type == ActionType.ALERT.value


# The bridge leaves a user's trigger alone


def test_syncing_a_position_doesnt_take_over_a_users_trailing_trigger(gtt):
    user_trigger, _ = created(gtt, symbol="SPY", condition="trailing", value=5)

    state = StopState(symbol="SPY", entry_price=657.6, current_stop=744.47, quantity=14)
    state.highest_price_seen = 773.43

    saved = bridge.sync_trailing_stop_to_gtt("SPY", state)

    assert saved.id != user_trigger.id
    assert gtt.get_trigger(user_trigger.id).action_config == {}


def evaluated_user_trigger(gtt):
    """A user's trailing trigger after a routine has evaluated it, which writes its state."""
    trigger, _ = created(gtt, symbol="NVDA", condition="trailing", value=5)
    gtt.update_trailing_stop(trigger.id, new_highest=200.0, new_stop=190.0)
    return gtt.get_trigger(trigger.id)


def test_an_evaluated_user_trigger_is_still_the_users(gtt):
    trigger = evaluated_user_trigger(gtt)
    assert "current_stop" in trigger.action_config  # what evaluation leaves behind

    state = StopState(symbol="NVDA", entry_price=150.0, current_stop=170.0, quantity=2)
    state.highest_price_seen = 180.0
    saved = bridge.sync_trailing_stop_to_gtt("NVDA", state)

    assert saved.id != trigger.id


def test_a_closed_position_doesnt_remove_an_evaluated_user_trigger(gtt):
    trigger = evaluated_user_trigger(gtt)

    assert bridge.cleanup_orphaned_trailing_stops([]) == {"removed": 0}
    assert gtt.get_trigger(trigger.id) is not None


def test_a_positions_saved_state_is_removed_once_closed(gtt):
    state = StopState(symbol="QQQ", entry_price=616.8, current_stop=698.77, quantity=3)
    state.highest_price_seen = 726.1
    saved = bridge.sync_trailing_stop_to_gtt("QQQ", state)

    assert bridge.cleanup_orphaned_trailing_stops([]) == {"removed": 1}
    assert gtt.get_trigger(saved.id) is None
