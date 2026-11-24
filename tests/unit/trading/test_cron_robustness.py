"""
The unattended cron loop (#259): a watchdog for a check that hangs, a catch-up window for a
routine whose tick was lost, atomic state writes, the empty-calendar warning and GTT triggers
that never lapse. Nothing leaves the machine: the calendar comes from the NYSE rules.
"""

import fcntl
import json
import logging
import signal
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import main

import src.utils.market_hours as mh
from src.trading.gtt import trailing_stop_bridge
from src.trading.gtt.gtt_manager import GTTManager
from src.trading.positions.position_manager import PositionManager
from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling.daily_scheduler import DailyScheduler
from src.trading.scheduling.notices import NoticeStore
from src.trading.scheduling.scheduler_state import SchedulerStateManager
from src.trading.scheduling.watchdog import (
    RUN_DUE_TIMEOUT_SECONDS,
    RunDueTimeoutError,
    run_due_timeout_seconds,
    watchdog,
)
from src.trading.state import local_state_manager
from src.utils.date_utils import get_datetime_now
from src.utils.state_paths import get_state_dir

ET = ZoneInfo("America/New_York")


def _et(*args):
    return datetime(*args, tzinfo=ET)


@pytest.fixture
def make_scheduler(tmp_path, monkeypatch):
    """A scheduler on its own state and notices, with the NYSE rules as the calendar."""
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler, "get_market_hours_for_date", mh._rule_based_hours)

    def make(cycle=None, **config):
        settings = {"enabled": True, **config}
        config_file = tmp_path / "scheduler_config.yaml"
        config_file.write_text("\n".join(f"{k}: {v!r}" for k, v in settings.items()))
        return DailyScheduler(
            config_file=str(config_file),
            trading_cycle=cycle
            or Mock(last_report_path=None, last_alerts=(), last_discrepancies=()),
            state_manager=SchedulerStateManager(),
            notice_store=NoticeStore(tmp_path / "notices.json", raise_errors=True),
        )

    return make


def _task(scheduler, name):
    return next(t for t in scheduler.tasks if t.name == name)


def _record(scheduler, routine, day, status="completed"):
    manager = scheduler.state_manager
    run_id = manager.record_execution_start(routine, market_date=day)
    manager.record_execution_complete(run_id, status=status)


# --- Watchdog ---------------------------------------------------------------------------


@pytest.fixture
def run_due(make_scheduler, monkeypatch):
    """main._run_due_mode with a scheduler on mock state; returns (scheduler, run)."""

    def build(limit=1, due="morning_routine", **config):
        scheduler = make_scheduler(**config)
        monkeypatch.setattr(
            scheduler, "should_run_task", lambda task, now=None: task.name == due, raising=False
        )
        monkeypatch.setattr(main, "DailyScheduler", lambda: scheduler)
        monkeypatch.setattr(main, "configure_scheduler_logging", lambda: None)
        monkeypatch.setattr(
            main, "load_scheduler_config", lambda: {"run_due_timeout_seconds": limit}
        )
        return scheduler

    return build


def _run_due_notices():
    return [n for n in NoticeStore().all_notices() if n.kind == "run_due_error"]


def test_a_hung_routine_is_cut_off_recorded_failed_and_noticed_once(run_due, tmp_path):
    scheduler = run_due(limit=1)
    scheduler.trading_cycle.morning_routine.side_effect = lambda **kw: time.sleep(30)
    started = time.monotonic()

    code = main._run_due_mode()

    assert time.monotonic() - started < 10
    assert code == 1
    [run] = scheduler.state_manager.executions_on([scheduler._market_now().date().isoformat()])[
        ("morning", scheduler._market_now().date().isoformat())
    ]
    assert run.status == "timed_out"
    assert "timed out after 1 s" in run.error_message
    [log] = scheduler.execution_log
    assert (log.status, "timed out after 1 s" in log.error_message) == ("timed_out", True)
    [notice] = scheduler.notice_store.all_notices()
    assert notice.kind == "routine_failed" and notice.severity == "CRITICAL"
    assert "timed out after 1 s" in notice.detail
    assert _run_due_notices() == []  # exactly one notice for the timeout
    assert scheduler.trading_cycle.morning_routine.call_count == 1  # not retried


