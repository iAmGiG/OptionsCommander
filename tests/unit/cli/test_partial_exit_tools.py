"""
/partial works against saved plans (#524, #584).

The CLI used to build PartialExitManager() without its order manager (so every
command failed), read a `positions` attribute that doesn't exist, and saw no plan
saved by another process. Plans now live in state/partial_exits.json (the test state
dir here). No orders are placed.
"""

import json
from unittest.mock import MagicMock

import pytest

import src.cli.tools.partial_exit_tools as tools
from src.cli.commands.slash_commands import _partial_handle_modify
from src.trading.orders.partial_exit_manager import (
    ExitTarget,
    PartialExitManager,
    PartialExitState,
    default_state_file,
    load_partial_exit_manager,
)


def _save_plan(order_id=None, filled=False):
    manager = load_partial_exit_manager()
    manager.exit_states["AAPL"] = PartialExitState(
        symbol="AAPL",
        entry_price=150.0,
        total_quantity=10,
        stop_price=142.5,
        targets=[
            ExitTarget(1, 5, 0.5, "limit", exit_price=156.0, order_id=order_id, filled=filled),
            ExitTarget(2, 5, 0.5, "trailing"),
        ],
    )
    manager.save()


def test_plans_round_trip_through_the_state_dir(isolated_state_dir):
    _save_plan(order_id="tp-1")

    assert default_state_file().parent == isolated_state_dir
    reloaded = load_partial_exit_manager().exit_states["AAPL"]
    assert reloaded.targets[0].order_id == "tp-1"
    assert reloaded.get_remaining_quantity() == 10


def test_no_plans_is_not_an_error():
    assert tools.show_all_partial_exits() == tools.NO_PLANS
    assert tools.get_exit_summary() == "[INFO] No partial exit positions registered."


def test_views_show_a_saved_plan():
    _save_plan()

    everything = tools.show_all_partial_exits()
    assert "[AAPL] 10 shares @ $150.00" in everything
    assert "Limit @ $156.00 (+4.0% gain)" in everything
    assert "Trailing Stop" in everything

    plan = tools.show_partial_exit_plan("aapl")
    assert "Partial Exit Plan: AAPL" in plan
    assert "Stop Price: $142.50" in plan
    assert "[ACTIVE] Target 1: 5 shares" in plan

    assert "Target 2: 5 shares" in tools.show_exit_targets("AAPL")
    assert "[AAPL] 2 target(s), 10 shares remaining" in tools.list_active_exits()
    assert "Active Targets: 2" in tools.get_exit_summary()


def test_modify_saves_the_new_price():
    _save_plan()

    output = _partial_handle_modify("AAPL 1 160")

    assert output.startswith("[OK] Modified AAPL target 1")
    assert load_partial_exit_manager().exit_states["AAPL"].targets[0].exit_price == 160.0


@pytest.mark.parametrize(
    "plan, args, expected",
    [
        ({"order_id": "tp-1"}, ("AAPL", 1, 160.0), "has a live order"),
        ({"filled": True}, ("AAPL", 1, 160.0), "already been filled"),
        ({}, ("AAPL", 2, 160.0), "trailing target, not a limit"),
        ({}, ("AAPL", 1, 0.0), "must be a positive number"),
        ({}, ("AAPL", 1, float("nan")), "must be a positive number"),
        ({}, ("MSFT", 1, 160.0), "No partial exit plan found for MSFT"),
    ],
)
def test_modify_refusals(plan, args, expected):
    _save_plan(**plan)

    output = tools.modify_exit_target(*args)

    assert output.startswith("[ERROR]")
    assert expected in output
    assert load_partial_exit_manager().exit_states["AAPL"].targets[0].exit_price == 156.0


def test_unreadable_plans_file_is_moved_aside_not_overwritten():
    default_state_file().parent.mkdir(parents=True, exist_ok=True)
    default_state_file().write_text("{not json")

    manager = load_partial_exit_manager()
    assert manager.exit_states == {}

    aside = list(default_state_file().parent.glob("partial_exits.json.unreadable-*"))
    assert len(aside) == 1 and aside[0].read_text() == "{not json"


def test_saving_one_plan_keeps_plans_saved_by_another_process():
    trading = load_partial_exit_manager()  # loaded before the CLI saves AAPL
    _save_plan()
    trading.exit_states["MSFT"] = PartialExitState(
        symbol="MSFT", entry_price=400.0, total_quantity=4, stop_price=380.0, targets=[]
    )

    trading.save("MSFT")

    saved = json.loads(default_state_file().read_text())["positions"]
    assert set(saved) == {"AAPL", "MSFT"}


