"""
After a position closes (#634): its trailing state and GTT trigger go, the report calls a
normal exit by its name, and its breach notice resolves. A moved average entry starts the
state afresh and updates the saved entry (#619). The broker is mocked; the GTT store is real,
on a temp database.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.trading.gtt.trailing_stop_bridge as bridge
import src.trading.scheduling.trading_cycle as cycle_module
from src.trading.gtt.gtt_manager import ActionType, ConditionType, GTTManager
from src.trading.orders.trailing_stop_manager import TrailingStopManager
from src.trading.scheduling.notices import STOP_BREACH, NoticeStore
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle, RoutineType
from src.trading.state.state_reconciler import Discrepancy, StateReconciler
from src.trading.utils.report_generator import ReportGenerator


def position(entry, quantity=14, price=None):
    return {
        "symbol": "X",
        "quantity": quantity,
        "entry_price": entry,
        "current_price": price or entry,
        "side": "long",
        "unrealized_pl": 0.0,
    }


SPY = position(657.595, 14, 760.0)
QQQ_SOLD_FILL = {
    "id": "f1",
    "symbol": "QQQ",
    "side": "sell",
    "order_type": "stop",
    "filled_qty": 3.0,
    "filled_avg_price": 695.996667,
}


@pytest.fixture
def gtt(tmp_path, monkeypatch):
    manager = GTTManager(db_path=str(tmp_path / "gtt.db"))
    monkeypatch.setattr(bridge, "get_gtt_manager", lambda: manager)
    return manager


@pytest.fixture
def stops():
    return TrailingStopManager(order_manager=None, config=MagicMock())


def holding(stops, gtt, symbol, entry, stop, high, quantity=3):
    """A position with saved trailing state: what each routine's GTT sync leaves behind."""
    state = stops.register_position(symbol, entry, stop, quantity, stop_order_id="sl")
    state.highest_price_seen = high
    bridge.sync_trailing_stop_to_gtt(symbol, state)


@pytest.fixture
def cycle(stops, monkeypatch):
    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", True)
    c = object.__new__(CostEfficientTradeCycle)  # no broker clients
    c.trailing_stop_manager = stops
    c._dry_run_active = False
    c._start_routine()
    return c


def trailing_triggers(gtt):
    return {
        t.symbol: t
        for t in gtt.get_triggers(enabled_only=False, active_only=False)
        if t.condition_type == ConditionType.TRAILING_STOP.value
    }


# Trailing state and the GTT trigger


def test_a_closed_position_leaves_no_trailing_state_or_trigger(cycle, stops, gtt):
    holding(stops, gtt, "QQQ", 616.80, 698.77, 726.10)
    holding(stops, gtt, "SPY", 657.60, 744.47, 773.43, 14)

    cycle._settle_closed_positions({"positions": {"SPY": SPY}}, [])

    assert set(stops.stop_states) == {"SPY"}
    assert set(trailing_triggers(gtt)) == {"SPY"}


def test_a_later_run_has_nothing_to_restore(cycle, stops, gtt):
    """The reported bug: the evening routine restored QQQ and synced it back."""
    holding(stops, gtt, "QQQ", 616.80, 698.77, 726.10)
    cycle._settle_closed_positions({"positions": {}}, [])

    fresh = TrailingStopManager(order_manager=None, config=MagicMock())
    result = bridge.restore_trailing_stops_from_gtt(fresh)

    assert result["restored"] == 0
    assert fresh.stop_states == {}


def test_a_new_purchase_starts_afresh(cycle, stops, gtt):
    holding(stops, gtt, "QQQ", 616.80, 698.77, 726.10)
    cycle._settle_closed_positions({"positions": {}}, [])

    state = stops.register_position("QQQ", 700.0, 665.0, 2)
    state.highest_price_seen = 700.0
    bridge.sync_trailing_stop_to_gtt("QQQ", state)

    config = trailing_triggers(gtt)["QQQ"].action_config
    assert (config["entry_price"], config["highest_price"], config["qty"]) == (700.0, 700.0, 2)


def test_sold_and_bought_again_between_routines_starts_afresh(cycle, stops, gtt):
    holding(stops, gtt, "QQQ", 616.80, 698.77, 726.10)

    cycle._settle_closed_positions({"positions": {"QQQ": position(700.0, 2)}}, [])

    assert "QQQ" not in stops.stop_states
    assert trailing_triggers(gtt) == {}


def test_the_rounded_entry_of_the_same_position_is_kept(cycle, stops, gtt):
    """The state rounds $657.595 to $657.60: not a new position."""
    holding(stops, gtt, "SPY", 657.60, 744.47, 773.43, 14)
    state = stops.stop_states["SPY"]

    cycle._settle_closed_positions({"positions": {"SPY": SPY}}, [])

    assert stops.stop_states["SPY"] is state
    assert state.highest_price_seen == 773.43
    assert "SPY" in trailing_triggers(gtt)