def test_the_timeout_is_not_swallowed_by_the_retry_loop_or_the_routines_handlers(run_due):
    scheduler = run_due(limit=1, max_retries=3, retry_delay_seconds=60)
    attempts = []

    def hang(**kwargs):
        attempts.append(1)
        try:
            time.sleep(30)
        except Exception:  # a contained step: must not see the timeout
            attempts.append("swallowed")

    scheduler.trading_cycle.morning_routine.side_effect = hang

    assert main._run_due_mode() == 1
    assert attempts == [1]


def test_the_lock_is_free_and_the_alarm_disarmed_after_a_timeout(run_due):
    scheduler = run_due(limit=1)
    scheduler.trading_cycle.morning_routine.side_effect = lambda **kw: time.sleep(30)
    previous = signal.getsignal(signal.SIGALRM)

    main._run_due_mode()

    with open(get_state_dir() / "scheduler.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # would raise if still held
    assert signal.alarm(0) == 0
    assert signal.getsignal(signal.SIGALRM) == previous


def test_a_timeout_outside_a_routine_is_one_run_due_error_notice(run_due, monkeypatch):
    scheduler = run_due(limit=1, due="none")
    monkeypatch.setattr(
        scheduler, "check_routine_outcomes", lambda now=None: time.sleep(30), raising=False
    )

    assert main._run_due_mode() == 1

    [notice] = _run_due_notices()
    assert notice.severity == "CRITICAL" and "timed out after 1 s" in notice.detail
    assert scheduler.notice_store.all_notices() == []
    assert scheduler.execution_log == []


def test_a_normal_run_disarms_the_alarm_and_restores_the_handler(run_due):
    scheduler = run_due(limit=100)
    previous = signal.signal(signal.SIGALRM, signal.SIG_DFL)
    try:
        assert main._run_due_mode() == 0
        assert signal.alarm(0) == 0
        assert signal.getsignal(signal.SIGALRM) == signal.SIG_DFL
    finally:
        signal.signal(signal.SIGALRM, previous)
    scheduler.trading_cycle.morning_routine.assert_called_once()


def test_a_failed_routine_still_exits_1_and_other_errors_still_propagate(run_due, monkeypatch):
    scheduler = run_due(limit=100, max_retries=0)
    scheduler.trading_cycle.morning_routine.side_effect = RuntimeError("broker down")
    assert main._run_due_mode() == 1

    monkeypatch.setattr(main, "DailyScheduler", Mock(side_effect=ValueError("bad state")))
    with pytest.raises(ValueError):
        main._run_due_mode()
    [notice] = _run_due_notices()
    assert "ValueError" in notice.title


def test_the_timeout_derives_from_base_exception_and_is_named_like_an_error():
    assert issubclass(RunDueTimeoutError, BaseException)
    assert not issubclass(RunDueTimeoutError, Exception)
    assert RunDueTimeoutError.__name__.endswith("Error")


def test_the_limit_comes_from_the_config_or_the_default(caplog):
    assert RUN_DUE_TIMEOUT_SECONDS == 900
    assert run_due_timeout_seconds({}) == 900
    assert run_due_timeout_seconds({"run_due_timeout_seconds": 120}) == 120
    assert run_due_timeout_seconds({"run_due_timeout_seconds": 0.2}) == 1  # never 0: no alarm
    assert run_due_timeout_seconds({"run_due_timeout_seconds": "soon"}) == 900
    assert "Ignoring" in caplog.text


def test_without_sigalrm_there_is_no_watchdog(monkeypatch):
    monkeypatch.delattr(signal, "SIGALRM")

    with watchdog(1) as armed:
        assert armed is False


def test_retries_that_could_outlast_the_limit_are_warned_about(make_scheduler, caplog):
    scheduler = make_scheduler(max_retries=3, retry_delay_seconds=60)  # 60 + 120 + 240 s

    with caplog.at_level(logging.WARNING):
        scheduler.warn_if_retries_exceed(900)
        assert "retry delays" not in caplog.text
        scheduler.warn_if_retries_exceed(300)

    assert "retry delays total 420 s, past the 300 s run limit" in caplog.text


# --- Atomic writes ----------------------------------------------------------------------


def test_a_crash_before_the_rename_leaves_the_execution_log_whole(make_scheduler, monkeypatch):
    scheduler = make_scheduler()
    scheduler.execution_log = [
        daily_scheduler.ExecutionLog("morning_routine", "09:20:00", "now", "completed")
    ]
    scheduler._save_execution_log()
    before = scheduler.log_file.read_text()
    scheduler.execution_log.append(
        daily_scheduler.ExecutionLog("evening_routine", "15:50:00", "now", "completed")
    )

    def crash(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr(local_state_manager.os, "replace", crash)
    scheduler._save_execution_log()  # contained and logged, as before

    assert scheduler.log_file.read_text() == before
    assert len(json.loads(before)) == 1
    assert [p.name for p in scheduler.log_file.parent.iterdir() if p.suffix == ".tmp"] == []


def test_a_crash_before_the_rename_leaves_the_positions_backup_whole(tmp_path, monkeypatch):
    manager = object.__new__(PositionManager)
    manager.state_file = tmp_path / "positions.json"
    manager._save_positions_backup({"SPY": {"quantity": 1}})
    before = manager.state_file.read_text()

    monkeypatch.setattr(local_state_manager.os, "replace", Mock(side_effect=OSError("disk gone")))
    manager._save_positions_backup({"SPY": {"quantity": 2}})

    assert manager.state_file.read_text() == before
    assert json.loads(before)["positions"] == {"SPY": {"quantity": 1}}


# --- Catch-up window --------------------------------------------------------------------


def _can_run(scheduler, name, now):
    return scheduler.should_run_task(_task(scheduler, name), now=now)


@pytest.mark.parametrize(
    "hour, minute, expected",
    [
        (9, 19, False),  # not yet
        (9, 20, True),
        (9, 25, True),
        (9, 40, True),  # a lost tick is caught up
        (10, 20, True),  # 60 minutes after its time
        (10, 21, False),
    ],
)
def test_the_morning_routine_may_start_late_until_an_hour_after_its_time(
    make_scheduler, hour, minute, expected
):
    scheduler = make_scheduler()

    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, hour, minute)) is expected


