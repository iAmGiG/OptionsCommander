"""
Scheduler flags that can't be read (E1-05): an unreadable or missing scheduler_state row
must not switch the scheduler off silently. The state manager tells "no row" from "can't
read" (read_state), a save never writes defaults over a row it couldn't read, and the cron
tick raises a CRITICAL notice while still running nothing. Nothing leaves the machine.
"""

import fcntl
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import src.utils.market_hours as mh
from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling.daily_scheduler import DailyScheduler, save_flags
from src.trading.scheduling.notices import NoticeStore
from src.trading.scheduling.scheduler_state import SchedulerState, SchedulerStateManager
from src.utils.state_paths import STATE_DIR_ENV, get_state_dir


def _corrupt(manager):
    """Overwrite the manager's database file with bytes that are not a database."""
    Path(manager._db_path).write_bytes(b"this is not a database\n" * 8)


class _SelectsFail:
    """
    A connection whose SELECTs raise while its writes go through: the shape of a read
    error that a blind save would follow with a write of defaults.
    """

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name):
        return getattr(self._target, name)

    def __setattr__(self, name, value):
        setattr(self._target, name, value)

    def execute(self, sql, *args):
        if sql.lstrip().upper().startswith("SELECT"):
            raise sqlite3.OperationalError("disk I/O error")
        return _SelectsFail(self._target.execute(sql, *args))

    def cursor(self):
        return _SelectsFail(self._target.cursor())


@pytest.fixture
def manager(tmp_path):
    return SchedulerStateManager(str(tmp_path / "user.db"))


def _reads_fail(patch, manager):
    """
    Make the manager's SELECTs fail for as long as `patch` (a monkeypatch.context()) lasts.
    The fault is scoped to the context on purpose: monkeypatch.undo() would also undo the
    autouse fixtures, the temporary state dir first of all (2026-10-13 incident class).
    """
    real = manager._get_connection
    patch.setattr(manager, "_get_connection", lambda: _SelectsFail(real()))


def _state_dir_is_still_temporary(tmp_path):
    return os.environ.get(STATE_DIR_ENV, "").startswith(str(tmp_path))


# --- The state manager ------------------------------------------------------------------


def test_read_state_tells_no_row_from_a_saved_row(manager):
    assert manager.read_state() is None
    assert manager.has_saved_state() is False

    assert manager.save_state(SchedulerState(enabled=True, evening_enabled=True))

    state = manager.read_state()
    assert (state.enabled, state.morning_enabled, state.evening_enabled) == (True, True, True)
    assert manager.has_saved_state() is True


def test_read_state_raises_on_a_corrupt_database_where_get_state_falls_back(manager, caplog):
    manager.save_state(SchedulerState(enabled=True))
    _corrupt(manager)

    with pytest.raises(sqlite3.DatabaseError):
        manager.read_state()

    # The lenient readers keep their contract: defaults, an error line, no exception
    assert manager.get_state() == SchedulerState()
    assert manager.has_saved_state() is False
    assert "Failed to read scheduler state" in caplog.text


def test_read_state_raises_when_the_connection_fails(manager, monkeypatch):
    _reads_fail(monkeypatch, manager)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        manager.read_state()


def test_a_save_over_an_unreadable_row_writes_nothing(manager, monkeypatch, caplog, tmp_path):
    assert manager.save_state(SchedulerState(enabled=True, evening_enabled=True))

    with monkeypatch.context() as broken:
        _reads_fail(broken, manager)
        assert manager.save_state(SchedulerState(enabled=True, evening_enabled=True)) is False
        assert manager.set_enabled(True) is False  # reads defaults, must not write them
        assert "Failed to save scheduler state" in caplog.text

    assert _state_dir_is_still_temporary(tmp_path)  # the fixtures' patches survived
    state = manager.read_state()
    assert (state.enabled, state.evening_enabled) == (True, True)  # the row is as it was


