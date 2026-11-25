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


def order(order_id, order_type, quantity, stop_price=None):
    """An open sell order as the fetched broker state lists it."""
    return {
        "id": order_id,
        "type": order_type,
        "side": "sell",
        "quantity": quantity,
        "stop_price": stop_price,
        "limit_price": None,
        "status": "new",
        "time_in_force": "gtc" if order_type == "stop" else "day",
        "created_at": "2026-11-20T15:00:00+00:00",
    }


MARKET_SELL = order("sell-0", "market", 5)  # a sale on its way: an earlier run's, or the CLI's


def protected_state(quantity=10, stop_quantity=5):
    """The broker state after a sale: the shares that stay are under a stop."""
    state = broker_state(AAPL=position(quantity=quantity))
    state["orders"] = {"AAPL": [order("stop-2", "stop", stop_quantity, stop_price=95.0)]}
    return state


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
    monkeypatch.setattr(cycle_module, "_SALE_SETTLE_SECONDS", 0, raising=False)


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


def test_an_open_market_sell_for_the_symbol_blocks_the_sale(cycle, broker):
    # A sale from an earlier attempt that the client reported as an error but the broker
    # accepted, or a sell from the CLI: selling again would close the position
    save_plan()
    state = {**broker_state(AAPL=position()), "orders": {"AAPL": [MARKET_SELL]}}

    _, lines = cycle._partial_exits(state, evening=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()
    cycle.fetch_broker_state.assert_not_called()
    assert any("market sell order for it is already open" in line for line in lines)
    assert cycle.last_partial_exits == ()


def test_a_changed_position_tries_no_sale(cycle, broker):
    save_plan()
    broker.get_positions.return_value = [
        {"symbol": "AAPL", "qty": "3", "side": "long", "qty_available": "0"}
    ]

    _, lines = cycle._partial_exits(broker_state(AAPL=position()), evening=True)

    assert sells(broker) == []
    assert any("the position changed" in line for line in lines)
    cycle.fetch_broker_state.assert_not_called()  # nothing went to the broker: no new state


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
    c.reconcile_state = MagicMock(side_effect=lambda state: [])  # a fresh list each routine
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
    stub_routine(cycle, protected_state())
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


# A sale that may have lost its stop: the stops are checked again in the same routine (E1-01)


def lost_stop_discrepancy(error=None):
    """What a second stop pass returns for AAPL: the stop placed, or not (an error)."""
    from src.trading.state.state_reconciler import Discrepancy

    return Discrepancy(
        type="MISSING_STOP",
        symbol="AAPL",
        details={"quantity": 5, "stop_price": 95.0, "reason": "no live stop order", "error": error},
        action="NEEDS_HUMAN_REVIEW" if error else "AUTO_FIXED",
        severity="HIGH",
    )


def numbered_fetches(cycle):
    """fetch_broker_state returns a new state on each call, numbered in order."""
    count = []

    def fetch(**kwargs):
        count.append(len(count) + 1)
        return {**broker_state(AAPL=position()), "fetch": count[-1]}

    cycle.fetch_broker_state = MagicMock(side_effect=fetch)
    return count


def stop_not_put_back(broker):
    broker.place_stop_order.return_value = {"status": "error", "message": "no"}


def sale_refused_and_stop_not_put_back(broker):
    broker.place_market_order.return_value = {"status": "error", "message": "rejected"}
    broker.place_stop_order.return_value = {"status": "error", "message": "no"}


def error_mid_sale(broker):
    broker.get_orders.side_effect = RuntimeError("broker down")


@pytest.mark.parametrize(
    "lose_the_stop", [stop_not_put_back, sale_refused_and_stop_not_put_back, error_mid_sale]
)
def test_a_sale_that_lost_its_stop_gets_a_second_stop_pass_on_fresh_state(
    cycle, broker, lose_the_stop
):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))
    fetches = numbered_fetches(cycle)
    lose_the_stop(broker)
    cycle.check_and_repair_stops.side_effect = [[], [lost_stop_discrepancy()]]
    sale_seen_at, real_cancel = [], broker.cancel_order.side_effect

    def cancel(order_id):  # the sale starts by cancelling the stop: note the fetch count then
        sale_seen_at.append(len(fetches))
        return real_cancel(order_id)

    broker.cancel_order.side_effect = cancel

    cycle.evening_routine(raise_errors=True)

    assert cycle.check_and_repair_stops.call_count == 2
    second_state = cycle.check_and_repair_stops.call_args_list[1].args[0]
    if sale_seen_at:  # the error-mid-sale case never gets to the cancel
        assert second_state["fetch"] > sale_seen_at[0]  # fetched after the sale, not before
    assert second_state["fetch"] == fetches[-2]  # and once more after the stop went in
    assert [d.type for d in cycle.last_discrepancies] == ["MISSING_STOP"]
    assert cycle.last_partial_exits[0].stop_lost is False  # the stop is back
    assert "re-placed" in cycle.last_partial_exits[0].reason


