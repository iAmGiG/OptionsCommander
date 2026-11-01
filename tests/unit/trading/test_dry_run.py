"""
Dry run (#601): a routine sends no orders and changes no saved state; its report shows
what it would have done.

The broker is mocked. As a backstop, the Alpaca SDK's order methods raise if anything
reaches them. The same setup run normally shows the writes the dry run holds back.
"""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import src.trading.scheduling.trading_cycle as cycle_module
from src.trading.orders.dry_run import DRY_RUN_STATUS, DryRunOrderManager
from src.trading.positions.position_tracker import PositionTracker
from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling.daily_scheduler import DailyScheduler
from src.trading.scheduling.scheduler_state import SchedulerStateManager
from src.trading.scheduling.trading_cycle import DRY_RUN_ACTION, CostEfficientTradeCycle
from src.trading.state.state_reconciler import StateReconciler, StopAdjustment
from src.trading.utils.report_generator import ReportGenerator

SDK_WRITES = (
    "submit_order",
    "replace_order_by_id",
    "cancel_order_by_id",
    "cancel_orders",
    "close_position",
    "close_all_positions",
    "exercise_options_position",
)

SELL_TRIGGER = SimpleNamespace(
    id=7,
    symbol="SPY",
    action_type="place_order",
    action_config={"side": "sell", "qty": 5},
)


@pytest.fixture(autouse=True)
def no_sdk_orders(monkeypatch):
    from alpaca.trading.client import TradingClient

    def refuse(name):
        def call(*args, **kwargs):
            raise AssertionError(f"TradingClient.{name} called")

        return call

    for name in SDK_WRITES:
        monkeypatch.setattr(TradingClient, name, refuse(name))


def _stop_order(order_id, symbol, stop_price, quantity):
    return {
        "id": order_id,
        "symbol": symbol,
        "type": "stop",
        "side": "sell",
        "quantity": quantity,
        "stop_price": stop_price,
        "time_in_force": "gtc",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
    }


def _broker():
    """SPY has no live stop (a repair would place one); QQQ's stop can be raised."""
    return {
        "positions": {
            "SPY": {
                "symbol": "SPY",
                "quantity": 14,
                "entry_price": 657.6,
                "current_price": 744.5,
                "side": "long",
            },
            "QQQ": {
                "symbol": "QQQ",
                "quantity": 3,
                "entry_price": 616.8,
                "current_price": 712.0,
                "side": "long",
            },
        },
        "orders": {"QQQ": [_stop_order("sl-q", "QQQ", 691.0, 3)]},
        "account": {},
    }


