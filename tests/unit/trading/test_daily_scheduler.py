"""Daily scheduler safety (#574 stage B): market time, trading days, flags, failures, lock."""

import fcntl
import logging
from datetime import datetime, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling.daily_scheduler import (
    DailyScheduler,
    configure_scheduler_logging,
    load_scheduler_config,
)
from src.trading.scheduling.scheduler_state import SchedulerStateManager
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle, RoutineError
from src.trading.state.state_reconciler import Discrepancy
from src.utils.state_paths import get_state_dir

ET = ZoneInfo("America/New_York")


def _et(*args):
    return datetime(*args, tzinfo=ET)


@pytest.fixture
def make_scheduler(tmp_path, monkeypatch):
    """A scheduler with a mock trading cycle and its own config and state."""
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler.time, "sleep", lambda seconds: None)

    def make(**config):
        settings = {"enabled": True, "morning_routine_time": "09:20:00"}
        settings.update(config)
        config_file = tmp_path / "scheduler_config.yaml"
        config_file.write_text("\n".join(f"{k}: {v!r}" for k, v in settings.items()))
        return DailyScheduler(
            config_file=str(config_file),
            trading_cycle=Mock(last_report_path="reports/daily/x.md"),
            state_manager=SchedulerStateManager(),
        )

    return make


def _task(scheduler, name):
    return next(t for t in scheduler.tasks if t.name == name)


def test_routine_time_is_eastern_whatever_the_host_timezone(make_scheduler, monkeypatch):
    scheduler = make_scheduler()
    morning = _task(scheduler, "morning_routine")

    class UtcHostClock(datetime):
        instant = datetime(2026, 10, 15, 13, 21, tzinfo=timezone.utc)  # 09:21 ET

        @classmethod
        def now(cls, tz=None):  # a host on UTC: naive now() is 13:21
            return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)

    monkeypatch.setattr(daily_scheduler, "datetime", UtcHostClock)
    assert scheduler.should_run_task(morning)

    UtcHostClock.instant = datetime(2026, 10, 15, 9, 21, tzinfo=timezone.utc)  # 05:21 ET
    assert not scheduler.should_run_task(morning)


def test_routines_run_on_trading_days_only(make_scheduler):
    scheduler = make_scheduler()
    morning = _task(scheduler, "morning_routine")

    assert scheduler.should_run_task(morning, now=_et(2026, 10, 15, 9, 21))  # Thursday
    assert not scheduler.should_run_task(morning, now=_et(2026, 10, 17, 9, 21))  # Saturday
    assert not scheduler.should_run_task(morning, now=_et(2026, 11, 26, 9, 21))  # Thanksgiving
    assert not scheduler.should_run_task(
        morning, now=_et(2026, 10, 15, 10, 21)
    )  # past the catch-up deadline
    assert not scheduler.should_run_task(morning, now=_et(2026, 10, 15, 9, 16))  # not yet


def test_evening_routine_moves_back_on_an_early_close(make_scheduler):
    scheduler = make_scheduler(evening_routine_time="15:50:00")
    evening = _task(scheduler, "evening_routine")

    # 2026-11-27, the day after Thanksgiving, closes at 13:00
    assert scheduler.should_run_task(evening, now=_et(2026, 11, 27, 12, 51))
    assert not scheduler.should_run_task(evening, now=_et(2026, 11, 27, 15, 51))
    assert scheduler.should_run_task(evening, now=_et(2026, 11, 30, 15, 51))  # normal day


def test_without_a_config_file_nothing_is_enabled(tmp_path):
    assert load_scheduler_config(str(tmp_path / "missing.yaml"))["enabled"] is False


def test_disabled_routine_is_skipped(make_scheduler):
    scheduler = make_scheduler(evening_routine_enabled=False)

    evening = _task(scheduler, "evening_routine")
    assert not scheduler.should_run_task(evening, now=_et(2026, 10, 15, 15, 50))


def test_saved_flags_override_the_config_file(make_scheduler):
    scheduler = make_scheduler(enabled=True)
    morning = _task(scheduler, "morning_routine")
    assert scheduler.should_run_task(morning, now=_et(2026, 10, 15, 9, 20))

    scheduler.save_flags(enabled=False)  # e.g. /scheduler disable in the CLI
    assert not scheduler.should_run_task(morning, now=_et(2026, 10, 15, 9, 20))

    disabled_in_file = make_scheduler(enabled=False)
    disabled_in_file.save_flags(enabled=True)
    assert disabled_in_file.should_run_task(morning, now=_et(2026, 10, 15, 9, 20))