def test_fill_update_does_not_undo_a_cli_edit():
    manager = load_partial_exit_manager()
    manager.exit_states["AAPL"] = PartialExitState(
        symbol="AAPL",
        entry_price=150.0,
        total_quantity=10,
        stop_price=142.5,
        targets=[
            ExitTarget(1, 5, 0.5, "limit", exit_price=156.0),
            ExitTarget(2, 5, 0.5, "limit", exit_price=165.0, order_id="tp-2"),
        ],
    )
    manager.save()
    trading = PartialExitManager(order_manager=MagicMock(), state_file=default_state_file())
    trading.order_manager.position_manager.get_order.return_value = {"status": "filled"}

    tools.modify_exit_target("AAPL", 1, 160.0)  # the CLI, in another process
    trading.update_position_fills("AAPL")  # its in-memory copy still says 156

    saved = load_partial_exit_manager().exit_states["AAPL"]
    assert saved.targets[0].exit_price == 160.0
    assert saved.targets[1].filled


def test_factory_saves_plans_where_the_cli_reads_them():
    mode_manager = MagicMock()
    manager = PartialExitManager.from_mode_manager(MagicMock(), None, mode_manager)

    assert manager.state_file == default_state_file()


def test_short_target_shows_a_gain_and_unpriced_limit_is_not_trailing():
    manager = load_partial_exit_manager()
    manager.exit_states["TSLA"] = PartialExitState(
        symbol="TSLA",
        entry_price=150.0,
        total_quantity=4,
        stop_price=157.5,
        side="short",
        targets=[
            ExitTarget(1, 2, 0.5, "limit", exit_price=144.0),
            ExitTarget(2, 2, 0.5, "limit", exit_price=None),
        ],
    )
    manager.save()

    targets = tools.show_exit_targets("TSLA")

    assert "Limit @ $144.00 (+4.0% gain)" in targets
    assert "Limit (no price set)" in targets
    assert "Trailing Stop" not in targets


def test_malformed_plan_gives_an_error_reply_not_an_exception():
    plan = {
        "symbol": "AAPL",
        "entry_price": 150.0,
        "total_quantity": 10,
        "stop_price": None,  # can't be formatted as a price
        "targets": [],
    }
    default_state_file().parent.mkdir(parents=True, exist_ok=True)
    default_state_file().write_text(json.dumps({"positions": {"AAPL": plan}}))

    assert tools.show_all_partial_exits().startswith("[ERROR] Failed to retrieve partial exits")
    assert tools.show_partial_exit_plan("AAPL").startswith("[ERROR]")


# /partial set and remove (#584)


def _held(symbol="AAPL", qty=10.0, entry=100.0, value=1000.0, side="long"):
    return {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "avg_entry_price": entry,
        "market_value": value,
    }


def _no(question):
    raise AssertionError(f"asked: {question}")


def _mode(monkeypatch, pct, enabled=True):
    from src.core import trading_modes

    config = {"enabled": enabled, "split": [0.5, 0.5], "target_1_pct": pct}
    monkeypatch.setattr(
        trading_modes.get_mode_manager(), "get_partial_exit_config_dict", lambda mode=None: config
    )


@pytest.mark.parametrize(
    "mode, pct", [("conservative", 0.04), ("moderate", 0.05), ("aggressive", 0.06)]
)
def test_set_uses_the_saved_modes_percentage(mode, pct):
    from src.core.trading_modes import TradingMode, get_mode_manager

    get_mode_manager().set_mode(TradingMode(mode))

    reply = tools.set_partial_plan("aapl", [_held(qty=7.0, entry=200.0)], _no)

    assert reply.startswith("[OK]")
    state = load_partial_exit_manager().exit_states["AAPL"]
    assert state.entry_price == 200.0 and state.total_quantity == 7
    target = state.targets[0]
    assert (target.exit_type, target.quantity) == ("software", 3)
    assert target.exit_price == pytest.approx(200.0 * (1 + pct))
    assert state.targets[1].quantity == 4
    assert "No order was sent" in reply