def _local():
    return {
        "positions": {
            "SPY": {"entry_price": 657.6, "quantity": 14, "stop_price": 744.34},
            "QQQ": {"entry_price": 616.8, "quantity": 3, "stop_price": 691.0},
        }
    }


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    """A cycle with the real repair, raise, GTT and report code, and a mock broker."""
    evaluator, executor, sync = MagicMock(), MagicMock(), MagicMock(return_value={"synced": 2})
    evaluator.evaluate_triggers_batch.return_value = [SELL_TRIGGER]
    executor.execute_triggers_batch.return_value = []
    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", True)
    monkeypatch.setattr(cycle_module, "get_trigger_evaluator", lambda: evaluator)
    monkeypatch.setattr(cycle_module, "get_action_executor", lambda: executor)
    monkeypatch.setattr(cycle_module, "sync_all_trailing_stops", sync)
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: True)

    config = MagicMock()
    config.get_risk_config.return_value = 0.05
    om = MagicMock()
    om.place_stop_order.return_value = {"status": "submitted", "order_id": "new-sl"}
    om.modify_order.return_value = {"status": "submitted", "order_id": "sl-q2"}

    c = object.__new__(CostEfficientTradeCycle)  # no broker clients
    c.order_manager = om
    c.account_monitor = MagicMock()
    c.broker_cache = MagicMock()
    c.reconciler = StateReconciler(config=config)
    c.local_state_manager = MagicMock(state=_local())
    c.trailing_stop_manager = None
    c.report_generator = ReportGenerator()
    c._daily_records = MagicMock()
    c.last_report_path = None
    c.fetch_broker_state = MagicMock(return_value=_broker())
    c.reconcile_state = MagicMock(return_value=[])
    c.check_position_alerts = MagicMock(return_value=[])
    c.calculate_stop_adjustments = MagicMock(
        return_value=[StopAdjustment("QQQ", "sl-q", 691.0, 698.77, "trailing", 13.3)]
    )
    c._get_report_file_path = lambda routine: str(tmp_path / f"{routine}.md")
    c.mocks = SimpleNamespace(om=om, evaluator=evaluator, executor=executor, sync=sync)
    return c


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_a_dry_run_sends_no_orders_and_saves_nothing(cycle, routine, tmp_path):
    m = cycle.mocks
    local_before = copy.deepcopy(cycle.local_state_manager.state)

    report = getattr(cycle, routine)(raise_errors=True, dry_run=True)  # no RoutineError

    m.om.place_stop_order.assert_not_called()
    m.om.modify_order.assert_not_called()
    assert cycle.order_manager is m.om  # the real one is back
    m.executor.execute_triggers_batch.assert_not_called()
    m.sync.assert_not_called()
    m.evaluator.evaluate_triggers_batch.assert_called_once_with(
        {"SPY": 744.5, "QQQ": 712.0}, persist=False
    )
    cycle.local_state_manager.save.assert_not_called()
    assert cycle.local_state_manager.state == local_before
    assert cycle._daily_records.method_calls == []
    cycle.account_monitor.get_orders.assert_not_called()

    name = routine.replace("_routine", "")
    saved = (tmp_path / f"{name}_dry_run.md").read_text()
    assert saved == report and report.startswith("> 🧪 **DRY RUN**")
    assert not (tmp_path / f"{name}.md").exists()
    assert "MISSING_STOP: SPY" in report and DRY_RUN_ACTION in report
    # Only the evening raises stops (#623)
    assert ("QQQ: would raise stop $691.00 -> $698.77" in report) == (routine == "evening_routine")
    assert "dry run: SPY place_order not executed" in report
    assert "Dry run: daily records not saved" in report


def test_a_dry_run_leaves_the_position_tracker_as_it_was(cycle):
    """The real alert check syncs and prunes the tracker; a dry run puts it back (#599)."""
    tracker = PositionTracker(take_profit_pct=0.08, stop_loss_pct=0.05)
    tracker.sync_position("AAPL", 230.0, 1, 220.0)  # no longer held: the check drops it
    tracker.alert_counter = 3
    before = copy.deepcopy(tracker.to_dict())
    cycle.position_tracker = tracker
    cycle.reconciler.position_tracker = tracker
    del cycle.check_position_alerts  # the real check

    cycle.morning_routine(raise_errors=True, dry_run=True)

    assert [a.symbol for a in cycle.last_alerts] == ["SPY"]  # 744.50 is 0.02% above 744.34
    assert tracker.to_dict() == before


def test_the_same_run_without_dry_run_acts(cycle, tmp_path):
    m = cycle.mocks

    cycle.evening_routine(raise_errors=True)

    m.om.place_stop_order.assert_called_once()
    m.om.modify_order.assert_called_once_with(order_id="sl-q", stop_price=698.77)
    m.executor.execute_triggers_batch.assert_called_once()
    m.sync.assert_called_once()
    cycle.local_state_manager.save.assert_called_once()
    assert (tmp_path / "evening.md").exists()


