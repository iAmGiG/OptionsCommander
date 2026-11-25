"""
Notices from the scheduler (#342): a failed run, a routine that never ran or never finished,
a position through its stop or left without one, and a crashed cron check. The market
calendar is answered from the NYSE rules (and counted), and nothing leaves the machine.
"""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling import trading_cycle as cycle_module
from src.trading.scheduling.daily_scheduler import DailyScheduler, RunOutcome, run_outcome
from src.trading.scheduling.notices import NoticeStore
from src.trading.scheduling.scheduler_state import SchedulerExecution, SchedulerStateManager
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle, RoutineError
from src.trading.state.state_reconciler import Discrepancy, PositionAlertSummary
from src.utils.state_paths import STATE_DIR_ENV, get_state_dir

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[3]


def _et(*args):
    return datetime(*args, tzinfo=ET)


@pytest.fixture
def calendar(monkeypatch):
    """The days the scheduler asked the market calendar about (answered from the NYSE rules)."""
    asked = []
    real = daily_scheduler.get_market_hours_for_date

    def lookup(day):
        asked.append(day)
        return real(day)

    monkeypatch.setattr(daily_scheduler, "get_market_hours_for_date", lookup)
    return asked


def _cycle(alerts=(), discrepancies=()):
    """A mock trading cycle holding the results of the routine it 'ran'."""
    return Mock(
        last_report_path="reports/daily/x.md",
        last_alerts=tuple(alerts),
        last_discrepancies=tuple(discrepancies),
    )


@pytest.fixture
def make_scheduler(tmp_path, monkeypatch, calendar):
    """A scheduler with a mock trading cycle, its own state and its own notices."""
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler.time, "sleep", lambda seconds: None)

    def make(cycle=None, **config):
        settings = {"enabled": True}
        settings.update(config)
        config_file = tmp_path / "scheduler_config.yaml"
        config_file.write_text("\n".join(f"{k}: {v!r}" for k, v in settings.items()))
        return DailyScheduler(
            config_file=str(config_file),
            trading_cycle=cycle or _cycle(),
            state_manager=SchedulerStateManager(),
            notice_store=NoticeStore(tmp_path / "notices.json", raise_errors=True),
        )

    return make


def _task(scheduler, name):
    return next(t for t in scheduler.tasks if t.name == name)


def _record(scheduler, routine, day, status="completed", started=None):
    """A run in the history: its status ('running' is never finished) and when it started."""
    manager = scheduler.state_manager
    run_id = manager.record_execution_start(routine, market_date=day)
    if status != "running":
        manager.record_execution_complete(run_id, status=status)
    if started is not None:  # the history keeps the host's local time, without a zone
        stamp = started.astimezone().replace(tzinfo=None).isoformat()
        with sqlite3.connect(manager._db_path) as conn:
            conn.execute(
                "UPDATE scheduler_history SET started_at = ? WHERE id = ?", (stamp, run_id)
            )
    return run_id


def _switched_on(scheduler, when):
    """Switch the scheduler on (as the CLI does), with both routines stamped as on since `when`."""
    scheduler.save_flags(enabled=True)
    stamp = when.astimezone().replace(tzinfo=None).isoformat()
    with sqlite3.connect(scheduler.state_manager._db_path) as conn:
        conn.execute(
            "UPDATE scheduler_state SET morning_enabled_at = ?, evening_enabled_at = ?",
            (stamp, stamp),
        )


def _both(scheduler, day):
    """The day's morning and evening routines, completed."""
    _record(scheduler, "morning", day)
    _record(scheduler, "evening", day)


def _keys(scheduler):
    return [notice.key for notice in scheduler.notice_store.all_notices()]


def _today(scheduler):
    return scheduler._market_now().date().isoformat()


# --- A run's own outcome ----------------------------------------------------------------


def test_a_failure_after_all_retries_is_one_notice_and_a_second_failure_counts_it(make_scheduler):
    scheduler = make_scheduler(max_retries=2, retry_delay_seconds=1)
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")
    morning = _task(scheduler, "morning_routine")

    scheduler._run_task(morning)

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == f"routine_failed:morning:{_today(scheduler)}"
    assert (notice.severity, notice.count) == ("CRITICAL", 1)
    assert notice.title == "Morning routine failed after 3 attempts"
    assert "broker down" in notice.detail
    assert notice.report_path == "reports/daily/x.md"
    assert notice.market_date == _today(scheduler)

    scheduler._run_task(morning)  # run again by hand the same day; it fails again

    [notice] = scheduler.notice_store.all_notices()
    assert notice.count == 2


def test_a_completed_run_resolves_the_days_failure_without_acknowledging_it(make_scheduler):
    scheduler = make_scheduler(max_retries=0)
    morning = _task(scheduler, "morning_routine")
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")
    scheduler._run_task(morning)
    scheduler.trading_cycle.morning_routine.side_effect = None

    scheduler._run_task(morning)

    [notice] = scheduler.notice_store.open_notices()  # still there to be read and acknowledged
    assert notice.resolved_at is not None and notice.acked_at is None