@pytest.mark.parametrize(
    "hour, minute, expected",
    [(15, 49, False), (15, 50, True), (15, 55, True), (15, 59, True), (16, 0, False)],
)
def test_the_evening_routine_may_start_until_a_minute_before_the_close(
    make_scheduler, hour, minute, expected
):
    scheduler = make_scheduler()

    assert _can_run(scheduler, "evening_routine", _et(2026, 10, 14, hour, minute)) is expected


@pytest.mark.parametrize(
    "hour, minute, expected", [(12, 49, False), (12, 50, True), (12, 59, True), (13, 0, False)]
)
def test_on_an_early_close_the_evening_deadline_moves_with_the_close(
    make_scheduler, hour, minute, expected
):
    scheduler = make_scheduler()

    assert _can_run(scheduler, "evening_routine", _et(2026, 11, 27, hour, minute)) is expected


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_a_routine_that_completed_or_failed_does_not_start_again_late(make_scheduler, status):
    scheduler = make_scheduler()
    _record(scheduler, "morning", "2026-10-14", status)

    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 40)) is False
    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 22)) is False


def test_a_run_that_never_finished_does_not_count_as_the_days_routine(make_scheduler):
    scheduler = make_scheduler()
    scheduler.state_manager.record_execution_start("morning", market_date="2026-10-14")

    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 40)) is True


def test_a_late_start_is_logged(make_scheduler, caplog):
    scheduler = make_scheduler()

    with caplog.at_level(logging.INFO):
        _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 22))
        assert "late" not in caplog.text
        _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 40))

    assert "Starting morning_routine late: scheduled 09:20, now 09:40" in caplog.text


def test_a_dry_run_keeps_the_five_minute_window(make_scheduler):
    scheduler = make_scheduler(dry_run=True)

    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 25)) is True
    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 9, 26)) is False
    assert _can_run(scheduler, "evening_routine", _et(2026, 10, 14, 15, 56)) is False