def test_the_morning_run_repairs_stops_but_doesnt_raise_them(cycle, tmp_path):
    """#623: the stop due a raise waits for the evening; the missing one is still placed."""
    m = cycle.mocks

    cycle.morning_routine(raise_errors=True)

    m.om.place_stop_order.assert_called_once()
    m.om.modify_order.assert_not_called()
    cycle.calculate_stop_adjustments.assert_not_called()
    m.executor.execute_triggers_batch.assert_called_once()
    m.sync.assert_called_once()
    cycle.local_state_manager.save.assert_called_once()
    assert (tmp_path / "morning.md").exists()


def test_the_cycle_setting_is_the_default(cycle, tmp_path):
    cycle.dry_run = True

    cycle.morning_routine()

    cycle.mocks.om.place_stop_order.assert_not_called()
    assert (tmp_path / "morning_dry_run.md").exists()


def test_the_guard_passes_reads_and_blocks_everything_else():
    om = MagicMock(mode="paper")
    om.get_orders.return_value = ["o1"]
    om.trading_client = object()
    guard = DryRunOrderManager(om)

    assert guard.get_orders(status="open") == ["o1"]
    assert guard.mode == "paper"
    for name in ("place_market_order", "modify_order", "cancel_all_orders", "a_new_write"):
        assert getattr(guard, name)(symbol="SPY")["status"] == DRY_RUN_STATUS
    assert [call[0] for call in guard.calls] == [
        "place_market_order",
        "modify_order",
        "cancel_all_orders",
        "a_new_write",
    ]
    om.place_market_order.assert_not_called()
    om.cancel_all_orders.assert_not_called()
    with pytest.raises(AttributeError, match="not available in a dry run"):
        guard.trading_client


@pytest.fixture
def make_scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler.time, "sleep", lambda seconds: None)

    def make(dry_run, trading_cycle=None):
        config_file = tmp_path / "scheduler_config.yaml"
        config_file.write_text(f"enabled: true\ndry_run: {str(dry_run).lower()}\n")
        return DailyScheduler(
            config_file=str(config_file),
            trading_cycle=trading_cycle,
            state_manager=SchedulerStateManager(),
        )

    return make


@pytest.mark.parametrize("dry_run", [True, False])
def test_the_scheduler_passes_its_dry_run_setting(make_scheduler, dry_run):
    scheduler = make_scheduler(dry_run, trading_cycle=Mock(last_report_path="r.md"))
    morning = next(t for t in scheduler.tasks if t.name == "morning_routine")

    scheduler._execute_task_with_retry(morning)

    scheduler.trading_cycle.morning_routine.assert_called_once_with(
        raise_errors=True, dry_run=dry_run
    )


def test_timeout_seconds_is_gone(make_scheduler):
    scheduler = make_scheduler(False)

    assert "timeout_seconds" not in daily_scheduler.DEFAULT_CONFIG
    assert not any(hasattr(task, "timeout_seconds") for task in scheduler.tasks)
    assert not hasattr(scheduler.state_manager.get_state(), "timeout_seconds")


@pytest.mark.parametrize(
    "words, setting, expected",
    [(["morning", "dry"], False, True), (["morning"], True, True), (["evening"], False, False)],
)
def test_the_cli_test_command_runs_dry_when_asked_or_set(
    make_scheduler, words, setting, expected, capsys
):
    import asyncio

    from src.cli.scheduler_cli import SchedulerCLI

    cycle = Mock()
    cycle.morning_routine.return_value = cycle.evening_routine.return_value = "report"
    cli = SchedulerCLI(scheduler=make_scheduler(setting, trading_cycle=cycle))

    asyncio.run(cli._handle_command(" ".join(["test", *words])))

    routine = getattr(cycle, f"{words[0]}_routine")
    routine.assert_called_once_with(dry_run=expected)
    label = "dry run: no orders are sent" if expected else "acts on the broker"
    assert label in capsys.readouterr().out