def test_a_dry_run_resolves_too_but_a_failed_dry_run_still_raises(make_scheduler):
    scheduler = make_scheduler(max_retries=0, dry_run=True)
    morning = _task(scheduler, "morning_routine")
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")
    scheduler._run_task(morning)
    assert _keys(scheduler) == [f"routine_failed:morning:{_today(scheduler)}"]
    scheduler.trading_cycle.morning_routine.side_effect = None

    scheduler._run_task(morning)

    assert scheduler.notice_store.all_notices()[0].resolved_at is not None


def test_the_notice_is_filed_under_the_trading_day_the_run_was_for(make_scheduler, monkeypatch):
    scheduler = make_scheduler(max_retries=0)
    monkeypatch.setattr(scheduler, "_market_now", lambda: _et(2026, 10, 15, 9, 20))
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")

    log = scheduler._run_task(_task(scheduler, "morning_routine"))

    assert log.market_date == "2026-10-15"
    assert _keys(scheduler) == ["routine_failed:morning:2026-10-15"]
    assert ("morning", "2026-10-15") in scheduler.state_manager.executions_on(["2026-10-15"])


def test_a_notice_problem_never_fails_or_hides_a_routine(make_scheduler, caplog):
    scheduler = make_scheduler()
    scheduler._notice_store = Mock(
        raise_notice=Mock(side_effect=OSError("disk full")),
        resolve_kinds=Mock(side_effect=OSError("disk full")),
    )
    morning = _task(scheduler, "morning_routine")

    log = scheduler._run_task(morning)

    assert log.status == "completed"
    assert scheduler.state_manager.get_last_execution("morning").status == "completed"
    assert "Notices not recorded for morning_routine" in caplog.text


# --- Positions --------------------------------------------------------------------------


def _alert(symbol, alert_type, severity, message):
    return PositionAlertSummary(
        symbol=symbol,
        alert_type=alert_type,
        severity=severity,
        message=message,
        timestamp="2026-10-27T09:20:00",
        current_price=170.0,
        details={},
    )


def _unprotected(symbol="SPY", **details):
    return Discrepancy(
        type="MISSING_STOP",
        symbol=symbol,
        details=details or {"reason": "no stop level below the current price to place"},
        action="NEEDS_HUMAN_REVIEW",
        severity="HIGH",
    )


STOP = _alert(
    "AAPL",
    "loss_threshold_reached",
    "CRITICAL",
    "🚨 AAPL is at or below its stop $171.00 (price $170.00), still open",
)
TARGET = _alert(
    "MSFT",
    "profit_target_reached",
    "CRITICAL",
    "🚨 MSFT is at or above its target $450.00 (price $451.00), still open",
)
NEAR_STOP = _alert(
    "SPY", "approaching_sl", "WARNING", "⚠️ SPY is 0.74% above its stop $744.47 (price $750.00)"
)


def test_a_critical_alert_raises_a_notice_and_a_warning_alert_does_not(make_scheduler):
    mild = _alert("GLD", "loss_threshold_reached", "WARNING", "GLD is at or below its stop")
    scheduler = make_scheduler(cycle=_cycle(alerts=[STOP, TARGET, NEAR_STOP, mild]))
    day = _today(scheduler)

    scheduler._run_task(_task(scheduler, "morning_routine"))

    notices = {n.key: n for n in scheduler.notice_store.all_notices()}
    assert set(notices) == {f"stop_breach:AAPL:{day}", f"target_breach:MSFT:{day}"}
    stop, target = notices[f"stop_breach:AAPL:{day}"], notices[f"target_breach:MSFT:{day}"]
    assert (stop.severity, target.severity) == ("CRITICAL", "WARNING")
    assert stop.title == "AAPL is at or below its stop, still open"
    assert stop.detail == "AAPL is at or below its stop $171.00 (price $170.00), still open"
    assert (stop.symbol, stop.report_path) == ("AAPL", "reports/daily/x.md")


def test_the_same_breach_in_the_evening_is_the_same_notice(make_scheduler):
    scheduler = make_scheduler(cycle=_cycle(alerts=[STOP]))

    scheduler._run_task(_task(scheduler, "morning_routine"))
    scheduler._run_task(_task(scheduler, "evening_routine"))

    [notice] = scheduler.notice_store.all_notices()
    assert notice.count == 2


def test_a_dry_run_raises_no_position_notices(make_scheduler):
    cycle = _cycle(alerts=[STOP, TARGET], discrepancies=[_unprotected()])
    scheduler = make_scheduler(cycle=cycle, dry_run=True)

    scheduler._run_task(_task(scheduler, "morning_routine"))

    assert _keys(scheduler) == []


def test_a_position_left_without_a_stop_is_a_notice_that_lasts_until_it_has_one(make_scheduler):
    cycle = _cycle(
        discrepancies=[_unprotected("SPY"), _unprotected("QQQ", error="insufficient qty")]
    )
    scheduler = make_scheduler(cycle=cycle)
    morning = _task(scheduler, "morning_routine")

    scheduler._run_task(morning)

    spy, qqq = scheduler.notice_store.all_notices()
    assert (spy.key, spy.severity, spy.symbol) == ("unprotected:SPY", "CRITICAL", "SPY")
    assert spy.title == "SPY has no protective stop"
    assert spy.detail == "no stop level below the current price to place"
    assert qqq.detail == "insufficient qty"

    scheduler._run_task(_task(scheduler, "evening_routine"))  # still unprotected that evening
    assert [n.count for n in scheduler.notice_store.all_notices()] == [2, 2]

    cycle.last_discrepancies = (_unprotected("SPY"),)  # QQQ has a stop again
    scheduler._run_task(morning)
    spy, qqq = scheduler.notice_store.all_notices()
    assert (spy.resolved_at, qqq.resolved_at is not None) == (None, True)

    cycle.last_discrepancies = ()
    scheduler._run_task(morning)
    assert all(n.resolved_at for n in scheduler.notice_store.all_notices())