def test_a_partial_sale_updates_the_quantity(cycle, stops, gtt):
    holding(stops, gtt, "SPY", 657.60, 744.47, 773.43, 14)

    cycle._settle_closed_positions({"positions": {"SPY": position(657.595, 9)}}, [])

    assert stops.stop_states["SPY"].quantity == 9


def test_restoring_leaves_a_users_trailing_trigger_out(stops, gtt, caplog):
    holding(stops, gtt, "SPY", 657.60, 744.47, 773.43, 14)
    gtt.create_trigger(
        symbol="AAPL",
        condition_type=ConditionType.TRAILING_STOP,
        trigger_value=0.05,
        action_type=ActionType.ALERT,
    )

    fresh = TrailingStopManager(order_manager=None, config=MagicMock())
    with caplog.at_level("WARNING", logger=bridge.logger.name):
        result = bridge.restore_trailing_stops_from_gtt(fresh)

    assert result["restored"] == 1
    assert result["skipped"] == 0
    assert set(fresh.stop_states) == {"SPY"}
    assert "Incomplete" not in caplog.text


def test_a_trigger_the_user_made_stays(cycle, stops, gtt):
    user_trigger = gtt.create_trigger(
        symbol="NVDA",
        condition_type=ConditionType.TRAILING_STOP,
        trigger_value=0.05,
        action_type=ActionType.ALERT,
    )
    other = gtt.create_trigger(
        symbol="TSLA",
        condition_type=ConditionType.PRICE_BELOW,
        trigger_value=200.0,
        action_type=ActionType.ALERT,
    )

    cycle._settle_closed_positions({"positions": {}}, [])

    assert gtt.get_trigger(user_trigger.id) is not None
    assert gtt.get_trigger(other.id) is not None


def test_a_dry_run_changes_nothing(cycle, stops, gtt):
    holding(stops, gtt, "QQQ", 616.80, 698.77, 726.10)
    cycle._dry_run_active = True

    cycle._settle_closed_positions({"positions": {}}, [])

    assert "QQQ" in stops.stop_states
    assert "QQQ" in trailing_triggers(gtt)


def test_a_cleanup_error_doesnt_fail_the_routine(cycle, stops, monkeypatch):
    def broken(symbols):
        raise RuntimeError("database locked")

    monkeypatch.setattr(cycle_module, "cleanup_orphaned_trailing_stops", broken)

    assert cycle._settle_closed_positions({"positions": {}}, []) == []


# Naming the exit


def ghost(symbol="QQQ"):
    return Discrepancy(
        type="GHOST_POSITION",
        symbol=symbol,
        details={"local_quantity": 3, "local_entry": 616.8},
        action="REMOVED_FROM_JSON",
        severity="MEDIUM",
    )


def report_for(discrepancies):
    return ReportGenerator().generate_routine_report(
        RoutineType.EVENING,
        {"positions": {}, "orders": {}, "account": {}},
        {"positions": {}},
        discrepancies,
        [],
    )


def test_a_stop_out_is_reported_as_a_close(cycle, gtt):
    cycle._new_fills = [QQQ_SOLD_FILL]

    settled = cycle._settle_closed_positions({"positions": {}}, [ghost()])

    assert settled[0].type == "POSITION_CLOSED"
    assert settled[0].severity == "LOW"
    report = report_for(settled)
    assert "POSITION_CLOSED: QQQ" in report
    assert "3 shares sold @ $696.00 (stop)" in report
    assert "GHOST" not in report
    assert "Phantom" not in report


def test_a_position_gone_with_no_fill_is_still_a_ghost(cycle, gtt):
    settled = cycle._settle_closed_positions({"positions": {}}, [ghost()])

    assert settled[0].type == "GHOST_POSITION"
    assert "Phantom 3 shares" in report_for(settled)


def test_a_fill_for_another_symbol_or_a_buy_doesnt_explain_it(cycle, gtt):
    cycle._new_fills = [
        {**QQQ_SOLD_FILL, "symbol": "SPY"},
        {**QQQ_SOLD_FILL, "side": "buy"},
    ]

    settled = cycle._settle_closed_positions({"positions": {}}, [ghost()])

    assert settled[0].type == "GHOST_POSITION"


def test_new_fills_come_from_this_run(cycle, gtt):
    cycle._new_fills = [QQQ_SOLD_FILL]

    cycle._start_routine()

    assert cycle._new_fills == []
    assert cycle.last_open_symbols is None