def test_toggling_one_routine_keeps_the_config_files_other_flags(make_scheduler):
    scheduler = make_scheduler(enabled=True, evening_routine_enabled=False)

    assert scheduler.save_flags(evening=True)
    assert scheduler.effective_settings() == {
        "enabled": True,  # from the config file, not the stored default (disabled)
        "morning": True,
        "evening": True,
        "source": "saved state",
    }


def test_a_completed_routine_counts_for_its_market_date(make_scheduler):
    scheduler = make_scheduler()
    morning = _task(scheduler, "morning_routine")
    run_id = scheduler.state_manager.record_execution_start("morning", market_date="2026-10-15")
    scheduler.state_manager.record_execution_complete(run_id, status="completed")

    assert not scheduler.should_run_task(morning, now=_et(2026, 10, 15, 9, 20))
    assert scheduler.should_run_task(morning, now=_et(2026, 10, 16, 9, 20))


def test_failures_are_retried_on_fresh_broker_state_then_recorded_as_failed(make_scheduler):
    scheduler = make_scheduler(max_retries=2, retry_delay_seconds=1)
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")

    log = scheduler._execute_task_with_retry(_task(scheduler, "morning_routine"))

    assert scheduler.trading_cycle.morning_routine.call_count == 3
    assert scheduler.trading_cycle.invalidate_broker_cache.call_count == 3
    scheduler.trading_cycle.morning_routine.assert_called_with(raise_errors=True, dry_run=False)
    assert log.status == "failed"
    assert scheduler.state_manager.get_last_execution("morning").status == "failed"
    assert not scheduler.state_manager.was_routine_run_today("morning")


def test_a_routine_out_of_retries_is_not_started_again_that_day(make_scheduler):
    scheduler = make_scheduler()
    morning = _task(scheduler, "morning_routine")
    run_id = scheduler.state_manager.record_execution_start("morning", market_date="2026-10-15")
    scheduler.state_manager.record_execution_complete(run_id, status="failed")

    assert not scheduler.should_run_task(morning, now=_et(2026, 10, 15, 9, 24))
    assert scheduler.should_run_task(morning, now=_et(2026, 10, 16, 9, 24))


def test_a_success_records_the_routines_own_report(make_scheduler):
    scheduler = make_scheduler()

    log = scheduler._execute_task_with_retry(_task(scheduler, "morning_routine"))

    assert (log.status, log.report_path) == ("completed", "reports/daily/x.md")


def test_scheduled_runs_report_what_ran(make_scheduler):
    scheduler = make_scheduler()
    scheduler.should_run_task = lambda task: task.name == "morning_routine"
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")

    ran = scheduler.run_scheduled_tasks()  # main.py --run-due exits 1 on a failure

    assert [(entry.task_name, entry.status) for entry in ran] == [("morning_routine", "failed")]


def _bare_cycle(tmp_path, stop_discrepancies=()):
    """A trading cycle with its broker and state steps stubbed out."""
    cycle = object.__new__(CostEfficientTradeCycle)
    cycle.fetch_broker_state = Mock(return_value={"positions": {}, "orders": {}})
    cycle.reconcile_state = Mock(return_value=[])
    cycle.check_and_repair_stops = Mock(return_value=list(stop_discrepancies))
    cycle.check_position_alerts = Mock(return_value=[])
    cycle.check_gtt_triggers = Mock(return_value=[])
    cycle.calculate_stop_adjustments = Mock(return_value=[])
    cycle.trailing_stop_manager = Mock()
    cycle.local_state_manager = Mock(state={})
    cycle.generate_routine_report = Mock(return_value="report")
    cycle._get_report_file_path = Mock(return_value=str(tmp_path / "report.md"))
    cycle.record_daily = Mock(return_value=[])
    cycle.last_report_path = None
    return cycle