def test_stops_that_were_placed_and_other_discrepancies_are_not_unprotected(make_scheduler):
    placed = Discrepancy("MISSING_STOP", "SPY", {"error": None}, "AUTO_FIXED", "HIGH")
    other = Discrepancy("UNKNOWN_POSITION", "QQQ", {}, "NEEDS_HUMAN_REVIEW", "HIGH")
    expiring = Discrepancy("STOP_EXPIRING", "IWM", {}, "NEEDS_HUMAN_REVIEW", "HIGH")
    scheduler = make_scheduler(cycle=_cycle(discrepancies=[placed, other, expiring]))

    scheduler._run_task(_task(scheduler, "morning_routine"))

    assert _keys(scheduler) == []


def test_a_routine_that_failed_to_place_a_stop_leaves_both_notices(make_scheduler):
    unplaced = _unprotected("SPY", error="insufficient qty available")
    scheduler = make_scheduler(cycle=_cycle(discrepancies=[unplaced]), max_retries=0)
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError(
        "protective stops not secured (SPY)"
    )

    scheduler._run_task(_task(scheduler, "morning_routine"))

    assert sorted(_keys(scheduler)) == sorted(
        [f"routine_failed:morning:{_today(scheduler)}", "unprotected:SPY"]
    )


def test_a_failed_run_does_not_clear_an_unprotected_notice(make_scheduler):
    cycle = _cycle(discrepancies=[_unprotected("SPY")])
    scheduler = make_scheduler(cycle=cycle, max_retries=0)
    morning = _task(scheduler, "morning_routine")
    scheduler._run_task(morning)
    cycle.last_discrepancies = ()  # a run that fails before it looks says nothing about SPY
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")

    scheduler._run_task(morning)

    assert {n.key: n.resolved_at for n in scheduler.notice_store.all_notices()}[
        "unprotected:SPY"
    ] is None


def test_a_cycle_without_results_raises_no_position_notices(make_scheduler, caplog):
    scheduler = make_scheduler(cycle=Mock(last_report_path="reports/daily/x.md"))  # mock attributes

    log = scheduler._run_task(_task(scheduler, "morning_routine"))

    assert log.status == "completed" and _keys(scheduler) == []
    assert "Notices not recorded" not in caplog.text


def test_a_recovery_run_raises_no_position_notices(make_scheduler):
    scheduler = make_scheduler(cycle=_cycle(alerts=[STOP], discrepancies=[_unprotected()]))
    recovery = daily_scheduler.ScheduledTask(
        name="recovery_routine",
        routine_type=daily_scheduler.RoutineType.RECOVERY,
        scheduled_time=daily_scheduler.dt_time(9, 0),
    )

    scheduler._run_task(recovery)

    assert _keys(scheduler) == []


# --- What the trading cycle keeps for the scheduler -----------------------------------


@pytest.fixture
def bare_cycle(tmp_path, monkeypatch):
    """A trading cycle with its broker and state steps stubbed out."""
    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", False)
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: True)
    cycle = object.__new__(CostEfficientTradeCycle)
    cycle.fetch_broker_state = Mock(return_value={"positions": {}, "orders": {}})
    cycle.reconcile_state = Mock(return_value=[])
    cycle.check_and_repair_stops = Mock(return_value=[])
    cycle.check_position_alerts = Mock(return_value=[])
    cycle.check_gtt_triggers = Mock(return_value=[])
    cycle.calculate_stop_adjustments = Mock(return_value=[])
    cycle.trailing_stop_manager = Mock()
    cycle.local_state_manager = Mock(state={})
    cycle.generate_routine_report = Mock(return_value="report")
    cycle._get_report_file_path = lambda routine: str(tmp_path / f"{routine}.md")
    cycle.record_daily = Mock(return_value=[])
    cycle.invalidate_broker_cache = Mock()
    cycle.last_report_path = None
    return cycle


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_the_cycle_keeps_the_routines_alerts_and_discrepancies(bare_cycle, routine):
    unknown = Discrepancy("UNKNOWN_POSITION", "QQQ", {}, "NEEDS_HUMAN_REVIEW", "HIGH")
    unprotected = _unprotected("SPY")
    bare_cycle.reconcile_state.return_value = [unknown]
    bare_cycle.check_and_repair_stops.return_value = [unprotected]
    bare_cycle.check_position_alerts.return_value = [STOP]
    assert bare_cycle.last_discrepancies == () and bare_cycle.notice_store is None  # class defaults

    getattr(bare_cycle, routine)(raise_errors=True)

    assert bare_cycle.last_discrepancies == (unknown, unprotected)
    assert bare_cycle.last_alerts == (STOP,)
    assert bare_cycle.last_report_path.endswith(".md")


