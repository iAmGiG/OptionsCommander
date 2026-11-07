"""GTT alerts on symbols you don't hold are checked from a snapshot; nothing else runs (#658)."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.trading.gtt import trailing_stop_bridge as bridge
from src.trading.gtt.gtt_manager import ActionType, ConditionType, GTTManager
from src.trading.gtt.trigger_evaluator import TriggerEvaluator
from src.trading.scheduling import trading_cycle
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle


def _trade(price, age_minutes=1):
    stamp = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return {"latest_trade": {"p": price, "t": stamp.isoformat()}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A cycle with a real GTTManager on a temp db, a fake snapshot and a fake executor."""
    manager = GTTManager(db_path=str(tmp_path / "gtt.db"))
    evaluator = TriggerEvaluator()
    evaluator.gtt_manager = manager
    executor = MagicMock()
    executor.execute_triggers_batch.side_effect = lambda fired, prices: [
        SimpleNamespace(success=True, trigger=t, details={}) for t in fired
    ]
    monkeypatch.setattr(trading_cycle, "GTT_AVAILABLE", True)
    monkeypatch.setattr(trading_cycle, "is_market_hours", lambda: True)  # the evening routine
    monkeypatch.setattr(trading_cycle, "get_trigger_evaluator", lambda: evaluator, raising=False)
    monkeypatch.setattr(trading_cycle, "get_action_executor", lambda: executor, raising=False)
    cycle = object.__new__(CostEfficientTradeCycle)
    cycle.market_data = MagicMock()
    cycle.market_data.get_snapshot.return_value = {"AAPL": _trade(205.0)}
    return SimpleNamespace(
        manager=manager, cycle=cycle, executor=executor, snapshot=cycle.market_data.get_snapshot
    )


def _alert(world, symbol="AAPL", condition=ConditionType.PRICE_ABOVE, value=200.0):
    return world.manager.create_trigger(symbol, condition, value, ActionType.ALERT)


def _executed(world, results):
    return [r.trigger.id for r in results if hasattr(r, "trigger")]


EMPTY = {"positions": {}, "orders": {}}


def test_an_alert_fires_at_a_fresh_price_above_its_level(world):
    trigger = _alert(world)

    results = world.cycle.check_gtt_triggers(EMPTY)  # nothing held: still checked

    assert _executed(world, results) == [trigger.id]
    world.snapshot.assert_called_once_with(["AAPL"])


def test_an_alert_does_not_fire_when_the_price_does_not_cross(world):
    _alert(world, value=250.0)

    assert world.cycle.check_gtt_triggers(EMPTY) == []
    world.executor.execute_triggers_batch.assert_not_called()


def test_a_below_alert_fires_under_its_level(world):
    trigger = _alert(world, condition=ConditionType.PRICE_BELOW, value=210.0)

    assert _executed(world, world.cycle.check_gtt_triggers(EMPTY)) == [trigger.id]


def test_one_snapshot_prices_all_the_symbols(world):
    _alert(world, "AAPL")
    _alert(world, "MSFT")
    world.snapshot.return_value = {"AAPL": _trade(205.0), "MSFT": _trade(300.0)}

    world.cycle.check_gtt_triggers(EMPTY)

    world.snapshot.assert_called_once_with(["AAPL", "MSFT"])


def test_a_stale_last_trade_is_skipped_and_logged(world, caplog):
    _alert(world)
    world.snapshot.return_value = {"AAPL": _trade(205.0, age_minutes=16 * 60)}

    with caplog.at_level(logging.INFO):
        results = world.cycle.check_gtt_triggers(EMPTY)

    assert results == []
    assert any("AAPL" in r.message and "stale" in r.message for r in caplog.records)
    assert all(r.levelno == logging.INFO for r in caplog.records if "stale" in r.message)


def test_a_missing_symbol_is_ignored(world):
    _alert(world)
    world.snapshot.return_value = {}

    assert world.cycle.check_gtt_triggers(EMPTY) == []


def test_an_order_trigger_is_neither_evaluated_nor_executed(world):
    order = world.manager.create_trigger(
        "AAPL",
        ConditionType.PRICE_ABOVE,
        200.0,
        ActionType.PLACE_ORDER,
        {"side": "buy", "qty": 5, "order_type": "market"},
    )

    results = world.cycle.check_gtt_triggers(EMPTY)

    world.executor.execute_triggers_batch.assert_not_called()
    assert [(r.trigger_id, r.success, r.details["skipped"]) for r in results] == [
        (order.id, False, True)
    ]
    assert results[0].message == "AAPL not held: only alerts run for symbols you don't hold"
    assert world.manager.get_trigger(order.id).trigger_count == 0