def test_a_stop_the_second_pass_cannot_place_fails_the_routine(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))
    stop_not_put_back(broker)
    cycle.check_and_repair_stops.side_effect = [
        [],
        [lost_stop_discrepancy(error="insufficient qty")],
    ]

    with pytest.raises(cycle_module.RoutineError, match=r"protective stops not secured \(AAPL\)"):
        cycle.evening_routine(raise_errors=True)

    assert cycle.check_and_repair_stops.call_count == 2
    assert cycle.last_discrepancies[0].details["error"] == "insufficient qty"  # for the notice
    assert cycle.last_partial_exits[0].stop_lost is True
    assert saved_target().order_id == "sell-1"  # the sale is on record for the retry


def test_the_retry_after_a_failed_second_pass_does_not_sell_again(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))
    stop_not_put_back(broker)
    cycle.check_and_repair_stops.side_effect = [
        [],
        [lost_stop_discrepancy(error="no")],
        [lost_stop_discrepancy()],  # the retry's first pass places the stop
    ]
    with pytest.raises(cycle_module.RoutineError):
        cycle.evening_routine(raise_errors=True)
    broker.reset_mock()

    # The scheduler retries a minute later: the broker still shows the 10 shares held
    cycle.evening_routine(raise_errors=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()
    assert cycle.check_and_repair_stops.call_count == 3  # no sale, so no second pass
    assert cycle.last_partial_exits == ()


def test_a_sale_whose_stop_went_back_still_gets_the_stops_checked_again(cycle, broker):
    save_plan()
    stub_routine(cycle, protected_state())

    cycle.evening_routine(raise_errors=True)

    assert cycle.check_and_repair_stops.call_count == 2
    assert cycle.last_partial_exits == ()
    assert cycle.last_discrepancies == ()


def test_a_sale_whose_stop_is_live_needs_two_fetches_only(cycle, broker):
    save_plan()
    stub_routine(cycle, protected_state())

    cycle.evening_routine(raise_errors=True)

    # The routine's own fetch, and one once the sale settled: the second pass runs on that
    assert cycle.fetch_broker_state.call_count == 2


def test_the_evening_second_pass_raises_to_the_trailing_level(cycle, broker):
    save_plan()
    stub_routine(cycle, protected_state())

    cycle.evening_routine(raise_errors=True)

    assert cycle.check_and_repair_stops.call_args_list[1].kwargs == {"raise_to_trailing": True}


def test_the_morning_second_pass_keeps_the_stops_level(cycle, broker, monkeypatch):
    save_plan()
    stub_routine(cycle, protected_state())

    def sold(broker_state, evening=False):  # should the morning routine ever sell
        cycle._note_sale("AAPL", "sell-1")
        return broker_state, []

    monkeypatch.setattr(cycle, "_partial_exits", sold)

    cycle.morning_routine(raise_errors=True)

    assert [c.kwargs for c in cycle.check_and_repair_stops.call_args_list] == [
        {"raise_to_trailing": False},
        {"raise_to_trailing": False},
    ]


def test_a_failure_whose_stop_turns_out_live_is_no_longer_lost(cycle, broker):
    save_plan()
    stub_routine(cycle, protected_state())  # the fetched state shows a stop on the shares left
    stop_not_put_back(broker)  # though the broker's answer to the put-back was an error

    cycle.evening_routine(raise_errors=True)

    assert cycle.check_and_repair_stops.call_count == 2
    (failure,) = cycle.last_partial_exits
    assert failure.stop_lost is False
    assert "found a live stop" in failure.reason
    assert cycle.last_discrepancies == ()


def test_only_the_symbol_whose_stop_went_back_is_no_longer_lost(cycle, broker):
    from src.trading.scheduling.trading_cycle import PartialExitFailure

    state = broker_state(AAPL=position(), MSFT=position("MSFT"))
    stub_routine(cycle, state)
    cycle._note_sale("AAPL", "sell-1")
    cycle._note_sale("MSFT", "sell-2")
    cycle.last_partial_exits = (
        PartialExitFailure("AAPL", 5, "lost", True),
        PartialExitFailure("MSFT", 5, "lost", True),
    )
    cycle.check_and_repair_stops.return_value = [lost_stop_discrepancy()]  # AAPL's stop placed
    discrepancies = []

    cycle._stops_after_partial_sales(state, discrepancies)

    assert [f.stop_lost for f in cycle.last_partial_exits] == [False, True]
    # MSFT has no stop and the pass placed none: the routine must fail, not complete
    assert [(d.symbol, bool(d.details.get("error"))) for d in discrepancies] == [
        ("AAPL", False),
        ("MSFT", True),
    ]


def test_the_second_pass_supersedes_the_first_passs_open_stop_items(cycle, broker):
    from src.trading.state.state_reconciler import Discrepancy

    def msft(type_, action, error=None):
        return Discrepancy(
            type=type_,
            symbol="MSFT",
            details={"reason": "r", "error": error},
            action=action,
            severity="HIGH",
        )

    save_plan()
    stub_routine(cycle, protected_state())
    mismatch = msft("QUANTITY_MISMATCH", "NEEDS_HUMAN_REVIEW")
    cycle.reconcile_state = MagicMock(side_effect=lambda state: [mismatch])
    cycle.check_and_repair_stops.side_effect = [
        [
            msft("TARGET_EXPIRING", "NEEDS_HUMAN_REVIEW"),
            msft("MISSING_STOP", "NEEDS_HUMAN_REVIEW", error="timeout"),
            msft("STOP_EXPIRING", "AUTO_FIXED"),
        ],
        [msft("TARGET_EXPIRING", "NEEDS_HUMAN_REVIEW"), msft("MISSING_STOP", "AUTO_FIXED")],
    ]

    cycle.evening_routine(raise_errors=True)  # the second pass placed MSFT's stop: no failure

    assert [(d.type, d.action) for d in cycle.last_discrepancies] == [
        ("QUANTITY_MISMATCH", "NEEDS_HUMAN_REVIEW"),  # not a stop item: kept
        ("STOP_EXPIRING", "AUTO_FIXED"),  # the first pass's renewal stays on record
        ("TARGET_EXPIRING", "NEEDS_HUMAN_REVIEW"),  # once, not twice
        ("MISSING_STOP", "AUTO_FIXED"),
    ]


def test_the_second_pass_fetches_when_the_sale_steps_own_refetch_failed(cycle, broker):
    save_plan()
    stub_routine(cycle, None)
    states = [{**broker_state(AAPL=position()), "fetch": n} for n in (1, 2, 3)]
    cycle.fetch_broker_state = MagicMock(
        side_effect=[states[0], RuntimeError("timeout"), states[1], states[2]]
    )
    cycle.check_and_repair_stops.side_effect = [[], [lost_stop_discrepancy()]]
    stop_not_put_back(broker)

    cycle.evening_routine(raise_errors=True)

    assert cycle.check_and_repair_stops.call_args_list[1].args[0]["fetch"] == 2  # not state 1
    sections = cycle._append_report_sections.call_args.args[2]
    assert any("Partial exits not checked: timeout" in line for line in sections)


def test_a_retry_after_a_refused_sale_does_not_sell_while_a_sell_order_is_open(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))
    sale_refused_and_stop_not_put_back(broker)
    cycle.check_and_repair_stops.side_effect = [
        [],
        [lost_stop_discrepancy(error="no")],
        [lost_stop_discrepancy()],  # the retry's first pass places the stop
    ]
    with pytest.raises(cycle_module.RoutineError):
        cycle.evening_routine(raise_errors=True)
    assert saved_target().order_id is None  # a refused sale is tried again
    broker.reset_mock()
    # The retry: the "refused" order was live after all (a client-side error after the
    # broker accepted it), and is still open
    cycle.fetch_broker_state.return_value = {
        **broker_state(AAPL=position()),
        "orders": {"AAPL": [MARKET_SELL]},
    }

    cycle.evening_routine(raise_errors=True)

    assert sells(broker) == []
    broker.cancel_order.assert_not_called()