def test_a_bad_fill_doesnt_fail_the_routine(cycle, gtt):
    cycle._new_fills = [{**QQQ_SOLD_FILL, "filled_avg_price": None}]

    settled = cycle._settle_closed_positions({"positions": {}}, [ghost()])

    assert settled[0].type == "GHOST_POSITION"


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_a_routine_settles_closed_positions_before_it_acts(cycle, stops, gtt, routine, monkeypatch):
    """Through the real routine: the GTT check and the report see the settled state."""
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: False)
    holding(stops, gtt, "QQQ", 616.80, 698.77, 726.10)
    seen = {}

    def record(name, broker_state):
        cycle._new_fills = [QQQ_SOLD_FILL]
        return []

    def gtt_check(broker_state):
        seen["held_at_gtt_check"] = set(stops.stop_states)
        return []

    report = MagicMock(return_value="report")
    cycle.fetch_broker_state = MagicMock(
        return_value={"positions": {}, "orders": {}, "account": {}}
    )
    cycle.record_daily = record
    cycle.reconcile_state = MagicMock(return_value=[ghost()])
    cycle.check_and_repair_stops = MagicMock(return_value=[])
    cycle.check_gtt_triggers = gtt_check
    cycle._after_gtt_actions = lambda broker_state, results: broker_state
    cycle.raise_stops = MagicMock(return_value=([], None))
    cycle._position_alerts = MagicMock(return_value=([], []))
    cycle._sync_trailing_stops_to_gtt = MagicMock()
    cycle.local_state_manager = MagicMock(state={"positions": {}})
    cycle.generate_routine_report = report
    cycle._append_report_sections = lambda text, *args: text
    cycle._save_report = lambda name, text: (text, f"{name}.md")
    cycle.dry_run = False

    getattr(cycle, routine)(raise_errors=True)

    assert seen["held_at_gtt_check"] == set()
    assert trailing_triggers(gtt) == {}
    assert report.call_args.args[2][0].type == "POSITION_CLOSED"
    assert cycle.last_open_symbols == frozenset()


# The saved entry price (#619)


def reconcile(broker, local):
    config = MagicMock()
    config.get_risk_config.return_value = 0.05
    return StateReconciler(config=config).reconcile(
        {"positions": broker, "orders": {}}, {"positions": local}
    )


def test_a_moved_average_entry_is_saved_and_listed():
    discrepancies, state = reconcile(
        {"SPY": position(680.0, 20)}, {"SPY": {"entry_price": 657.6, "quantity": 14}}
    )

    assert state["positions"]["SPY"]["entry_price"] == 680.0
    changed = [d for d in discrepancies if d.type == "ENTRY_PRICE_CHANGED"]
    assert changed[0].details == {"broker_entry": 680.0, "local_entry": 657.6}
    assert changed[0].action == "AUTO_FIXED"
    assert any(d.type == "QUANTITY_MISMATCH" for d in discrepancies)
    assert "ENTRY_PRICE_CHANGED: SPY" in report_for(discrepancies)


def test_a_rounding_difference_is_not_listed():
    discrepancies, state = reconcile(
        {"SPY": position(657.595, 14)}, {"SPY": {"entry_price": 657.6, "quantity": 14}}
    )

    assert discrepancies == []
    assert state["positions"]["SPY"]["entry_price"] == 657.6


# The notice


def scheduler_with(cycle, tmp_path):
    from src.trading.scheduling.daily_scheduler import DailyScheduler

    scheduler = object.__new__(DailyScheduler)
    scheduler._trading_cycle = cycle
    scheduler.notice_store = NoticeStore(str(tmp_path / "notices.json"))
    return scheduler


def breach(store, symbol, date="2026-10-30"):
    store.raise_notice(
        key=f"{STOP_BREACH}:{symbol}:{date}",
        kind=STOP_BREACH,
        severity="CRITICAL",
        title=f"{symbol} is at or below its stop, still open",
        market_date=date,
        symbol=symbol,
    )


def test_a_breach_notice_resolves_once_the_position_is_gone(tmp_path):
    cycle = SimpleNamespace(last_open_symbols=frozenset({"SPY"}))
    scheduler = scheduler_with(cycle, tmp_path)
    breach(scheduler.notice_store, "QQQ")
    breach(scheduler.notice_store, "SPY")

    scheduler._notice_positions("2026-10-30", run_completed=True)

    resolved = {n.symbol: n.resolved_at for n in scheduler.notice_store.open_notices()}
    assert resolved["QQQ"] is not None  # still listed until acknowledged
    assert resolved["SPY"] is None


@pytest.mark.parametrize(
    ("completed", "open_symbols"),
    [(False, frozenset()), (True, None)],
)
def test_a_notice_stays_unless_a_completed_run_saw_the_positions(tmp_path, completed, open_symbols):
    cycle = SimpleNamespace(last_open_symbols=open_symbols)
    scheduler = scheduler_with(cycle, tmp_path)
    breach(scheduler.notice_store, "QQQ")

    scheduler._notice_positions("2026-10-30", run_completed=completed)

    assert scheduler.notice_store.open_notices()[0].resolved_at is None