def test_a_routine_that_fails_early_leaves_nothing_of_the_one_before(bare_cycle):
    bare_cycle.check_and_repair_stops.return_value = [_unprotected("SPY")]
    bare_cycle.check_position_alerts.return_value = [STOP]
    bare_cycle.morning_routine(raise_errors=True)
    assert bare_cycle.last_alerts and bare_cycle.last_discrepancies and bare_cycle.last_report_path

    bare_cycle.fetch_broker_state.side_effect = ConnectionError("broker down")
    with pytest.raises(RoutineError, match="broker down"):
        bare_cycle.evening_routine(raise_errors=True)

    assert bare_cycle.last_alerts == () and bare_cycle.last_discrepancies == ()
    assert bare_cycle.last_report_path is None  # not the morning's report


def test_the_discrepancies_of_a_routine_that_fails_to_place_a_stop_are_kept(bare_cycle):
    unplaced = _unprotected("SPY", error="insufficient qty available")
    bare_cycle.check_and_repair_stops.return_value = [unplaced]

    with pytest.raises(RoutineError, match="SPY"):  # the report is saved first
        bare_cycle.morning_routine(raise_errors=True)

    assert bare_cycle.last_discrepancies == (unplaced,)
    assert bare_cycle.last_report_path.endswith("morning.md")


def test_notices_flow_from_a_real_routine_through_the_scheduler_into_the_next_report(
    make_scheduler, bare_cycle
):
    bare_cycle.check_and_repair_stops.return_value = [_unprotected("SPY")]
    bare_cycle.check_position_alerts.return_value = [STOP]
    scheduler = make_scheduler(cycle=bare_cycle)

    log = scheduler._run_task(_task(scheduler, "evening_routine"))

    assert log.status == "completed"
    assert sorted(_keys(scheduler)) == sorted(
        [f"stop_breach:AAPL:{_today(scheduler)}", "unprotected:SPY"]
    )
    report = bare_cycle.morning_routine(raise_errors=True)  # the cycle was given the store
    assert "## Open Notices" in report
    assert "SPY has no protective stop" in report and "AAPL is at or below its stop" in report


def test_a_report_lists_the_latest_ten_open_notices_and_counts_the_rest(bare_cycle, tmp_path):
    bare_cycle.notice_store = NoticeStore(tmp_path / "notices.json", raise_errors=True)
    for n in range(1, 13):
        bare_cycle.notice_store.raise_notice(
            f"stop_breach:S{n}:2026-10-27", "stop_breach", "CRITICAL", f"S{n} breach"
        )

    lines = bare_cycle._open_notice_lines()

    listed = [line for line in lines if "breach" in line]
    assert len(listed) == 10 and "S3 breach" in listed[0] and "S12 breach" in listed[-1]
    assert lines[-1].startswith("- ... and 2 older ones")


# --- Routines that never ran ------------------------------------------------------------


def test_a_routine_with_no_run_is_missed_once_its_deadline_and_a_grace_period_have_passed(
    make_scheduler,
):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")  # Wednesday

    scheduler.check_routine_outcomes(
        now=_et(2026, 10, 15, 10, 24)
    )  # Thursday, before the catch-up deadline
    scheduler.check_routine_outcomes(
        now=_et(2026, 10, 15, 10, 24, 59)
    )  # deadline + 5, less a second
    assert _keys(scheduler) == []

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 26))

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == "routine_missed:morning:2026-10-15"
    assert (notice.severity, notice.market_date) == ("CRITICAL", "2026-10-15")
    assert notice.title == "Morning routine did not run on 2026-10-15"
    assert "09:20 ET morning routine" in notice.detail and "cron" in notice.detail


def test_the_evening_routine_is_missed_after_its_own_time(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    _record(scheduler, "morning", "2026-10-15")

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 16, 3))
    assert _keys(scheduler) == []
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 16, 5))

    assert _keys(scheduler) == ["routine_missed:evening:2026-10-15"]


def test_an_early_close_moves_the_evening_routine_and_so_its_miss(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-11-25")
    _record(scheduler, "morning", "2026-11-27")  # the day after Thanksgiving closes at 13:00

    scheduler.check_routine_outcomes(now=_et(2026, 11, 27, 13, 3))
    assert _keys(scheduler) == []
    scheduler.check_routine_outcomes(now=_et(2026, 11, 27, 13, 5))  # the 12:59 deadline + 5 = 13:04

    assert _keys(scheduler) == ["routine_missed:evening:2026-11-27"]


@pytest.mark.parametrize(
    "now, previous_day",
    [
        (_et(2026, 10, 17, 12, 0), "2026-10-16"),  # Saturday: Friday's runs are checked
        (_et(2026, 11, 26, 12, 0), "2026-11-25"),  # Thanksgiving
    ],
)
def test_no_routine_is_due_on_a_day_without_a_session(make_scheduler, calendar, now, previous_day):
    scheduler = make_scheduler()
    _both(scheduler, previous_day)

    scheduler.check_routine_outcomes(now=now)

    assert _keys(scheduler) == []
    assert calendar == []  # no should_run_task ran first: both days from the NYSE rules


def test_a_scheduler_disabled_in_the_config_file_is_not_missed(make_scheduler):
    scheduler = make_scheduler(enabled=False)

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 16, 30))

    assert _keys(scheduler) == []