def test_a_setter_whose_read_fails_once_writes_nothing(manager, caplog):
    """
    A transient read error (a lock) at the setter's read, gone by the save's own read: the
    setter must not write the defaults plus its one change over the row it couldn't see.
    """
    assert manager.save_state(
        SchedulerState(enabled=True, evening_enabled=True, morning_time="09:25:00")
    )
    real = manager._get_connection
    failures = iter([True])  # the first SELECT fails, every later one works

    class _FirstSelectFails(_SelectsFail):
        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith("SELECT") and next(failures, False):
                raise sqlite3.OperationalError("database is locked")
            return self._target.execute(sql, *args)

    manager._get_connection = lambda: _FirstSelectFails(real())
    with caplog.at_level(logging.ERROR):
        assert manager.set_enabled(False) is False
    assert "Scheduler state not changed" in caplog.text

    state = manager.read_state()
    assert (state.enabled, state.evening_enabled, state.morning_time) == (True, True, "09:25:00")
    for setter, value in (
        (manager.set_morning_enabled, False),
        (manager.set_evening_enabled, False),
        (manager.update_times, "09:26:00"),
    ):
        failures = iter([True])
        assert setter(value) is False
    state = manager.read_state()
    assert (state.enabled, state.evening_enabled, state.morning_time) == (True, True, "09:25:00")


def test_has_run_history_counts_real_runs_only(manager):
    assert manager.has_run_history() is False

    run = manager.record_execution_start("morning", market_date="2026-10-15")
    manager.record_execution_complete(run, status="dry_run")
    assert manager.has_run_history() is False  # a dry run isn't the day's routine (#601)

    manager.record_execution_start("evening", market_date="2026-10-15")  # still running
    assert manager.has_run_history() is True


def test_has_run_history_raises_on_a_corrupt_database(manager):
    _corrupt(manager)

    with pytest.raises(sqlite3.DatabaseError):
        manager.has_run_history()


# --- The cron tick ----------------------------------------------------------------------


ET = ZoneInfo("America/New_York")
# A clock inside each routine's run window on 2026-10-15, a Thursday with a full session
IN_WINDOW = {
    "morning_routine": datetime(2026, 10, 15, 9, 20, tzinfo=ET),
    "evening_routine": datetime(2026, 10, 15, 15, 50, tzinfo=ET),
}


@pytest.fixture
def make_scheduler(tmp_path, monkeypatch):
    """
    A scheduler with a mock trading cycle, its own state and notices, the NYSE rules as the
    calendar, and a config file saying enabled: false, as the shipped one does (the saved
    flags switch it on).
    """
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(daily_scheduler, "get_market_hours_for_date", mh._rule_based_hours)

    def make(due=(), **config):
        settings = {"enabled": False, **config}
        config_file = tmp_path / "scheduler_config.yaml"
        config_file.write_text("\n".join(f"{k}: {v!r}" for k, v in settings.items()))
        scheduler = DailyScheduler(
            config_file=str(config_file),
            trading_cycle=Mock(last_report_path=None, last_alerts=(), last_discrepancies=()),
            state_manager=SchedulerStateManager(),
            notice_store=NoticeStore(tmp_path / "notices.json", raise_errors=True),
        )
        # The tasks "due" on this tick see a clock inside their window; the rest of
        # should_run_task, the flags check first of all, is the real thing
        real_should_run = scheduler.should_run_task
        monkeypatch.setattr(
            scheduler,
            "should_run_task",
            lambda task, now=None: (
                task.name in due and real_should_run(task, now=IN_WINDOW[task.name])
            ),
            raising=False,
        )
        return scheduler

    return make


def _notices(scheduler):
    return [(n.key, n.severity, n.count) for n in scheduler.notice_store.all_notices()]


def _today(scheduler):
    return scheduler._market_now().date().isoformat()


def _routines_run(scheduler):
    cycle = scheduler.trading_cycle
    return cycle.morning_routine.call_count + cycle.evening_routine.call_count


def test_an_unreadable_state_runs_nothing_and_is_one_critical_notice_a_day(make_scheduler):
    scheduler = make_scheduler(due=("morning_routine", "evening_routine"), enabled=True)
    scheduler.save_flags(enabled=True)
    _corrupt(scheduler.state_manager)

    ran = scheduler.run_scheduled_tasks()
    scheduler.run_scheduled_tasks()  # the next tick

    assert ran == [] and _routines_run(scheduler) == 0  # not even from the config's flags
    assert _notices(scheduler) == [(f"state_db_error:{_today(scheduler)}", "CRITICAL", 1)]
    [notice] = scheduler.notice_store.all_notices()
    assert "not a database" in notice.detail