def test_the_cli_test_command_rejects_an_unknown_mode(make_scheduler, capsys):
    import asyncio

    from src.cli.scheduler_cli import SchedulerCLI

    cycle = Mock()
    cli = SchedulerCLI(scheduler=make_scheduler(False, trading_cycle=cycle))

    asyncio.run(cli._handle_command("test morning live"))

    cycle.morning_routine.assert_not_called()
    assert "Usage: test [morning|evening] [dry]" in capsys.readouterr().out


def test_a_database_with_the_old_timeout_column_still_loads_and_saves(tmp_path):
    import sqlite3

    db = tmp_path / "user.db"
    with sqlite3.connect(db) as conn:  # the scheduler_state table as created before #601
        conn.execute(
            """CREATE TABLE scheduler_state (
                id INTEGER PRIMARY KEY CHECK (id = 1), enabled BOOLEAN DEFAULT FALSE,
                morning_enabled BOOLEAN DEFAULT TRUE, evening_enabled BOOLEAN DEFAULT FALSE,
                morning_time TEXT DEFAULT '09:20:00', evening_time TEXT DEFAULT '15:50:00',
                max_retries INTEGER DEFAULT 3, retry_delay_seconds INTEGER DEFAULT 60,
                timeout_seconds INTEGER DEFAULT 300, updated_at TEXT)"""
        )
        conn.execute("INSERT INTO scheduler_state (id, enabled, evening_enabled) VALUES (1, 1, 1)")
    manager = SchedulerStateManager(db_path=str(db))

    state = manager.get_state()
    assert (state.enabled, state.evening_enabled) == (True, True)
    state.morning_enabled = False
    assert manager.save_state(state)
    assert manager.get_state().morning_enabled is False


def test_trailing_stop_states_in_memory_are_restored(cycle):
    trailing = SimpleNamespace(stop_states={"QQQ": SimpleNamespace(current_stop=691.0)})
    cycle.trailing_stop_manager = trailing

    def moves_the_stop(broker_state):
        trailing.stop_states["QQQ"].current_stop = 698.77
        trailing.stop_states["SPY"] = "new"
        return []

    cycle.calculate_stop_adjustments = moves_the_stop

    cycle.morning_routine(dry_run=True)

    assert trailing.stop_states["QQQ"].current_stop == 691.0 and "SPY" not in trailing.stop_states


def test_a_scheduled_dry_run_is_not_the_days_routine(make_scheduler):
    scheduler = make_scheduler(True, trading_cycle=Mock(last_report_path="r.md"))
    morning = next(t for t in scheduler.tasks if t.name == "morning_routine")

    log = scheduler._execute_task_with_retry(morning)

    assert log.status == "dry_run"
    assert scheduler.state_manager.get_last_execution("morning").status == "dry_run"
    today = scheduler._market_now().date().isoformat()
    assert not scheduler.state_manager.was_routine_finished_today("morning", today)


def test_crash_recovery_in_a_dry_run_saves_no_state(cycle, tmp_path):
    cycle.local_state_manager.positions = {}
    before = copy.deepcopy(cycle.local_state_manager.state)

    report = cycle.recover_from_crash(raise_errors=True, dry_run=True)

    cycle.local_state_manager.save.assert_not_called()
    assert cycle.local_state_manager.state == before
    assert report.startswith("> 🧪 **DRY RUN**")
    assert (tmp_path / "recovery_dry_run.md").exists()


def test_gtt_triggers_a_dry_run_held_back_are_marked_not_failed(cycle):
    report = cycle.morning_routine(dry_run=True)

    assert "[~] Trigger 7: dry run: SPY place_order not executed" in report
    assert "[x] Trigger 7" not in report


def test_only_the_listed_reads_pass(monkeypatch):
    from src.trading.orders import dry_run

    om = MagicMock()
    guard = DryRunOrderManager(om)

    assert guard.get_positions() is om.get_positions.return_value
    assert guard.get_or_create_stop(symbol="SPY")["status"] == DRY_RUN_STATUS  # not a listed read
    om.get_or_create_stop.assert_not_called()
    assert "get_orders" in dry_run.READS