def test_a_scheduler_disabled_from_the_cli_is_not_missed(make_scheduler):
    scheduler = make_scheduler()
    scheduler.save_flags(enabled=False)  # as the CLI's `disable` does

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 16, 30))

    assert _keys(scheduler) == []


def test_only_the_routines_still_enabled_can_be_missed(make_scheduler):
    scheduler = make_scheduler(evening_routine_enabled=False)
    _both(scheduler, "2026-10-14")

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 16, 30))

    assert _keys(scheduler) == ["routine_missed:morning:2026-10-15"]


@pytest.mark.parametrize("status", ["completed", "dry_run"])
def test_a_completed_or_dry_run_is_no_miss_and_clears_an_earlier_one(make_scheduler, status):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 26))
    assert _keys(scheduler) == ["routine_missed:morning:2026-10-15"]

    _record(scheduler, "morning", "2026-10-15", status)
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 31))

    [notice] = scheduler.notice_store.all_notices()
    assert notice.resolved_at is not None


@pytest.mark.parametrize("status", ["completed", "dry_run"])
def test_a_routine_that_ran_is_not_missed(make_scheduler, status):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    _record(scheduler, "morning", "2026-10-15", status)

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))

    assert _keys(scheduler) == []


def test_a_scheduler_switched_on_after_the_window_excuses_the_miss(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    _switched_on(scheduler, _et(2026, 10, 15, 10, 21))  # the catch-up deadline was 10:20

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 26))

    assert _keys(scheduler) == []


def test_a_scheduler_switched_on_before_the_window_ended_does_not(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    _switched_on(scheduler, _et(2026, 10, 15, 9, 24))  # inside the 09:25 window

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 26))

    assert _keys(scheduler) == ["routine_missed:morning:2026-10-15"]


def test_only_a_save_that_switches_a_routine_on_stamps_it(make_scheduler, monkeypatch):
    scheduler = make_scheduler()
    clock = iter(f"2026-10-15T{hour}:00:00" for hour in ("08", "10", "11", "12", "13"))
    monkeypatch.setattr(scheduler_state, "now_iso", lambda: next(clock))

    scheduler.save_flags(enabled=True, morning=True, evening=False)  # 08:00 morning on
    scheduler.save_flags(enabled=True, morning=True, evening=False)  # 10:00 nothing switched
    scheduler.save_flags(evening=True)  # 11:00 evening on
    state = scheduler.state_manager.get_state()
    assert (state.morning_enabled_at, state.evening_enabled_at) == (
        "2026-10-15T08:00:00",
        "2026-10-15T11:00:00",
    )

    scheduler.save_flags(enabled=False)  # 12:00
    scheduler.save_flags(enabled=True)  # 13:00 off and on again: both switched on
    state = scheduler.state_manager.get_state()
    assert state.morning_enabled_at == state.evening_enabled_at == "2026-10-15T13:00:00"


def test_a_save_that_switches_nothing_on_does_not_excuse_a_miss(make_scheduler):
    """Switching the evening on at 10:30 says nothing about the morning's 09:20 run."""
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    _switched_on(scheduler, _et(2026, 10, 14, 8, 0))
    with sqlite3.connect(scheduler.state_manager._db_path) as conn:
        conn.execute(
            "UPDATE scheduler_state SET evening_enabled_at = ?",
            (_et(2026, 10, 15, 10, 30).astimezone().replace(tzinfo=None).isoformat(),),
        )

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 35))

    assert _keys(scheduler) == ["routine_missed:morning:2026-10-15"]


def test_an_older_database_gains_the_stamp_columns_and_keeps_its_flags(tmp_path):
    """The live state/user.db's row: made before #601 (timeout_seconds), saved on 10-15."""
    db = tmp_path / "user.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE scheduler_state (id INTEGER PRIMARY KEY CHECK (id = 1), "
            "enabled BOOLEAN DEFAULT FALSE, morning_enabled BOOLEAN DEFAULT TRUE, "
            "evening_enabled BOOLEAN DEFAULT FALSE, morning_time TEXT DEFAULT '09:20:00', "
            "evening_time TEXT DEFAULT '15:50:00', max_retries INTEGER DEFAULT 3, "
            "retry_delay_seconds INTEGER DEFAULT 60, timeout_seconds INTEGER DEFAULT 300, "
            "updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO scheduler_state VALUES "
            "(1, 1, 1, 1, '09:20:00', '15:50:00', 3, 60, 300, '2026-10-15T03:23:26.987347')"
        )

    state = SchedulerStateManager(str(db)).get_state()

    assert (state.enabled, state.morning_enabled, state.evening_enabled) == (True, True, True)
    assert (state.morning_enabled_at, state.evening_enabled_at) == (None, None)


def test_a_database_saved_before_the_stamps_existed_excuses_nothing(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    scheduler.save_flags(enabled=True)
    with sqlite3.connect(scheduler.state_manager._db_path) as conn:
        conn.execute(
            "UPDATE scheduler_state SET morning_enabled_at = NULL, evening_enabled_at = NULL"
        )

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 26))

    assert _keys(scheduler) == ["routine_missed:morning:2026-10-15"]


# --- Routines that failed or never finished ---------------------------------------------