def test_the_notice_resolves_once_the_state_is_readable_again(make_scheduler):
    scheduler = make_scheduler()
    _corrupt(scheduler.state_manager)
    scheduler.run_scheduled_tasks()
    assert [n.resolved_at for n in scheduler.notice_store.all_notices()] == [None]

    Path(scheduler.state_manager._db_path).unlink()
    scheduler.state_manager = SchedulerStateManager()  # a fresh, readable database
    scheduler.run_scheduled_tasks()

    [notice] = scheduler.notice_store.all_notices()
    assert notice.key == f"state_db_error:{_today(scheduler)}"
    assert notice.resolved_at is not None


FLAGS_KINDS = {"state_db_error", daily_scheduler.SCHEDULER_FLAGS_MISSING}


def _flags_notices(scheduler):
    """The flags notices only: the outcome check runs on the real clock, and whether it finds
    yesterday's routines missed is that check's business, not this file's."""
    return [n.kind for n in scheduler.notice_store.all_notices() if n.kind in FLAGS_KINDS]


def test_a_readable_state_is_no_notice_and_the_routines_run_as_before(make_scheduler):
    scheduler = make_scheduler(due=("morning_routine",))
    scheduler.save_flags(enabled=True)

    ran = scheduler.run_scheduled_tasks()

    assert [entry.status for entry in ran] == ["completed"]
    assert _routines_run(scheduler) == 1
    assert _flags_notices(scheduler) == []


def test_a_fresh_state_with_the_shipped_config_stays_quiet(make_scheduler):
    scheduler = make_scheduler(due=("morning_routine",))  # no row, no history, enabled: false

    assert scheduler.run_scheduled_tasks() == []
    assert _routines_run(scheduler) == 0
    assert _notices(scheduler) == []


def test_a_missing_row_where_the_scheduler_has_run_is_a_notice_until_flags_are_saved(
    make_scheduler,
):
    scheduler = make_scheduler(due=("morning_routine",))
    manager = scheduler.state_manager
    manager.record_execution_complete(
        manager.record_execution_start("morning", market_date="2026-10-14"), status="completed"
    )
    today = _today(scheduler)

    assert scheduler.run_scheduled_tasks() == []  # off, as today: the config says so
    scheduler.run_scheduled_tasks()
    assert _routines_run(scheduler) == 0
    assert _notices(scheduler) == [(f"scheduler_flags_missing:{today}", "CRITICAL", 1)]
    [notice] = scheduler.notice_store.all_notices()
    assert "no saved scheduler flags" in notice.detail and "enable" in notice.detail

    scheduler.save_flags(enabled=False)  # the CLI's `disable`: off on purpose, recorded
    scheduler.run_scheduled_tasks()

    [notice] = scheduler.notice_store.all_notices()
    assert (notice.key, notice.resolved_at is not None) == (
        f"scheduler_flags_missing:{today}",
        True,
    )


def test_a_missing_row_is_no_notice_when_the_config_runs_the_scheduler(make_scheduler):
    scheduler = make_scheduler(due=("morning_routine",), enabled=True)
    manager = scheduler.state_manager
    manager.record_execution_complete(
        manager.record_execution_start("morning", market_date="2026-10-14"), status="completed"
    )

    ran = scheduler.run_scheduled_tasks()

    assert [entry.status for entry in ran] == ["completed"]
    assert _flags_notices(scheduler) == []


def test_a_flags_missing_notice_from_an_earlier_day_resolves_once_flags_are_saved(
    make_scheduler,
):
    """The row went missing on day D and the owner saved flags on D+1: D's notice closes too."""
    scheduler = make_scheduler()
    manager = scheduler.state_manager
    manager.record_execution_complete(
        manager.record_execution_start("morning", market_date="2026-10-14"), status="completed"
    )
    scheduler.check_routine_outcomes(now=datetime(2026, 10, 15, 9, 31, tzinfo=ET))
    assert _notices(scheduler) == [("scheduler_flags_missing:2026-10-15", "CRITICAL", 1)]

    scheduler.save_flags(enabled=True)
    scheduler.check_routine_outcomes(now=datetime(2026, 10, 16, 9, 31, tzinfo=ET))

    by_key = {n.key: n for n in scheduler.notice_store.all_notices()}
    assert by_key["scheduler_flags_missing:2026-10-15"].resolved_at is not None