def test_a_positions_trailing_state_is_not_evaluated(world, monkeypatch):
    state = world.manager.create_trigger(
        "AAPL",
        ConditionType.TRAILING_STOP,
        0.05,
        ActionType.ALERT,
        {"entry_price": 190.0, "highest_price": 190.0, "current_stop": 180.0},
    )
    assert bridge.holds_position_state(state)

    results = world.cycle.check_gtt_triggers(EMPTY)  # AAPL trades at 205

    assert results == []
    saved = world.manager.get_trigger(state.id)
    assert saved.action_config["highest_price"] == 190.0
    world.snapshot.assert_not_called()


def test_a_users_trailing_alert_on_an_unheld_symbol_is_evaluated(world):
    trigger = world.manager.create_trigger(
        "AAPL", ConditionType.TRAILING_STOP, 0.05, ActionType.ALERT, {"highest_price": 190.0}
    )

    world.cycle.check_gtt_triggers(EMPTY)

    assert world.manager.get_trigger(trigger.id).action_config["highest_price"] == 205.0


def test_held_symbols_are_left_to_the_held_path(world):
    _alert(world, "SPY", value=100.0)
    held = {"positions": {"SPY": {"quantity": 3, "current_price": 730.0, "side": "long"}}}

    world.cycle.check_gtt_triggers(held)

    world.snapshot.assert_not_called()  # nothing unheld to price


@pytest.mark.parametrize("failure", [RuntimeError("boom"), {}])
def test_a_failed_snapshot_leaves_the_held_results_intact(world, failure):
    held_alert = _alert(world, "SPY", value=100.0)
    _alert(world)
    if isinstance(failure, Exception):
        world.snapshot.side_effect = failure
    else:
        world.snapshot.return_value = failure
    held = {"positions": {"SPY": {"quantity": 3, "current_price": 730.0, "side": "long"}}}

    results = world.cycle.check_gtt_triggers(held)

    assert _executed(world, results) == [held_alert.id]


def test_a_failed_evaluation_does_not_raise(world, monkeypatch):
    _alert(world)
    monkeypatch.setattr(
        TriggerEvaluator,
        "evaluate_triggers_batch",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    assert world.cycle.check_gtt_triggers(EMPTY) == []


def test_a_dry_run_executes_nothing_for_unheld_alerts(world):
    trigger = _alert(world)
    world.cycle._dry_run_active = True

    results = world.cycle.check_gtt_triggers(EMPTY)

    world.executor.execute_triggers_batch.assert_not_called()
    assert [(r.trigger_id, r.details["dry_run"]) for r in results] == [(trigger.id, True)]


def test_without_market_data_the_unheld_path_is_skipped(world):
    _alert(world)
    world.cycle.market_data = None

    assert world.cycle.check_gtt_triggers(EMPTY) == []
    assert CostEfficientTradeCycle.market_data is None


def test_nothing_is_checked_while_the_market_is_closed(world, monkeypatch):
    """The 09:20 routine: IEX has no pre-market prints, so no snapshot call is made."""
    monkeypatch.setattr(trading_cycle, "is_market_hours", lambda: False)
    _alert(world)

    assert world.cycle.check_gtt_triggers(EMPTY) == []
    world.snapshot.assert_not_called()


def test_an_order_trigger_whose_condition_isnt_met_is_not_reported(world):
    world.manager.create_trigger(
        "AAPL",
        ConditionType.PRICE_ABOVE,
        250.0,
        ActionType.PLACE_ORDER,
        {"side": "buy", "qty": 5, "order_type": "market"},
    )

    assert world.cycle.check_gtt_triggers(EMPTY) == []
    world.executor.execute_triggers_batch.assert_not_called()


def test_one_unreadable_trade_doesnt_silence_the_others(world):
    msft = _alert(world, symbol="MSFT", value=400.0)
    _alert(world)
    world.snapshot.return_value = {
        "AAPL": {"latest_trade": {"p": 205.0, "t": "not a time"}},
        "MSFT": _trade(410.0),
    }

    assert _executed(world, world.cycle.check_gtt_triggers(EMPTY)) == [msft.id]