@pytest.mark.parametrize(
    "minutes_old, expected",
    [(45, ["routine_stuck:morning:2026-10-15"]), (5, [])],
)
def test_a_run_that_started_and_never_finished_is_stuck_after_30_minutes(
    make_scheduler, minutes_old, expected
):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    now = _et(2026, 10, 15, 10, 5)
    _record(
        scheduler, "morning", "2026-10-15", "running", started=now - timedelta(minutes=minutes_old)
    )

    scheduler.check_routine_outcomes(now=now)

    assert _keys(scheduler) == expected  # nor is it "missed": a run is recorded
    if expected:
        [notice] = scheduler.notice_store.all_notices()
        assert notice.severity == "CRITICAL" and "09:20 ET" in notice.detail
        assert "never finished" in notice.title


def test_a_stuck_run_that_finishes_clears_its_notice(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    run_id = _record(
        scheduler, "morning", "2026-10-15", "running", started=_et(2026, 10, 15, 9, 20)
    )
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 5))
    assert _keys(scheduler) == ["routine_stuck:morning:2026-10-15"]

    scheduler.state_manager.record_execution_complete(run_id, status="completed")
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 10, 10))

    assert scheduler.notice_store.all_notices()[0].resolved_at is not None


def test_a_failed_run_is_found_in_the_history_without_a_second_notice(make_scheduler):
    scheduler = make_scheduler(max_retries=0)
    _both(scheduler, "2026-10-14")
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")
    now = _et(2026, 10, 15, 9, 20)
    scheduler._market_now = lambda: now
    scheduler._run_task(_task(scheduler, "morning_routine"))  # raises the notice itself

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))

    [notice] = scheduler.notice_store.all_notices()
    assert (notice.key, notice.count) == ("routine_failed:morning:2026-10-15", 1)


def test_a_failure_the_history_remembers_but_no_notice_does_is_raised_from_the_history(
    make_scheduler,
):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    run_id = _record(scheduler, "morning", "2026-10-15", "running")
    scheduler.state_manager.record_execution_complete(
        run_id, status="failed", error_message="Attempt 4 failed: key=abc123 broker down"
    )

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == "routine_failed:morning:2026-10-15"
    assert notice.title == "Morning routine failed"
    assert "broker down" in notice.detail and "abc123" not in notice.detail


@pytest.mark.parametrize(
    "statuses, expected",
    [
        (["failed", "completed"], RunOutcome.OK),  # a manual retry fixed it
        (["running", "dry_run"], RunOutcome.OK),
        (["running", "failed"], RunOutcome.FAILED),
        ([], RunOutcome.MISSED),
    ],
)
def test_the_outcome_of_a_days_runs(statuses, expected):
    now = _et(2026, 10, 15, 12, 0)
    runs = [
        SchedulerExecution(
            id=i, routine_type="morning", status=status, started_at="2026-10-15T13:20:00"
        )
        for i, status in enumerate(statuses)
    ]

    assert run_outcome(runs, now)[0] is expected


# --- The previous trading day -----------------------------------------------------------


def test_a_missing_evening_run_is_found_on_the_next_trading_day_once(make_scheduler, calendar):
    scheduler = make_scheduler()
    _record(scheduler, "morning", "2026-10-23")  # Friday: no evening run

    scheduler.check_routine_outcomes(now=_et(2026, 10, 26, 8, 0))  # Monday morning
    scheduler.check_routine_outcomes(now=_et(2026, 10, 26, 8, 5))  # the next cron tick

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == "routine_missed:evening:2026-10-23"
    assert notice.count == 1
    assert calendar == []  # the past day, and today without a should_run_task, from the rules


def test_the_previous_trading_day_skips_holidays_and_weekends(make_scheduler):
    scheduler = make_scheduler()
    _record(scheduler, "morning", "2026-11-25")  # the Wednesday before Thanksgiving

    scheduler.check_routine_outcomes(now=_et(2026, 11, 27, 8, 0))  # Friday: Thursday was closed

    assert _keys(scheduler) == ["routine_missed:evening:2026-11-25"]


@pytest.mark.parametrize(
    "saved, expected",
    [
        (_et(2026, 11, 27, 12, 52), ["routine_missed:evening:2026-11-27"]),
        (_et(2026, 11, 27, 13, 0), []),  # after the early-close evening window (12:55)
    ],
)
def test_the_previous_days_early_close_moves_its_evening_window(make_scheduler, saved, expected):
    """15:50 + 10 would put the deadline at 16:00, after both of these saves."""
    scheduler = make_scheduler()
    _record(scheduler, "morning", "2026-11-27")  # the day after Thanksgiving closes at 13:00
    _switched_on(scheduler, saved)

    scheduler.check_routine_outcomes(now=_et(2026, 11, 30, 8, 0))  # the Monday after

    assert _keys(scheduler) == expected


# --- The check itself -------------------------------------------------------------------


def test_the_outcome_check_does_not_call_should_run_task(make_scheduler):
    scheduler = make_scheduler()
    scheduler.should_run_task = Mock(return_value=True)

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 16, 30))

    scheduler.should_run_task.assert_not_called()
    assert "routine_missed:evening:2026-10-15" in _keys(scheduler)


def test_the_outcome_check_reuses_the_calendar_lookup_should_run_task_made(
    make_scheduler, calendar
):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")
    now = _et(2026, 10, 15, 9, 31)
    for task in scheduler.tasks:
        scheduler.should_run_task(task, now=now)

    scheduler.check_routine_outcomes(now=now)

    assert calendar == [date(2026, 10, 15)]  # one lookup served both tasks and the check