def test_without_a_sale_the_stops_are_checked_once(cycle, broker):
    save_plan(target=106.0)  # not reached
    stub_routine(cycle, broker_state(AAPL=position()))

    cycle.evening_routine(raise_errors=True)

    cycle.check_and_repair_stops.assert_called_once()
    cycle.fetch_broker_state.assert_called_once()


def test_a_dry_run_sells_nothing_so_checks_the_stops_once(cycle, broker):
    save_plan()
    stub_routine(cycle, broker_state(AAPL=position()))

    cycle.evening_routine(raise_errors=True, dry_run=True)

    cycle.check_and_repair_stops.assert_called_once()


# The second pass on the real stop check, against the broker state the sale leaves (E1-01).
# A market order fills in moments, but the sale's order can still be open when the routine
# looks again: the position then reads as unsold and its shares as held by that order.


def real_stop_check(c, fake, sale_open_for=0, stop_outcomes=("submitted",)):
    """
    The routine's stop check is the real one (StateReconciler and repair_stops), and the
    fetched broker state is built from the fake's open orders and the shares sold. The
    sale's market order stays open for `sale_open_for` broker polls after it is placed
    (0: it fills at the first look; a large number: not within the routine's wait).
    stop_outcomes: the broker's answers to stop placements, in order, the last repeated;
    an accepted stop joins the open orders.

    Returns:
        A namespace with `filled` (quantities sold) and `fill_sale()` to settle the sale
    """
    from src.trading.state.state_reconciler import StateReconciler

    config = MagicMock()
    config.get_risk_config.return_value = 0.05
    c.reconciler = StateReconciler(config=config)
    c.broker_cache = MagicMock()
    c.local_state_manager.state["positions"]["AAPL"]["stop_price"] = 95.0
    c.check_and_repair_stops = CostEfficientTradeCycle.check_and_repair_stops.__get__(c)
    world = SimpleNamespace(filled=[], polls=0, outcomes=list(stop_outcomes), placed=0)

    def fill_sale():
        sale = next((o for o in fake.open if o["id"] == "sell-1"), None)
        if sale is not None:
            fake.open.remove(sale)
            world.filled.append(int(sale["qty"]))

    def sell(**kw):
        fake.open.append(
            {
                "id": "sell-1",
                "side": "sell",
                "status": "new",
                "order_type": "market",
                "qty": kw["qty"],
            }
        )
        return {"status": "submitted", "order_id": "sell-1"}

    def get_orders(**kw):
        if any(o["id"] == "sell-1" for o in fake.open):
            world.polls += 1
            if world.polls > sale_open_for:
                fill_sale()
        return list(fake.open)

    def place_stop(**kw):
        status = world.outcomes.pop(0) if len(world.outcomes) > 1 else world.outcomes[0]
        if status != "submitted":
            return {"status": status, "message": "no"}
        world.placed += 1
        order_id = f"stop-{world.placed + 1}"
        fake.open.append(
            {
                "id": order_id,
                "side": "sell",
                "status": "new",
                "order_type": "stop",
                "stop_price": kw["stop_price"],
                "qty": kw["qty"],
            }
        )
        return {"status": "submitted", "order_id": order_id}

    def fetch(**kw):
        held = 10 - sum(world.filled)
        orders = [
            order(o["id"], o["order_type"], int(o["qty"]), stop_price=o.get("stop_price"))
            for o in fake.open
        ]
        free = max(0, held - sum(o["quantity"] for o in orders))
        return {
            "positions": {"AAPL": {**position(quantity=held), "qty_available": free}},
            "orders": {"AAPL": orders} if orders else {},
            "account": {},
        }

    fake.place_market_order.side_effect = sell
    fake.get_orders.side_effect = get_orders
    fake.place_stop_order.side_effect = place_stop
    c.fetch_broker_state = MagicMock(side_effect=fetch)
    world.fill_sale = fill_sale
    return world