def test_a_state_db_error_from_an_earlier_day_resolves_on_the_next_readable_tick(make_scheduler):
    scheduler = make_scheduler()
    _corrupt(scheduler.state_manager)
    scheduler.check_routine_outcomes(now=datetime(2026, 10, 15, 9, 31, tzinfo=ET))
    assert _notices(scheduler) == [("state_db_error:2026-10-15", "CRITICAL", 1)]

    Path(scheduler.state_manager._db_path).unlink()
    scheduler.state_manager = SchedulerStateManager()  # fixed the next morning
    scheduler.check_routine_outcomes(now=datetime(2026, 10, 16, 9, 31, tzinfo=ET))

    by_key = {n.key: n for n in scheduler.notice_store.all_notices()}
    assert by_key["state_db_error:2026-10-15"].resolved_at is not None


def test_the_enabled_at_read_in_the_outcome_check_is_strict_too(make_scheduler):
    """
    _routines_enabled_at reads the row again after _tick_settings; a database that breaks
    between the two reads is the day's state_db_error notice, not an empty enabled_at.
    """
    scheduler = make_scheduler()
    scheduler.save_flags(enabled=True)
    manager = scheduler.state_manager
    real_read = manager.read_state
    reads = []

    def flaky_read():
        reads.append(1)
        if len(reads) > 1:
            raise sqlite3.OperationalError("database is locked")
        return real_read()

    manager.read_state = flaky_read
    scheduler.check_routine_outcomes(now=datetime(2026, 10, 15, 9, 31, tzinfo=ET))

    assert len(reads) == 2
    assert _notices(scheduler) == [("state_db_error:2026-10-15", "CRITICAL", 1)]


def test_a_dry_run_history_alone_is_no_missing_row(make_scheduler):
    scheduler = make_scheduler()
    manager = scheduler.state_manager
    manager.record_execution_complete(
        manager.record_execution_start("morning", market_date="2026-10-14"), status="dry_run"
    )

    scheduler.run_scheduled_tasks()

    assert _notices(scheduler) == []


def test_a_tick_without_the_lock_still_notices_unreadable_state(make_scheduler):
    scheduler = make_scheduler()
    _corrupt(scheduler.state_manager)

    with open(get_state_dir() / "scheduler.lock", "a") as other_process:
        fcntl.flock(other_process, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scheduler.run_scheduled_tasks()
        fcntl.flock(other_process, fcntl.LOCK_UN)

    assert _notices(scheduler) == [(f"state_db_error:{_today(scheduler)}", "CRITICAL", 1)]


def test_saving_flags_over_unreadable_state_fails_and_keeps_the_row(
    make_scheduler, monkeypatch, caplog, tmp_path
):
    scheduler = make_scheduler()
    assert scheduler.save_flags(enabled=True)

    with monkeypatch.context() as broken:
        _reads_fail(broken, scheduler.state_manager)
        with caplog.at_level(logging.ERROR):
            assert scheduler.save_flags(evening=True) is False
    assert "Scheduler flags not saved" in caplog.text  # save_flags itself refused

    assert _state_dir_is_still_temporary(tmp_path)
    assert scheduler.effective_settings()["enabled"] is True  # not the config's False


def test_save_flags_refuses_an_unreadable_state_before_asking_the_manager_to_save(caplog):
    """The save_flags guard on its own: save_state's guard behind it is not what is tested."""
    manager = Mock(spec=SchedulerStateManager)
    manager.read_state.side_effect = sqlite3.OperationalError("database is locked")

    with caplog.at_level(logging.ERROR):
        assert save_flags({"enabled": False}, manager, enabled=True) is False

    manager.save_state.assert_not_called()
    assert "Scheduler flags not saved" in caplog.text


def test_the_cli_facing_readers_still_fall_back_without_raising(make_scheduler):
    scheduler = make_scheduler()
    scheduler.save_flags(enabled=True)
    _corrupt(scheduler.state_manager)

    assert scheduler.effective_settings() == {
        "enabled": False,
        "morning": True,
        "evening": True,
        "source": "config",
    }
    assert scheduler.next_run() is None