@pytest.mark.parametrize(
    "now, previous, expected",
    [
        (_et(2026, 10, 15, 10, 5), "2026-10-14", ["routine_stuck:morning:2026-10-15"]),  # Thursday
        (_et(2026, 10, 17, 10, 5), "2026-10-16", []),  # Saturday
        (_et(2026, 11, 26, 10, 5), "2026-11-25", []),  # Thanksgiving
    ],
)
def test_without_a_calendar_answer_the_check_takes_today_from_the_nyse_rules(
    make_scheduler, calendar, now, previous, expected
):
    """As on a tick that didn't get the lock: should_run_task never asked the calendar."""
    scheduler = make_scheduler()
    _both(scheduler, previous)
    if expected:
        started = now - timedelta(minutes=45)
        _record(scheduler, "morning", now.date().isoformat(), "running", started=started)

    scheduler.check_routine_outcomes(now=now)

    assert _keys(scheduler) == expected  # nothing "missed" on a day without a session
    assert calendar == []


def test_a_tick_that_did_not_get_the_lock_makes_no_calendar_call(make_scheduler, calendar):
    scheduler = make_scheduler()

    with open(get_state_dir() / "scheduler.lock", "a") as other_process:
        fcntl.flock(other_process, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scheduler.run_scheduled_tasks()
        fcntl.flock(other_process, fcntl.LOCK_UN)

    assert calendar == []


def test_every_tick_checks_the_outcomes_whether_or_not_it_got_the_lock(make_scheduler):
    scheduler = make_scheduler()
    scheduler.should_run_task = Mock(return_value=False)
    scheduler.check_routine_outcomes = Mock()

    scheduler.run_scheduled_tasks()
    assert scheduler.should_run_task.call_count == 2
    scheduler.check_routine_outcomes.assert_called_once_with()

    with open(get_state_dir() / "scheduler.lock", "a") as other_process:
        fcntl.flock(other_process, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scheduler.run_scheduled_tasks()
        fcntl.flock(other_process, fcntl.LOCK_UN)

    assert scheduler.should_run_task.call_count == 2  # not asked while the lock was held
    assert scheduler.check_routine_outcomes.call_count == 2


def test_a_tick_that_ran_a_routine_checks_the_outcomes_after_it(make_scheduler):
    scheduler = make_scheduler(max_retries=0)
    scheduler.should_run_task = lambda task: task.name == "morning_routine"
    scheduler.trading_cycle.morning_routine.side_effect = RoutineError("broker down")
    seen = []
    scheduler.check_routine_outcomes = lambda: seen.append(
        [n.key for n in scheduler.notice_store.all_notices()]
    )

    ran = scheduler.run_scheduled_tasks()

    assert [entry.status for entry in ran] == ["failed"]  # main.py --run-due exits 1
    assert seen == [[f"routine_failed:morning:{_today(scheduler)}"]]


def test_an_unreadable_history_is_a_notice_and_the_check_does_not_raise(
    make_scheduler, monkeypatch
):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")

    def unreadable(days):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(scheduler.state_manager, "executions_on", unreadable)
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 36))

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == "state_db_error:2026-10-15"
    assert (notice.severity, notice.count) == ("CRITICAL", 1)
    assert "disk I/O error" in notice.detail

    scheduler.state_manager = SchedulerStateManager()  # readable again
    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 41))
    by_key = {n.key: n for n in scheduler.notice_store.all_notices()}
    assert by_key["state_db_error:2026-10-15"].resolved_at is not None


def test_a_state_manager_that_fails_everywhere_is_the_same_notice(make_scheduler):
    scheduler = make_scheduler()
    broken = Mock(spec=SchedulerStateManager)
    for name in ("has_saved_state", "get_state", "read_state", "has_run_history", "executions_on"):
        getattr(broken, name).side_effect = sqlite3.OperationalError("database is locked")
    scheduler.state_manager = broken

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))

    assert _keys(scheduler) == ["state_db_error:2026-10-15"]


def test_unreadable_flags_are_the_notice_even_though_the_config_says_disabled(
    make_scheduler, monkeypatch, tmp_path
):
    """E1-05: the real manager used to answer "never saved", and the config's false hid it."""
    scheduler = make_scheduler(enabled=False)
    scheduler.save_flags(enabled=True)

    def locked():
        raise sqlite3.OperationalError("database is locked")

    # The fault is scoped to a context: monkeypatch.undo() would also undo the autouse
    # fixtures, the temporary state dir first of all
    with monkeypatch.context() as broken:
        broken.setattr(scheduler.state_manager, "_get_connection", locked)
        scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))
        scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 36))
        scheduler.check_routine_outcomes(now=_et(2026, 10, 16, 9, 31))  # the next day

    assert os.environ.get(STATE_DIR_ENV, "").startswith(str(tmp_path))
    notices = {n.key: n for n in scheduler.notice_store.all_notices()}
    assert sorted(notices) == ["state_db_error:2026-10-15", "state_db_error:2026-10-16"]
    assert [(n.severity, n.count) for n in notices.values()] == [("CRITICAL", 1)] * 2
    assert all("database is locked" in n.detail for n in notices.values())

    scheduler.check_routine_outcomes(now=_et(2026, 10, 16, 9, 36))  # readable: the saved True
    assert [n.resolved_at is not None for n in scheduler.notice_store.all_notices()] == [True, True]