def stop_quantities(broker):
    return [c.kwargs["qty"] for c in broker.place_stop_order.call_args_list]


def test_the_second_pass_places_the_stop_on_the_shares_left(cycle, broker):
    save_plan()
    stub_routine(cycle, None)
    real_stop_check(cycle, broker, stop_outcomes=("error", "submitted"))  # the put-back fails

    cycle.evening_routine(raise_errors=True)

    assert stop_quantities(broker) == [5, 5]
    assert broker.place_stop_order.call_args.kwargs["stop_price"] == 95.0
    assert [d.action for d in cycle.last_discrepancies] == ["AUTO_FIXED"]
    assert cycle.last_partial_exits[0].stop_lost is False


def test_the_second_pass_fails_the_routine_when_the_broker_still_refuses(cycle, broker):
    save_plan()
    stub_routine(cycle, None)
    real_stop_check(cycle, broker, stop_outcomes=("error",))

    with pytest.raises(cycle_module.RoutineError, match=r"protective stops not secured \(AAPL\)"):
        cycle.evening_routine(raise_errors=True)

    assert stop_quantities(broker) == [5, 5]
    assert cycle.last_partial_exits[0].stop_lost is True


def test_the_second_pass_waits_for_the_sale_to_fill(cycle, broker, monkeypatch):
    save_plan()
    stub_routine(cycle, None)
    world = real_stop_check(cycle, broker, sale_open_for=2, stop_outcomes=("error", "submitted"))
    naps = []
    monkeypatch.setattr(cycle_module, "time", SimpleNamespace(sleep=naps.append))
    monkeypatch.setattr(cycle_module, "_SALE_SETTLE_SECONDS", 0.25)

    cycle.evening_routine(raise_errors=True)

    assert naps == [0.25, 0.25]  # two looks found the sale open; the third found it filled
    assert world.filled == [5]
    assert stop_quantities(broker) == [5, 5]  # on the 5 that stay, not on 10 "held"
    assert [d.action for d in cycle.last_discrepancies] == ["AUTO_FIXED"]