def test_routine_errors_raise_for_the_scheduler_but_not_for_the_cli(tmp_path):
    cycle = _bare_cycle(tmp_path)
    cycle.fetch_broker_state.side_effect = ConnectionError("broker down")

    assert cycle.morning_routine().startswith("❌")  # CLI: message, as before
    with pytest.raises(RoutineError, match="broker down"):
        cycle.morning_routine(raise_errors=True)
    with pytest.raises(RoutineError, match="broker down"):
        cycle.evening_routine(raise_errors=True)


def test_an_unplaced_stop_fails_the_routine_after_saving_its_report(tmp_path):
    unplaced = Discrepancy(
        type="MISSING_STOP",
        symbol="SPY",
        details={"error": "insufficient qty"},
        action="NEEDS_HUMAN_REVIEW",
        severity="HIGH",
    )
    cycle = _bare_cycle(tmp_path, [unplaced])

    with pytest.raises(RoutineError, match="SPY"):
        cycle.morning_routine(raise_errors=True)
    assert (tmp_path / "report.md").read_text() == "report"
    assert cycle.last_report_path == str(tmp_path / "report.md")


def test_cases_left_for_review_do_not_fail_the_routine(tmp_path):
    review = Discrepancy(
        type="MISSING_STOP",
        symbol="SPY",
        details={"reason": "no saved stop price"},
        action="NEEDS_HUMAN_REVIEW",
        severity="HIGH",
    )
    cycle = _bare_cycle(tmp_path, [review])

    assert cycle.morning_routine(raise_errors=True) == "report"


def test_a_run_in_progress_elsewhere_blocks_scheduled_and_manual_runs(make_scheduler):
    scheduler = make_scheduler()
    scheduler.should_run_task = Mock(return_value=True)

    with open(get_state_dir() / "scheduler.lock", "a") as other_process:
        fcntl.flock(other_process, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scheduler.run_scheduled_tasks()
        scheduler.run_once("morning_routine")
        fcntl.flock(other_process, fcntl.LOCK_UN)

    scheduler.should_run_task.assert_not_called()
    scheduler.trading_cycle.morning_routine.assert_not_called()


def test_next_run_skips_weekends_and_disabled_routines(make_scheduler):
    scheduler = make_scheduler(evening_routine_enabled=False)

    task, when = scheduler.next_run(now=_et(2026, 10, 16, 12, 0))  # Friday noon

    assert (task.name, when) == ("morning_routine", _et(2026, 10, 19, 9, 20))


def test_cli_next_run_reports_a_trading_day(make_scheduler, monkeypatch):
    from src.cli.tools import scheduler_tools

    scheduler = make_scheduler(evening_routine_enabled=False)
    monkeypatch.setattr(scheduler_tools, "_get_scheduler", lambda: scheduler)
    monkeypatch.setattr(scheduler, "_market_now", lambda: _et(2026, 10, 17, 8, 0))  # Saturday

    result = scheduler_tools.get_next_scheduled_run()

    assert result["status"] == "success"
    assert result["next_time"] == _et(2026, 10, 19, 9, 20).isoformat()


@pytest.fixture
def restore_root_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_daemon_logging_goes_to_the_state_dir_once(restore_root_logging):
    root = logging.getLogger()
    root.handlers[:] = [logging.StreamHandler()]  # e.g. from an earlier basicConfig
    root.setLevel(logging.DEBUG)

    path = configure_scheduler_logging()
    configure_scheduler_logging()  # a second call adds nothing
    logging.getLogger("scheduler-test").info("routine check")

    assert path == get_state_dir() / "scheduler.log"
    assert path.read_text().count("routine check") == 1
    assert len(root.handlers) == 2  # the existing console handler, plus the file
    assert root.level == logging.DEBUG  # not raised to INFO


def test_reports_are_named_by_the_market_date(monkeypatch, tmp_path):
    """A run at 22:44 ET is that day's report, though the host (UTC) is already on the next."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 15, 2, 44, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(scheduler_state, "datetime", _Clock)
    cycle = object.__new__(CostEfficientTradeCycle)
    template = str(tmp_path / "{date}_{routine_type}.md")
    cycle.paths = {"report_templates": {"daily_routine": template}}

    assert cycle._get_report_file_path("evening") == str(tmp_path / "2026-10-14_evening.md")
    (tmp_path / "2026-10-14_evening.md").write_text("first run")
    assert cycle._get_report_file_path("evening") == str(tmp_path / "2026-10-14_evening_2.md")