def test_a_bug_in_the_outcome_check_does_not_stop_the_tick(make_scheduler, monkeypatch, caplog):
    scheduler = make_scheduler()
    monkeypatch.setattr(scheduler, "_check_day", Mock(side_effect=ZeroDivisionError("bug")))

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))  # no exception

    assert "Routine outcomes not checked" in caplog.text


def test_a_notice_store_that_fails_does_not_stop_the_tick(make_scheduler, caplog):
    scheduler = make_scheduler()
    scheduler._notice_store = Mock(raise_notice=Mock(side_effect=OSError("disk full")))

    scheduler.check_routine_outcomes(now=_et(2026, 10, 15, 9, 31))  # a miss to raise: no exception

    assert "Routine outcomes not checked" in caplog.text


def test_a_time_without_a_zone_is_read_as_market_time(make_scheduler):
    scheduler = make_scheduler()
    _both(scheduler, "2026-10-14")

    scheduler.check_routine_outcomes(now=datetime(2026, 10, 15, 10, 26))

    assert _keys(scheduler) == ["routine_missed:morning:2026-10-15"]


# --- The history reader -----------------------------------------------------------------


def test_executions_on_groups_runs_by_routine_and_day_oldest_first(make_scheduler):
    scheduler = make_scheduler()
    failed = _record(scheduler, "morning", "2026-10-15", "failed", started=_et(2026, 10, 15, 9, 20))
    retried = _record(scheduler, "morning", "2026-10-15", started=_et(2026, 10, 15, 9, 26))
    evening = _record(scheduler, "evening", "2026-10-15", started=_et(2026, 10, 15, 15, 50))
    _record(scheduler, "morning", "2026-10-16")
    legacy = _record(scheduler, "morning", "2026-10-14")
    with sqlite3.connect(scheduler.state_manager._db_path) as conn:  # saved before market_date
        conn.execute(
            "UPDATE scheduler_history SET market_date = NULL, started_at = '2026-10-14T13:20:00' "
            "WHERE id = ?",
            (legacy,),
        )

    runs = scheduler.state_manager.executions_on(["2026-10-14", "2026-10-15"])

    assert sorted(runs) == [
        ("evening", "2026-10-15"),
        ("morning", "2026-10-14"),
        ("morning", "2026-10-15"),
    ]
    assert [r.id for r in runs[("morning", "2026-10-15")]] == [failed, retried]
    assert [r.id for r in runs[("evening", "2026-10-15")]] == [evening]
    assert [r.id for r in runs[("morning", "2026-10-14")]] == [legacy]
    assert scheduler.state_manager.executions_on([]) == {}


def test_executions_on_raises_on_a_database_error_unlike_the_other_readers(tmp_path):
    manager = SchedulerStateManager(db_path=str(tmp_path / "missing" / "user.db"))

    assert manager.get_execution_history() == []  # the other readers log it and move on
    with pytest.raises(sqlite3.OperationalError):
        manager.executions_on(["2026-10-15"])


# --- The cron entry point ---------------------------------------------------------------


def test_a_crashed_cron_check_leaves_a_notice_and_still_raises(tmp_path):
    """Run in a subprocess: importing main changes sys.path and builds the CLI."""
    work, state = tmp_path / "cwd", tmp_path / "state"
    work.mkdir()
    code = textwrap.dedent(
        """
        import sys
        import main

        class Crashing:
            def warn_if_retries_exceed(self, limit_seconds):
                pass

            def run_scheduled_tasks(self):
                raise RuntimeError("tick failed: api_key=abc123")

        main.DailyScheduler = Crashing
        main.configure_scheduler_logging = lambda: None
        try:
            main._run_due_mode()
        except RuntimeError as error:
            print("RERAISED", error)

        # With the notices module gone, the error still surfaces and nothing else is raised
        sys.modules["src.trading.scheduling.notices"] = None

        class Failing(Crashing):
            def run_scheduled_tasks(self):
                raise KeyError("second")

        main.DailyScheduler = Failing
        try:
            main._run_due_mode()
        except KeyError as error:
            print("RERAISED", error)
        """
    )
    env = dict(os.environ)
    env.pop("RUN_PAPER_ORDER_TESTS", None)
    env.update(
        AUTOTRADER_STATE_DIR=str(state),
        AUTOTRADER_CACHE_DIR=str(tmp_path / "cache"),
        PYTHONPATH=str(REPO),
        PYTHONDONTWRITEBYTECODE="1",
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    reraised = [line for line in result.stdout.splitlines() if line.startswith("RERAISED")]
    assert reraised == ["RERAISED tick failed: api_key=abc123", "RERAISED 'second'"]
    saved = json.loads((state / "notices.json").read_text())
    [notice] = saved["notices"].values()  # the KeyError left none: the module was unavailable
    assert notice["key"].startswith("run_due_error:") and notice["key"].endswith(":RuntimeError")
    assert (notice["severity"], notice["count"]) == ("CRITICAL", 1)
    assert "abc123" not in json.dumps(saved)