@pytest.mark.parametrize(
    "positions, expected",
    [
        ([_held(qty=1.0, value=100.0)], "at least 2"),
        ([_held(qty=-5.0, side="short")], "short"),
        ([_held("MSFT")], "not held"),
        ([_held(qty=2.5)], "whole number"),
        (None, "Could not read"),
    ],
)
def test_set_refusals(positions, expected):
    reply = tools.set_partial_plan("AAPL", positions, _no)

    assert reply.startswith("[ERROR]") and expected in reply
    assert load_partial_exit_manager().exit_states == {}


def test_set_refuses_a_symbol_that_already_has_a_plan():
    _save_plan()

    reply = tools.set_partial_plan("AAPL", [_held()], _no)

    assert "already has a plan" in reply
    assert load_partial_exit_manager().exit_states["AAPL"].targets[0].exit_type == "limit"


def test_set_asks_when_the_price_is_already_at_the_target(monkeypatch):
    _mode(monkeypatch, 0.05)
    asked = []

    def decline(question):
        asked.append(question)
        return False

    reply = tools.set_partial_plan("AAPL", [_held(value=1100.0)], decline)  # $110 > $105

    assert "next evening routine would sell 5" in asked[0]
    assert reply.startswith("[INFO]")
    assert load_partial_exit_manager().exit_states == {}
    assert tools.set_partial_plan("AAPL", [_held(value=1100.0)], lambda q: True).startswith("[OK]")


def test_set_does_not_ask_below_the_target(monkeypatch):
    _mode(monkeypatch, 0.05)

    assert tools.set_partial_plan("AAPL", [_held(value=1040.0)], _no).startswith("[OK]")


def test_set_refuses_when_the_mode_has_partial_exits_off(monkeypatch):
    _mode(monkeypatch, 0.05, enabled=False)

    assert "switched off" in tools.set_partial_plan("AAPL", [_held()], _no)


def test_set_places_no_order(monkeypatch):
    from src.trading.orders import partial_exit_manager as module

    orders = MagicMock()
    monkeypatch.setattr(module.PartialExitManager, "_place_target_orders", orders)
    monkeypatch.setattr(module.PartialExitManager, "register_position", orders)

    tools.set_partial_plan("AAPL", [_held()], _no)

    orders.assert_not_called()


def test_remove_asks_then_drops_the_plan():
    _save_plan()

    assert tools.remove_partial_plan("aapl", lambda q: False).startswith("[INFO]")
    assert "AAPL" in load_partial_exit_manager().exit_states
    assert tools.remove_partial_plan("aapl", lambda q: True).startswith("[OK]")
    assert load_partial_exit_manager().exit_states == {}
    assert tools.remove_partial_plan("AAPL", _no).startswith("[INFO]")


def test_a_software_target_is_shown_as_one():
    manager = load_partial_exit_manager()
    manager.register_software_plan("AAPL", 100.0, 10, target_1_pct=0.05)

    text = tools.show_partial_exit_plan("AAPL")

    assert "Software target @ $105.00 (+5.0% gain)" in text
    assert "Sold by the evening routine" in text
    assert "Stop Price" not in text
    assert text.count("Trailing Stop") == 1  # target 2 only


def test_a_sold_software_target_shows_its_order():
    manager = load_partial_exit_manager()
    state = manager.register_software_plan("AAPL", 100.0, 10)
    state.targets[0].order_id = "sell-1"
    manager.save()

    assert "sold: order sell-1" in tools.show_partial_exit_plan("AAPL")


def test_modify_works_for_a_software_target():
    load_partial_exit_manager().register_software_plan("AAPL", 100.0, 10)

    reply = _partial_handle_modify("AAPL 1 110.50")

    assert reply.startswith("[OK]")
    assert load_partial_exit_manager().exit_states["AAPL"].targets[0].exit_price == 110.5


def test_the_slash_command_routes_set_and_remove(monkeypatch):
    from src.cli.commands import slash_commands

    printed = []
    monkeypatch.setattr(slash_commands, "safe_print", printed.append)
    session = MagicMock()
    session.account_monitor.get_positions.return_value = [_held(value=1000.0)]

    slash_commands.cmd_partial(session, "set aapl")
    slash_commands.cmd_partial(session, "set")
    monkeypatch.setattr(slash_commands, "_confirmed", lambda q: True)
    slash_commands.cmd_partial(session, "remove AAPL")
    session.account_monitor.get_positions.side_effect = RuntimeError("down")
    slash_commands.cmd_partial(session, "set AAPL")

    assert printed[0].startswith("[OK]")
    assert printed[1] == "Usage: /partial set <symbol>"
    assert printed[2].startswith("[OK] Removed")
    assert "Could not read the positions" in printed[3]
