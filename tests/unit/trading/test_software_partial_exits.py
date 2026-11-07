"""
Software partial exits (#584): the evening routine sells target 1 of a saved plan once the
price is at it; the morning routine only reports it. The broker is a fake, and the plans
live in the test state dir: nothing here sends an order.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.trading.broker.alpaca_execution_manager as execution_module
import src.trading.scheduling.trading_cycle as cycle_module
from src.trading.orders.partial_exit_manager import (
    ExitTarget,
    PartialExitState,
    load_partial_exit_manager,
)
from src.trading.scheduling.notices import CRITICAL, WARNING, NoticeStore
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle

STOP = {
    "id": "stop-1",
    "side": "sell",
    "status": "new",
    "order_type": "stop",
    "stop_price": 95.0,
    "qty": 10.0,
    "legs": [],
}


def position(symbol="AAPL", quantity=10, entry=100.0, price=105.0):
    return {
        "symbol": symbol,
        "quantity": quantity,
        "entry_price": entry,
        "current_price": price,
        "side": "long",
    }


def broker_state(**positions):
    return {"positions": positions, "orders": {}, "account": {}}


def save_plan(symbol="AAPL", entry=100.0, total=10, target=104.0, qty=5, **target_fields):
    manager = load_partial_exit_manager()
    manager.exit_states[symbol] = PartialExitState(
        symbol=symbol,
        entry_price=entry,
        total_quantity=total,
        stop_price=95.0,
        targets=[
            ExitTarget(1, qty, 0.5, "software", exit_price=target, **target_fields),
            ExitTarget(2, total - qty, 0.5, "trailing"),
        ],
    )
    manager.save()


def saved_target(symbol="AAPL"):
    return load_partial_exit_manager().exit_states[symbol].targets[0]


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: True)
    monkeypatch.setattr(execution_module, "is_market_hours", lambda: True)
    monkeypatch.setattr(execution_module, "_RELEASE_WAIT_SECONDS", 0)


@pytest.fixture
def broker():
    """A broker holding 10 AAPL under one stop; a cancel takes effect straight away."""
    fake = MagicMock()
    fake.open = [dict(STOP)]
    fake.get_positions.return_value = [
        {"symbol": "AAPL", "qty": "10", "side": "long", "qty_available": "0"}
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
def cycle(broker):
    c = object.__new__(CostEfficientTradeCycle)  # no broker clients
    c.order_manager = broker
    c.trailing_stop_manager = None
    c._dry_run_active = False
    c.local_state_manager = SimpleNamespace(
        state={"positions": {"AAPL": {"quantity": 10, "entry_price": 100.0}}}
    )
    c.fetch_broker_state = MagicMock(return_value=broker_state(AAPL=position(quantity=5)))
    c._start_routine()
    return c


def sells(broker):
    return broker.place_market_order.call_args_list


# Selling


def test_the_evening_routine_sells_target_1_at_the_target(cycle, broker):
    save_plan()

    state, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    broker.place_market_order.assert_called_once_with(symbol="AAPL", qty=5, side="sell")
    assert broker.cancel_order.call_args.args == ("stop-1",)
    broker.place_stop_order.assert_called_once()  # the stop goes back, on the other 5
    assert broker.place_stop_order.call_args.kwargs["qty"] == 5
    assert any("sold 5 shares" in line for line in lines)
    assert cycle.last_partial_exits == ()
    assert state["positions"]["AAPL"]["quantity"] == 5  # fetched again after the sale
    cycle.fetch_broker_state.assert_called_once_with(force_refresh=True)


def test_the_order_id_is_saved_and_local_quantity_updated(cycle):
    save_plan()

    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert saved_target().order_id == "sell-1"
    assert cycle.local_state_manager.state["positions"]["AAPL"]["quantity"] == 5


def test_nothing_sells_below_the_target(cycle, broker):
    save_plan(target=106.0)

    state, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()
    cycle.fetch_broker_state.assert_not_called()
    assert lines == []


def test_the_morning_routine_reports_and_never_sells(cycle, broker):
    save_plan()

    _, lines = cycle._partial_exits(broker_state(AAPL=position()))

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()
    assert any(
        "AAPL reached its partial target $104.00; the evening routine sells 5 shares" in line
        for line in lines
    )


def test_after_hours_the_evening_routine_does_not_sell(cycle, broker, monkeypatch):
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: False)
    save_plan()

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []
    assert any("market is closed" in line for line in lines)


def test_a_position_no_larger_than_the_target_is_not_sold(cycle, broker):
    save_plan()
    cycle._dry_run_active = True  # a real run drops such a plan first (shares sold elsewhere)

    _, lines = cycle._partial_exits(broker_state(AAPL=position(quantity=5)), evening=True)

    assert sells(broker) == []
    assert any("would close the position" in line for line in lines)


def test_a_retry_after_an_accepted_sale_does_not_sell_again(cycle, broker):
    save_plan()
    cycle._partial_exits(broker_state(AAPL=position()), evening=True)
    broker.reset_mock()

    # The scheduler re-runs the routine: the broker still shows the shares as held
    cycle._start_routine()
    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()


def test_a_plan_removed_by_the_cli_meanwhile_is_not_sold(cycle, broker, monkeypatch):
    save_plan()
    real = cycle_module.CostEfficientTradeCycle._partial_upkeep

    def removed_first(self, manager, state):
        load_partial_exit_manager().remove_position("AAPL")  # /partial remove, in the CLI
        return real(self, manager, state)

    monkeypatch.setattr(CostEfficientTradeCycle, "_partial_upkeep", removed_first)

    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []


def test_a_target_changed_by_the_cli_meanwhile_uses_the_saved_price(cycle, broker, monkeypatch):
    save_plan()
    real = cycle_module.CostEfficientTradeCycle._partial_upkeep

    def raised_first(self, manager, state):
        load_partial_exit_manager().update_target_price("AAPL", 1, 110.0)
        return real(self, manager, state)

    monkeypatch.setattr(CostEfficientTradeCycle, "_partial_upkeep", raised_first)

    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []


# A failed sale


def test_a_failed_sale_puts_the_stop_back_and_is_recorded(cycle, broker):
    save_plan()
    broker.place_market_order.return_value = {"status": "error", "message": "rejected"}

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert any("FAILED" in line for line in lines)
    broker.place_stop_order.assert_called_once()
    assert broker.place_stop_order.call_args.kwargs["qty"] == 10
    (failure,) = cycle.last_partial_exits
    assert (failure.symbol, failure.quantity, failure.stop_lost) == ("AAPL", 5, False)
    assert saved_target().order_id is None  # nothing sold: tried again next evening
    assert cycle.local_state_manager.state["positions"]["AAPL"]["quantity"] == 10


def test_a_stop_that_cannot_be_put_back_is_recorded_as_lost(cycle, broker):
    save_plan()
    broker.place_market_order.return_value = {"status": "error", "message": "rejected"}
    broker.place_stop_order.return_value = {"status": "error", "message": "no"}

    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert cycle.last_partial_exits[0].stop_lost is True


def test_a_sale_whose_new_stop_failed_is_sold_and_recorded_as_lost(cycle, broker):
    save_plan()
    broker.place_stop_order.return_value = {"status": "error", "message": "no"}

    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert saved_target().order_id == "sell-1"
    assert cycle.last_partial_exits[0].stop_lost is True


def test_an_error_during_the_sale_is_contained(cycle, broker):
    save_plan()
    broker.get_orders.side_effect = RuntimeError("broker down")

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert any("FAILED" in line for line in lines)
    assert cycle.last_partial_exits[0].stop_lost is True


def test_the_routine_state_is_reset_each_routine(cycle, broker):
    save_plan()
    broker.place_market_order.return_value = {"status": "error", "message": "rejected"}
    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    cycle._start_routine()

    assert cycle.last_partial_exits == ()


# A dry run


def test_a_dry_run_reports_and_calls_nothing(cycle, broker):
    save_plan()
    cycle._dry_run_active = True

    state, lines = cycle._partial_exits(broker_state(AAPL=position(price=105.0)), evening=True)

    assert any("would sell 5 shares at about $105.00" in line for line in lines)
    assert broker.method_calls == []
    cycle.fetch_broker_state.assert_not_called()
    assert saved_target().order_id is None


def test_a_dry_run_after_hours_does_not_claim_a_sale(cycle, broker, monkeypatch):
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: False)
    save_plan()
    cycle._dry_run_active = True

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert not any("would sell" in line for line in lines)


def test_a_dry_run_changes_no_plan(cycle):
    save_plan(order_id="sell-0")
    cycle._dry_run_active = True
    cycle._new_fills = [{"id": "sell-0"}]

    cycle._partial_exits(broker_state(), evening=True)  # not held: would be removed

    assert saved_target().filled is False


# Upkeep


def test_a_target_is_filled_when_its_order_appears_in_the_new_fills(cycle):
    save_plan(order_id="sell-0")
    cycle._new_fills = [{"id": "sell-0"}]

    _, lines = cycle._partial_exits(broker_state(AAPL=position(quantity=5)))

    target = saved_target()
    assert target.filled is True and target.filled_at
    assert any("partial target 1 sold" in line for line in lines)


def test_another_fill_does_not_fill_it(cycle):
    save_plan(order_id="sell-0")
    cycle._new_fills = [{"id": "other"}]

    cycle._partial_exits(broker_state(AAPL=position(quantity=10)))

    assert saved_target().filled is False


def test_a_plan_for_a_symbol_no_longer_held_is_removed(cycle):
    save_plan()
    save_plan("MSFT")

    _, lines = cycle._partial_exits(broker_state(AAPL=position()))

    assert "MSFT" not in load_partial_exit_manager().exit_states
    assert "AAPL" in load_partial_exit_manager().exit_states
    assert any("MSFT: no longer held" in line for line in lines)


def test_a_moved_average_entry_drops_the_plan(cycle, broker):
    save_plan()

    _, lines = cycle._partial_exits(
        broker_state(AAPL=position(entry=101.5, price=120.0)), evening=True
    )

    assert load_partial_exit_manager().exit_states == {}
    assert sells(broker) == []
    assert any("average entry moved" in line for line in lines)


def test_the_rounded_entry_keeps_the_plan(cycle):
    save_plan()

    cycle._partial_exits(broker_state(AAPL=position(entry=100.04)))

    assert "AAPL" in load_partial_exit_manager().exit_states


def disk_writes_fail(monkeypatch):
    """Saving a plan fails from here on (the error is contained, as in production)."""
    from src.trading.orders.partial_exit_manager import PartialExitManager

    def refuse(self, plans):
        raise OSError("disk full")

    monkeypatch.setattr(PartialExitManager, "_write_plans", refuse)


def test_a_failed_save_is_reported_and_the_next_evening_does_not_sell(cycle, broker, monkeypatch):
    save_plan()
    with monkeypatch.context() as failing:
        disk_writes_fail(failing)
        _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert any("NOT saved" in line for line in lines)
    (failure,) = cycle.last_partial_exits
    assert failure.critical is True and failure.stop_lost is False
    assert "could sell again" in failure.reason
    assert saved_target().order_id is None  # the disk has no ID; memory did
    broker.reset_mock()

    # Next evening: the broker now shows the shares sold, so the plan no longer fits
    cycle._start_routine()
    _, lines = cycle._partial_exits(broker_state(AAPL=position(quantity=5)), evening=True)

    assert sells(broker) == []
    assert any("holds 5 shares where the plan expects 10" in line for line in lines)


def test_an_exception_mid_sale_sets_the_sentinel_and_blocks_a_resale(cycle, broker):
    save_plan()
    broker.get_orders.side_effect = RuntimeError("broker down")

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert saved_target().order_id == "unconfirmed"
    (failure,) = cycle.last_partial_exits
    assert failure.critical and failure.stop_lost
    assert "/partial remove AAPL, then /partial set AAPL" in failure.reason
    assert any("will not sell again" in line for line in lines)
    broker.get_orders.side_effect = lambda **kwargs: list(broker.open)
    broker.reset_mock()
    cycle._start_routine()

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()
    assert any("never confirmed" in line for line in lines)


def test_an_exception_with_a_failing_save_says_so(cycle, broker, monkeypatch):
    save_plan()
    broker.get_orders.side_effect = RuntimeError("broker down")
    disk_writes_fail(monkeypatch)

    cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert "COULD NOT BE SAVED" in cycle.last_partial_exits[0].reason


# Share counts, pending sales and stale plans


def test_a_target_is_filled_when_the_position_shrank_by_its_shares(cycle):
    save_plan(order_id="sell-0")
    cycle._new_fills = []  # the fill was recorded by someone else earlier

    cycle._partial_exits(broker_state(AAPL=position(quantity=5)))

    assert saved_target().filled is True
    assert "AAPL" in load_partial_exit_manager().exit_states  # 5 left is what the plan expects


def test_a_pending_sale_is_reported_on_every_run(cycle):
    save_plan(order_id="sell-0")

    for _ in range(2):
        cycle._start_routine()
        _, lines = cycle._partial_exits(broker_state(AAPL=position()))
        assert any("order sell-0 for AAPL shows no fill yet" in line for line in lines)
        assert cycle.last_partial_exits == ()  # fresh: no notice yet
    assert saved_target().filled is False


def test_a_stale_pending_sale_raises_a_warning(cycle, monkeypatch):
    manager = load_partial_exit_manager()
    save_plan(order_id="sell-0")
    manager.refresh_plan("AAPL").last_updated = "2026-11-02T15:50:00"
    manager.save("AAPL")
    monkeypatch.setattr(
        cycle_module, "market_today", lambda: "2026-11-03"
    )  # a trading day later: fine

    cycle._partial_exits(broker_state(AAPL=position()))
    assert cycle.last_partial_exits == ()

    monkeypatch.setattr(cycle_module, "market_today", lambda: "2026-11-04")
    _, lines = cycle._partial_exits(broker_state(AAPL=position()))

    (failure,) = cycle.last_partial_exits
    assert (failure.critical, failure.stop_lost) == (False, False)
    assert "cancelled or rejected?" in lines[-1]


def test_a_stale_pending_sale_notice_is_a_warning(cycle, tmp_path):
    from src.trading.scheduling.trading_cycle import PartialExitFailure

    cycle.last_partial_exits = (PartialExitFailure("AAPL", 5, "no fill", False),)
    scheduler = scheduler_with(cycle, tmp_path)

    scheduler._notice_positions("2026-11-09", run_completed=True)

    assert scheduler.notice_store.open_notices()[0].severity == WARNING


def test_an_add_on_drops_the_plan(cycle, broker):
    save_plan()

    _, lines = cycle._partial_exits(broker_state(AAPL=position(quantity=14)), evening=True)

    assert load_partial_exit_manager().exit_states == {}
    assert sells(broker) == []
    assert any("holds 14 shares where the plan expects 10" in line for line in lines)


def test_a_pending_sale_keeps_a_plan_whose_shares_have_not_moved(cycle):
    save_plan(order_id="sell-0")

    cycle._partial_exits(broker_state(AAPL=position(quantity=10)))

    assert "AAPL" in load_partial_exit_manager().exit_states


# No plans


def test_with_no_plans_nothing_is_touched():
    c = object.__new__(CostEfficientTradeCycle)  # no order manager, as several tests build it
    c._dry_run_active = False
    c._start_routine()
    state = broker_state(AAPL=position())
    c.fetch_broker_state = MagicMock()

    assert c._partial_exits(state, evening=True) == (state, [])
    c.fetch_broker_state.assert_not_called()
    assert c.last_partial_exits == ()


def test_an_unreadable_plans_file_is_set_aside_and_nothing_sells(cycle, broker):
    from src.trading.orders.partial_exit_manager import default_state_file

    default_state_file().parent.mkdir(parents=True, exist_ok=True)
    default_state_file().write_text('{"positions": {"AAPL": {"symbol": "AAPL"}}}')

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == [] and lines == []
    assert list(default_state_file().parent.glob("partial_exits.json.unreadable-*"))


def test_an_error_in_the_step_does_not_fail_the_routine(cycle, monkeypatch):
    import src.trading.orders.partial_exit_manager as manager_module

    def broken():
        raise RuntimeError("disk")

    monkeypatch.setattr(manager_module, "load_partial_exit_manager", broken)
    state = broker_state(AAPL=position())

    result, lines = cycle._partial_exits(state, evening=True)

    assert result is state
    assert any("Partial exits not checked: disk" in line for line in lines)


# Through the routines


def stub_routine(c, state):
    c.fetch_broker_state = MagicMock(return_value=state)
    c.record_daily = lambda name, broker_state: []
    c.reconcile_state = MagicMock(return_value=[])
    c.check_and_repair_stops = MagicMock(return_value=[])
    c.check_gtt_triggers = MagicMock(return_value=[])
    c._after_gtt_actions = lambda broker_state, results: broker_state
    c.raise_stops = MagicMock(return_value=([], None))
    c._position_alerts = MagicMock(return_value=([], []))
    c._sync_trailing_stops_to_gtt = MagicMock()
    c.local_state_manager.save = MagicMock()
    c.generate_routine_report = MagicMock(return_value="report")
    c._append_report_sections = MagicMock(side_effect=lambda text, *args: text)
    c._save_report = lambda name, text: (text, f"{name}.md")
    c._settle_closed_positions = lambda broker_state, discrepancies: discrepancies
    c.dry_run = False
    c._daily_records = None


def test_the_evening_routine_sells_before_it_raises_stops(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))
    order = []
    cycle.raise_stops = MagicMock(side_effect=lambda s: order.append("raise") or ([], None))
    broker.place_market_order.side_effect = lambda **kw: (
        order.append("sell") or {"status": "submitted", "order_id": "sell-1"}
    )

    cycle.evening_routine(raise_errors=True)

    assert order == ["sell", "raise"]
    sections = cycle._append_report_sections.call_args.args[2]
    assert any("sold 5 shares" in line for line in sections)


def test_the_morning_routine_never_sells(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))

    cycle.morning_routine(raise_errors=True)

    assert sells(broker) == []
    sections = cycle._append_report_sections.call_args.args[2]
    assert any("the evening routine sells 5 shares" in line for line in sections)


def test_the_dry_run_routine_reports_a_would_sell(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))
    broker.reset_mock()

    cycle.evening_routine(raise_errors=True, dry_run=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()
    sections = cycle._append_report_sections.call_args.args[2]
    assert any("would sell 5 shares" in line for line in sections)


# The scheduler's notice


def scheduler_with(cycle, tmp_path):
    from src.trading.scheduling.daily_scheduler import DailyScheduler

    scheduler = object.__new__(DailyScheduler)
    scheduler._trading_cycle = cycle
    scheduler.notice_store = NoticeStore(str(tmp_path / "notices.json"))
    return scheduler


@pytest.mark.parametrize("stop_lost, severity", [(True, CRITICAL), (False, WARNING)])
def test_a_failed_sale_raises_a_notice(cycle, tmp_path, stop_lost, severity):
    from src.trading.scheduling.notices import PARTIAL_EXIT_FAILED
    from src.trading.scheduling.trading_cycle import PartialExitFailure

    cycle.last_partial_exits = (PartialExitFailure("AAPL", 5, "rejected", stop_lost),)
    scheduler = scheduler_with(cycle, tmp_path)

    scheduler._notice_positions("2026-11-09", run_completed=True)

    (notice,) = scheduler.notice_store.open_notices()
    assert notice.key == f"{PARTIAL_EXIT_FAILED}:AAPL:2026-11-09"
    assert notice.severity == severity
    assert notice.symbol == "AAPL"
