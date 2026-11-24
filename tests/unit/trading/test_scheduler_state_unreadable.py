"""
Scheduler flags that can't be read (E1-05): an unreadable or missing scheduler_state row
must not switch the scheduler off silently. The state manager tells "no row" from "can't
read" (read_state), a save never writes defaults over a row it couldn't read, and the cron
tick raises a CRITICAL notice while still running nothing. Nothing leaves the machine.
"""

import sqlite3
from pathlib import Path

import pytest

from src.trading.scheduling.scheduler_state import SchedulerState, SchedulerStateManager


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


def _reads_fail(monkeypatch, manager):
    real = manager._get_connection
    monkeypatch.setattr(manager, "_get_connection", lambda: _SelectsFail(real()))


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


def test_a_save_over_an_unreadable_row_writes_nothing(manager, monkeypatch, caplog):
    assert manager.save_state(SchedulerState(enabled=True, evening_enabled=True))
    _reads_fail(monkeypatch, manager)

    assert manager.save_state(SchedulerState(enabled=True, evening_enabled=True)) is False
    assert manager.set_enabled(True) is False  # reads defaults, must not write them
    assert "Failed to save scheduler state" in caplog.text

    monkeypatch.undo()
    state = manager.read_state()
    assert (state.enabled, state.evening_enabled) == (True, True)  # the row is as it was


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