def test_a_sale_still_open_after_the_wait_fails_the_routine(cycle, broker):
    save_plan()
    stub_routine(cycle, None)
    real_stop_check(cycle, broker, sale_open_for=10**6, stop_outcomes=("error",))

    with pytest.raises(cycle_module.RoutineError, match=r"protective stops not secured \(AAPL\)"):
        cycle.evening_routine(raise_errors=True)

    # Only the failed put-back went to the broker: no stop for 10 shares that read as held
    # while the sale is open; the scheduler retries once the sale has filled
    assert stop_quantities(broker) == [5]
    (unresolved,) = [d for d in cycle.last_discrepancies if d.details.get("error")]
    assert (unresolved.type, unresolved.symbol) == ("MISSING_STOP", "AAPL")
    assert "still be open" in unresolved.details["error"]
    assert cycle.last_partial_exits[0].stop_lost is True


def test_a_sale_still_open_with_its_stop_back_leaves_nothing_for_review(cycle, broker):
    save_plan()
    stub_routine(cycle, None)
    real_stop_check(cycle, broker, sale_open_for=10**6)

    cycle.evening_routine(raise_errors=True)

    assert stop_quantities(broker) == [5]  # the put-back; the shares being sold need none
    assert cycle.last_discrepancies == ()
    assert cycle.last_partial_exits == ()


def test_the_retry_after_the_sale_settled_places_the_stop_and_sells_nothing(cycle, broker):
    save_plan()
    stub_routine(cycle, None)
    world = real_stop_check(cycle, broker, sale_open_for=10**6, stop_outcomes=("error",))
    with pytest.raises(cycle_module.RoutineError):
        cycle.evening_routine(raise_errors=True)
    # A minute later: the sale has filled, and the broker takes stops again
    world.fill_sale()
    world.outcomes[:] = ["submitted"]

    cycle.evening_routine(raise_errors=True)

    assert broker.place_market_order.call_count == 1  # the first run's sale only
    assert broker.cancel_order.call_count == 1
    assert stop_quantities(broker) == [5, 5]  # the put-back, then the retry's first pass
    assert [d.action for d in cycle.last_discrepancies] == ["AUTO_FIXED"]
    assert saved_target().filled is True


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