def test_next_run_stays_on_a_routine_until_its_deadline(make_scheduler):
    scheduler = make_scheduler()

    task, _ = scheduler.next_run(_et(2026, 10, 14, 10, 20))
    assert task.name == "morning_routine"
    task, _ = scheduler.next_run(_et(2026, 10, 14, 10, 21))
    assert task.name == "evening_routine"

    _record(scheduler, "morning", "2026-10-14")
    task, _ = scheduler.next_run(_et(2026, 10, 14, 9, 40))
    assert task.name == "evening_routine"

    _record(scheduler, "evening", "2026-10-14")
    task, when = scheduler.next_run(_et(2026, 10, 14, 15, 59))
    assert (task.name, when.date()) == ("morning_routine", date(2026, 10, 15))


def test_a_missed_morning_is_reported_only_after_the_deadline_and_grace(make_scheduler):
    scheduler = make_scheduler()
    _record(scheduler, "morning", "2026-10-13")
    _record(scheduler, "evening", "2026-10-13")

    scheduler.check_routine_outcomes(now=_et(2026, 10, 14, 10, 24, 59))
    assert scheduler.notice_store.all_notices() == []
    scheduler.check_routine_outcomes(now=_et(2026, 10, 14, 10, 25))

    assert [n.key for n in scheduler.notice_store.all_notices()] == [
        "routine_missed:morning:2026-10-14"
    ]


def test_a_missed_evening_is_reported_only_after_the_deadline_and_grace(make_scheduler):
    scheduler = make_scheduler()
    for day in ("2026-10-13", "2026-10-14"):
        _record(scheduler, "morning", day)
    _record(scheduler, "evening", "2026-10-13")

    scheduler.check_routine_outcomes(now=_et(2026, 10, 14, 16, 3, 59))
    assert scheduler.notice_store.all_notices() == []
    scheduler.check_routine_outcomes(now=_et(2026, 10, 14, 16, 4))

    assert [n.key for n in scheduler.notice_store.all_notices()] == [
        "routine_missed:evening:2026-10-14"
    ]


@pytest.mark.parametrize(
    "switched_on, missed",
    [
        (_et(2026, 10, 14, 9, 22), True),  # within the window: the catch-up applies
        (_et(2026, 10, 14, 10, 0), False),  # after 09:25: it never started late, so no miss
    ],
)
def test_a_miss_is_excused_by_a_switch_on_after_the_window(make_scheduler, switched_on, missed):
    scheduler = make_scheduler()
    scheduler.save_flags(enabled=True)
    _record(scheduler, "morning", "2026-10-13")
    _record(scheduler, "evening", "2026-10-13")
    stamp = switched_on.astimezone().replace(tzinfo=None).isoformat()
    with sqlite3.connect(scheduler.state_manager._db_path) as conn:
        conn.execute("UPDATE scheduler_state SET morning_enabled_at = ?", (stamp,))

    scheduler.check_routine_outcomes(now=_et(2026, 10, 14, 10, 30))

    keys = [n.key for n in scheduler.notice_store.all_notices()]
    assert keys == (["routine_missed:morning:2026-10-14"] if missed else [])


# --- Calendar and GTT -------------------------------------------------------------------


class _EmptyCalendar:
    def get_calendar(self, filters):
        return []


@pytest.fixture
def empty_calendar(monkeypatch):
    monkeypatch.setattr(mh, "_CALENDAR_CLIENT", _EmptyCalendar())
    monkeypatch.setattr(mh, "_CALENDAR_CACHE", {})


def test_an_empty_calendar_answer_on_a_rules_trading_day_follows_the_rules(empty_calendar, caplog):
    with caplog.at_level(logging.WARNING, logger=mh.logger.name):
        hours = mh.get_market_hours_for_date(date(2026, 10, 14))

    assert hours == (mh.DEFAULT_MARKET_OPEN, mh.DEFAULT_MARKET_CLOSE, True)  # the rules win
    assert mh._CALENDAR_CACHE[date(2026, 10, 14)][2] is True  # the rules' answer, this process
    assert mh.get_market_hours_for_date(date(2026, 10, 14))[2] is True
    assert caplog.text.count("2026-10-14") == 1  # warned once per process
    assert "returned nothing for a trading day" in mh.calendar_disagreement(date(2026, 10, 14))


@pytest.mark.parametrize("day", [date(2026, 10, 17), date(2026, 11, 26)])  # Saturday, Thanksgiving
def test_an_empty_calendar_answer_on_a_closed_day_is_quiet(empty_calendar, caplog, day):
    with caplog.at_level(logging.WARNING, logger=mh.logger.name):
        assert mh.get_market_hours_for_date(day)[2] is False

    assert caplog.text == ""


def _stop_state():
    return SimpleNamespace(
        highest_price_seen=100.0,
        current_stop=95.0,
        entry_price=90.0,
        quantity=10,
        stop_order_id="abc",
        in_profit_zone=True,
        adjustments_count=1,
        voter_signal=None,
        support_level=None,
    )


@pytest.fixture
def gtt(tmp_path, monkeypatch):
    manager = GTTManager(db_path=str(tmp_path / "gtt.db"))
    monkeypatch.setattr(trailing_stop_bridge, "get_gtt_manager", lambda: manager)
    return manager


def _days_left(trigger):
    return (datetime.fromisoformat(trigger.expiration_date) - get_datetime_now()) / timedelta(
        days=1
    )


def test_creating_a_trailing_stop_trigger_sets_its_expiration(gtt):
    trigger = trailing_stop_bridge.sync_trailing_stop_to_gtt("SPY", _stop_state())

    assert _days_left(trigger) == pytest.approx(90, abs=0.01)


def test_syncing_an_existing_trigger_moves_its_expiration_forward(gtt):
    trigger = trailing_stop_bridge.sync_trailing_stop_to_gtt("SPY", _stop_state())
    soon = (get_datetime_now() + timedelta(days=2)).isoformat()
    gtt.update_trigger(trigger.id, expiration_date=soon)
    assert not gtt.get_trigger(trigger.id).is_expired

    synced = trailing_stop_bridge.sync_trailing_stop_to_gtt("SPY", _stop_state())

    assert synced.id == trigger.id
    assert _days_left(synced) == pytest.approx(90, abs=0.01)


def test_a_sync_with_another_expiration_uses_it(gtt):
    trigger = trailing_stop_bridge.sync_trailing_stop_to_gtt("SPY", _stop_state())

    synced = trailing_stop_bridge.sync_trailing_stop_to_gtt(
        "SPY", _stop_state(), expiration_days=30
    )

    assert (synced.id, round(_days_left(synced))) == (trigger.id, 30)


# --- Review fixes -----------------------------------------------------------------------


def test_switching_a_routine_on_late_does_not_start_a_late_run(make_scheduler):
    scheduler = make_scheduler()
    scheduler.save_flags(enabled=True)
    stamp = _et(2026, 10, 14, 10, 0).astimezone().replace(tzinfo=None).isoformat()
    with sqlite3.connect(scheduler.state_manager._db_path) as conn:
        conn.execute("UPDATE scheduler_state SET morning_enabled_at = ?", (stamp,))
    task = _task(scheduler, "morning_routine")

    assert _can_run(scheduler, "morning_routine", _et(2026, 10, 14, 10, 1)) is False
    assert scheduler.run_deadline(task, date(2026, 10, 14)) == _et(2026, 10, 14, 9, 25)


def test_a_timed_out_run_does_not_settle_the_day_but_two_do(make_scheduler):
    scheduler = make_scheduler()
    now = _et(2026, 10, 14, 9, 40)

    _record(scheduler, "morning", "2026-10-14", status="timed_out")
    assert _can_run(scheduler, "morning_routine", now) is True  # one more try
    _record(scheduler, "morning", "2026-10-14", status="timed_out")
    assert _can_run(scheduler, "morning_routine", now) is False
    assert scheduler.next_run(now=now)[0].name == "evening_routine"


def test_a_timed_out_run_is_noticed_as_failed(make_scheduler):
    scheduler = make_scheduler()
    scheduler.save_flags(enabled=True)
    _record(scheduler, "morning", "2026-10-13")
    _record(scheduler, "evening", "2026-10-13")
    _record(scheduler, "morning", "2026-10-14", status="timed_out")

    scheduler.check_routine_outcomes(now=_et(2026, 10, 14, 9, 40))

    assert [n.key for n in scheduler.notice_store.all_notices()] == [
        "routine_failed:morning:2026-10-14"
    ]


def test_a_timeout_after_the_routine_finished_keeps_its_result(run_due):
    scheduler = run_due(limit=1)
    manager = scheduler.state_manager
    real = manager.record_execution_complete

    def complete_then_alarm(*args, **kwargs):
        real(*args, **kwargs)
        manager.record_execution_complete = real
        raise RunDueTimeoutError(1)  # lands in the code after the completed record

    manager.record_execution_complete = complete_then_alarm

    main._run_due_mode()

    day = scheduler._market_now().date().isoformat()
    [run] = manager.executions_on([day])[("morning", day)]
    assert run.status == "completed"
    assert scheduler.notice_store.all_notices() == []


def test_a_late_completion_cannot_be_overwritten(make_scheduler):
    manager = make_scheduler().state_manager
    run_id = manager.record_execution_start("morning", market_date="2026-10-14")
    manager.record_execution_complete(run_id, status="completed")

    manager.record_execution_complete(run_id, status="timed_out", only_if_running=True)

    assert manager.executions_on(["2026-10-14"])[("morning", "2026-10-14")][0].status == "completed"


def test_next_run_is_never_in_the_past(make_scheduler):
    scheduler = make_scheduler()
    now = _et(2026, 10, 14, 10, 5)

    task, when = scheduler.next_run(now=now)

    assert (task.name, when) == ("morning_routine", now)


def test_a_calendar_disagreement_is_one_warning_notice_over_ticks(
    make_scheduler, empty_calendar, monkeypatch
):
    scheduler = make_scheduler()
    # the real lookup, answered by the empty fake calendar
    monkeypatch.setattr(daily_scheduler, "get_market_hours_for_date", mh.get_market_hours_for_date)
    task = next(t for t in scheduler.tasks if t.name == "morning_routine")
    for _tick in range(2):  # two cron ticks
        scheduler._hours_memo.clear()
        assert scheduler.scheduled_at(task, date(2026, 10, 14)) is not None  # rules: trading day

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == "calendar_disagrees:2026-10-14"
    assert (notice.severity, notice.count) == ("WARNING", 1)
    assert "market_hours.yaml" in notice.detail


def test_agreeing_calendar_answers_raise_no_notice(make_scheduler):
    scheduler = make_scheduler()  # the autouse fixture answers from the rules
    task = next(t for t in scheduler.tasks if t.name == "morning_routine")

    scheduler.scheduled_at(task, date(2026, 10, 14))

    assert scheduler.notice_store.all_notices() == []


# --- Unreadable scheduler flags (E1-05) ---------------------------------------------------


def test_a_cron_tick_on_unreadable_state_runs_nothing_and_leaves_a_critical_notice(
    make_scheduler, monkeypatch
):
    scheduler = make_scheduler(enabled=False)  # the shipped config; the saved flag switches on
    scheduler.save_flags(enabled=True)
    Path(scheduler.state_manager._db_path).write_bytes(b"not a database\n" * 8)
    monkeypatch.setattr(
        scheduler, "should_run_task", lambda task, now=None: True, raising=False
    )  # as if both routines were due this tick
    monkeypatch.setattr(main, "DailyScheduler", lambda: scheduler)
    monkeypatch.setattr(main, "configure_scheduler_logging", lambda: None)
    monkeypatch.setattr(main, "load_scheduler_config", lambda: {"run_due_timeout_seconds": 30})
    monkeypatch.setattr(main, "ping_heartbeat", lambda: False)  # stays offline

    code = main._run_due_mode()

    # main.py decides the exit code (and so the heartbeat) from the routines that ran, and
    # none did: the CRITICAL notice, emailed by the digest where set up, is the alarm
    assert code == 0
    assert scheduler.trading_cycle.morning_routine.call_count == 0
    assert scheduler.trading_cycle.evening_routine.call_count == 0
    [notice] = scheduler.notice_store.all_notices()
    today = scheduler._market_now().date().isoformat()
    assert (notice.key, notice.severity, notice.count) == (f"state_db_error:{today}", "CRITICAL", 1)
    assert _run_due_notices() == []  # the tick itself did not crash
