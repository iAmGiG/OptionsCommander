#!/usr/bin/env python3
"""
Scheduler State Persistence - SQLite-backed state management.

Issue #478: Migrate scheduler runtime state to SQLite for persistence across restarts.

This module provides:
- Scheduler enable/disable state persistence
- Execution history tracking (replaces JSON log file)
- Last run timestamps for each routine type

Uses the same state/user.db as trading_modes.py for consistency.
"""

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.utils.date_utils import get_datetime_now, now_iso
from src.utils.market_hours import market_today  # noqa: F401  (re-exported: the ET date, #620)
from src.utils.state_paths import get_user_db_path

logger = logging.getLogger(__name__)

MARKET_TIMEZONE = "America/New_York"


@dataclass
class SchedulerState:
    """Current scheduler state."""

    enabled: bool = False
    morning_enabled: bool = True
    evening_enabled: bool = False
    morning_time: str = "09:20:00"
    evening_time: str = "15:50:00"
    max_retries: int = 3
    retry_delay_seconds: int = 60
    updated_at: Optional[str] = None
    # When each routine last went from off to on (the scheduler and the routine enabled);
    # save_state() sets them from the saved row, whatever the state passed in holds
    morning_enabled_at: Optional[str] = None
    evening_enabled_at: Optional[str] = None


# Columns added to scheduler_state after it was first made (#342)
_ENABLED_AT_COLUMNS = ("morning_enabled_at", "evening_enabled_at")


def _turned_on_at(on: bool, was_on: bool, since: Optional[str], now: str) -> Optional[str]:
    """A routine's enabled_at stamp after a save: now if the save turned it on."""
    return now if on and not was_on else since


@dataclass
class SchedulerExecution:
    """Record of a scheduler execution."""

    id: Optional[int] = None
    routine_type: str = ""  # 'morning', 'evening', 'recovery'
    started_at: str = ""
    completed_at: Optional[str] = None
    status: str = "pending"  # pending, running, completed, failed, timed_out, retrying
    attempt: int = 1
    error_message: Optional[str] = None
    report_path: Optional[str] = None
    api_calls_used: int = 0


def _execution_from_row(row: sqlite3.Row) -> SchedulerExecution:
    """A scheduler_history row as a SchedulerExecution."""
    return SchedulerExecution(
        id=row["id"],
        routine_type=row["routine_type"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        status=row["status"],
        attempt=row["attempt"],
        error_message=row["error_message"],
        report_path=row["report_path"],
        api_calls_used=row["api_calls_used"],
    )


class SchedulerStateManager:
    """
    Manages scheduler state persistence in SQLite.

    Uses state/user.db (same as TradingModeManager) for consistency.
    Provides methods for:
    - Getting/setting scheduler enabled state
    - Recording execution history
    - Querying last run times
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize scheduler state manager.

        Args:
            db_path: Path to SQLite database (default: state/user.db)
        """
        if db_path is None:
            db_path = get_user_db_path()

        self._db_path = db_path
        self._init_database()
        logger.debug(f"SchedulerStateManager initialized with db: {db_path}")

    def _init_database(self) -> None:
        """Initialize SQLite tables for scheduler state."""
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()

            # Scheduler state table (single row for current state)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled BOOLEAN DEFAULT FALSE,
                    morning_enabled BOOLEAN DEFAULT TRUE,
                    evening_enabled BOOLEAN DEFAULT FALSE,
                    morning_time TEXT DEFAULT '09:20:00',
                    evening_time TEXT DEFAULT '15:50:00',
                    max_retries INTEGER DEFAULT 3,
                    retry_delay_seconds INTEGER DEFAULT 60,
                    -- databases made before #601 also have an unused timeout_seconds column
                    updated_at TEXT,
                    morning_enabled_at TEXT,
                    evening_enabled_at TEXT
                )
            """)
            columns = {row[1] for row in cursor.execute("PRAGMA table_info(scheduler_state)")}
            for column in _ENABLED_AT_COLUMNS:
                if column not in columns:
                    cursor.execute(f"ALTER TABLE scheduler_state ADD COLUMN {column} TEXT")

            # Scheduler execution history (replaces JSON log)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    routine_type TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT DEFAULT 'pending',
                    attempt INTEGER DEFAULT 1,
                    error_message TEXT,
                    report_path TEXT,
                    api_calls_used INTEGER DEFAULT 0
                )
            """)

            # The trading day a run belongs to (ET); started_at is host-local time
            columns = {row[1] for row in cursor.execute("PRAGMA table_info(scheduler_history)")}
            if "market_date" not in columns:
                cursor.execute("ALTER TABLE scheduler_history ADD COLUMN market_date TEXT")

            # Create index for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_scheduler_history_routine_date
                ON scheduler_history(routine_type, started_at)
            """)

            conn.commit()
            conn.close()
            logger.debug("Scheduler state tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize scheduler database: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # =========================================================================
    # State Management
    # =========================================================================

    def get_state(self) -> SchedulerState:
        """
        Get current scheduler state.

        Returns:
            SchedulerState with current configuration
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduler_state WHERE id = 1")
            row = cursor.fetchone()
            conn.close()

            if row:
                return SchedulerState(
                    enabled=bool(row["enabled"]),
                    morning_enabled=bool(row["morning_enabled"]),
                    evening_enabled=bool(row["evening_enabled"]),
                    morning_time=row["morning_time"],
                    evening_time=row["evening_time"],
                    max_retries=row["max_retries"],
                    retry_delay_seconds=row["retry_delay_seconds"],
                    updated_at=row["updated_at"],
                    **{
                        column: row[column]
                        for column in _ENABLED_AT_COLUMNS
                        if column in row.keys()
                    },
                )
            else:
                # Return defaults if no state exists
                return SchedulerState()
        except Exception as e:
            logger.error(f"Failed to get scheduler state: {e}")
            return SchedulerState()

    def save_state(self, state: SchedulerState) -> bool:
        """
        Save scheduler state. A routine that this save turns on (the scheduler and the
        routine both enabled, where before they weren't) gets enabled_at = now; any other
        save keeps its stamp, so changing a time doesn't look like switching it on.

        Args:
            state: SchedulerState to persist

        Returns:
            True if successful
        """
        try:
            before = self.get_state() if self.has_saved_state() else None
            now = now_iso()
            stamps = [
                _turned_on_at(
                    state.enabled and getattr(state, f"{routine}_enabled"),
                    bool(before and before.enabled and getattr(before, f"{routine}_enabled")),
                    getattr(before, f"{routine}_enabled_at", None),
                    now,
                )
                for routine in ("morning", "evening")
            ]
            conn = self._get_connection()
            cursor = conn.cursor()

            # Upsert (insert or replace)
            cursor.execute(
                """
                INSERT OR REPLACE INTO scheduler_state
                (id, enabled, morning_enabled, evening_enabled, morning_time,
                 evening_time, max_retries, retry_delay_seconds, updated_at,
                 morning_enabled_at, evening_enabled_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    state.enabled,
                    state.morning_enabled,
                    state.evening_enabled,
                    state.morning_time,
                    state.evening_time,
                    state.max_retries,
                    state.retry_delay_seconds,
                    now,
                    *stamps,
                ),
            )

            conn.commit()
            conn.close()
            logger.info(f"Scheduler state saved: enabled={state.enabled}")
            return True
        except Exception as e:
            logger.error(f"Failed to save scheduler state: {e}")
            return False

    def has_saved_state(self) -> bool:
        """True once a state row has been saved (e.g. by the CLI); defaults apply until then."""
        try:
            conn = self._get_connection()
            row = conn.execute("SELECT 1 FROM scheduler_state WHERE id = 1").fetchone()
            conn.close()
            return row is not None
        except Exception as e:
            logger.error(f"Failed to read scheduler state: {e}")
            return False

    def set_enabled(self, enabled: bool) -> bool:
        """
        Enable or disable the scheduler.

        Args:
            enabled: True to enable, False to disable

        Returns:
            True if successful
        """
        state = self.get_state()
        state.enabled = enabled
        return self.save_state(state)

    def is_enabled(self) -> bool:
        """Check if scheduler is enabled."""
        return self.get_state().enabled

    def set_morning_enabled(self, enabled: bool) -> bool:
        """
        Enable or disable the morning routine.

        Args:
            enabled: True to enable, False to disable

        Returns:
            True if successful
        """
        state = self.get_state()
        state.morning_enabled = enabled
        return self.save_state(state)

    def set_evening_enabled(self, enabled: bool) -> bool:
        """
        Enable or disable the evening routine.

        Args:
            enabled: True to enable, False to disable

        Returns:
            True if successful
        """
        state = self.get_state()
        state.evening_enabled = enabled
        return self.save_state(state)

    def update_times(
        self, morning_time: Optional[str] = None, evening_time: Optional[str] = None
    ) -> bool:
        """
        Update routine times.

        Args:
            morning_time: New morning time (HH:MM:SS format)
            evening_time: New evening time (HH:MM:SS format)

        Returns:
            True if successful
        """
        state = self.get_state()
        if morning_time:
            state.morning_time = morning_time
        if evening_time:
            state.evening_time = evening_time
        return self.save_state(state)

    # =========================================================================
    # Execution History
    # =========================================================================

    def record_execution_start(
        self, routine_type: str, attempt: int = 1, market_date: Optional[str] = None
    ) -> int:
        """
        Record the start of a scheduler execution.

        Args:
            routine_type: Type of routine (morning, evening, recovery)
            attempt: Attempt number (1 = first attempt)
            market_date: Trading day the run is for (YYYY-MM-DD, ET; default today in ET)

        Returns:
            Execution ID for later updates
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scheduler_history
                (routine_type, started_at, status, attempt, market_date)
                VALUES (?, ?, 'running', ?, ?)
            """,
                (routine_type, now_iso(), attempt, market_date or market_today()),
            )
            execution_id = cursor.lastrowid
            conn.commit()
            conn.close()
            logger.debug(f"Recorded execution start: {routine_type} (id={execution_id})")
            return execution_id
        except Exception as e:
            logger.error(f"Failed to record execution start: {e}")
            return -1

    def record_execution_complete(
        self,
        execution_id: int,
        status: str,
        report_path: Optional[str] = None,
        api_calls_used: int = 0,
        error_message: Optional[str] = None,
        only_if_running: bool = False,
    ) -> bool:
        """
        Record execution completion or failure.

        Args:
            execution_id: ID from record_execution_start
            status: Final status (completed, failed, timed_out, retrying)
            report_path: Path to generated report
            api_calls_used: Number of API calls made
            error_message: Error message if failed
            only_if_running: Leave a run that already finished as it is (the watchdog's
                timeout can land after a routine recorded its own result)

        Returns:
            True if successful
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE scheduler_history
                SET completed_at = ?, status = ?, report_path = ?,
                    api_calls_used = ?, error_message = ?
                WHERE id = ?
            """
                + (" AND status IN ('running', 'retrying')" if only_if_running else ""),
                (now_iso(), status, report_path, api_calls_used, error_message, execution_id),
            )
            conn.commit()
            conn.close()
            logger.debug(f"Recorded execution complete: id={execution_id}, status={status}")
            return True
        except Exception as e:
            logger.error(f"Failed to record execution complete: {e}")
            return False

    def get_execution_history(
        self,
        days: int = 7,
        routine_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[SchedulerExecution]:
        """
        Get execution history for the last N days.

        Args:
            days: Number of days to retrieve
            routine_type: Optional filter by routine type
            limit: Optional max records to return

        Returns:
            List of SchedulerExecution records
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cutoff = get_datetime_now().replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff = cutoff.isoformat()

            query = """
                SELECT * FROM scheduler_history
                WHERE started_at >= date(?, '-' || ? || ' days')
            """
            params: List[Any] = [cutoff, days]

            if routine_type:
                query += " AND routine_type = ?"
                params.append(routine_type)

            query += " ORDER BY started_at DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            return [_execution_from_row(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get execution history: {e}")
            return []

    def get_last_execution(self, routine_type: str) -> Optional[SchedulerExecution]:
        """
        Get the most recent execution of a routine type.

        Args:
            routine_type: Type of routine

        Returns:
            Most recent SchedulerExecution or None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM scheduler_history
                WHERE routine_type = ?
                ORDER BY started_at DESC
                LIMIT 1
            """,
                (routine_type,),
            )
            row = cursor.fetchone()
            conn.close()

            return _execution_from_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get last execution: {e}")
            return None

    def was_routine_run_today(self, routine_type: str, market_date: Optional[str] = None) -> bool:
        """
        Check if a routine was successfully run today.

        Args:
            routine_type: Type of routine (morning, evening)
            market_date: Trading day to check (YYYY-MM-DD, ET; default today in ET)

        Returns:
            True if routine completed successfully today
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            today = market_date or market_today()

            # Rows from before market_date was recorded fall back to their host-local date
            cursor.execute(
                """
                SELECT COUNT(*) FROM scheduler_history
                WHERE routine_type = ?
                AND coalesce(market_date, date(started_at)) = ?
                AND status = 'completed'
            """,
                (routine_type, today),
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count > 0
        except Exception as e:
            logger.error(f"Failed to check if routine run today: {e}")
            return False

    def was_routine_finished_today(
        self, routine_type: str, market_date: Optional[str] = None
    ) -> bool:
        """
        Check if a routine completed, or failed after its retries, on a trading day.

        The scheduler uses this so a routine that ran out of retries isn't started again
        later in the same window; a manual run can still retry it.

        Args:
            routine_type: Type of routine (morning, evening)
            market_date: Trading day to check (YYYY-MM-DD, ET; default today in ET)
        """
        try:
            conn = self._get_connection()
            row = conn.execute(
                """
                SELECT COUNT(*) FROM scheduler_history
                WHERE routine_type = ?
                AND coalesce(market_date, date(started_at)) = ?
                AND status IN ('completed', 'failed')
            """,
                (routine_type, market_date or market_today()),
            ).fetchone()
            conn.close()
            return row[0] > 0
        except Exception as e:
            logger.error(f"Failed to check if routine finished today: {e}")
            return False

    def count_timeouts(self, routine_type: str, market_date: Optional[str] = None) -> int:
        """How many runs of a routine the cron watchdog cut off on a trading day."""
        try:
            with closing(self._get_connection()) as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM scheduler_history
                    WHERE routine_type = ?
                    AND coalesce(market_date, date(started_at)) = ?
                    AND status = 'timed_out'
                """,
                    (routine_type, market_date or market_today()),
                ).fetchone()
            return row[0]
        except Exception as e:
            logger.error(f"Failed to count routine timeouts: {e}")
            return 0

    def executions_on(
        self, market_dates: Sequence[str]
    ) -> Dict[Tuple[str, str], List[SchedulerExecution]]:
        """
        The runs recorded for trading days, by (routine type, day), oldest first. A run
        belongs to its market date, or to its start date where none was saved (as in
        was_routine_finished_today).

        Unlike the other readers this raises on a database error, so the caller can tell
        an unreadable history from an empty one.

        Args:
            market_dates: Trading days (YYYY-MM-DD, ET)
        """
        runs: Dict[Tuple[str, str], List[SchedulerExecution]] = {}
        with closing(self._get_connection()) as conn:
            for day in market_dates:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduler_history
                    WHERE coalesce(market_date, date(started_at)) = ?
                    ORDER BY started_at, id
                """,
                    (day,),
                ).fetchall()
                for row in rows:
                    runs.setdefault((row["routine_type"], day), []).append(_execution_from_row(row))
        return runs

    def get_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Get execution statistics.

        Args:
            days: Number of days to analyze

        Returns:
            Dict with success rates, counts, etc.
        """
        history = self.get_execution_history(days)

        if not history:
            return {"total": 0, "message": "No execution history"}

        stats: Dict[str, Any] = {
            "total": len(history),
            "completed": sum(1 for e in history if e.status == "completed"),
            "failed": sum(1 for e in history if e.status in ("failed", "timed_out")),
            "by_routine": {},
        }

        stats["success_rate"] = (
            (stats["completed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        )

        # Group by routine type
        for routine in ["morning", "evening", "recovery"]:
            routine_history = [e for e in history if e.routine_type == routine]
            if routine_history:
                completed = sum(1 for e in routine_history if e.status == "completed")
                stats["by_routine"][routine] = {
                    "total": len(routine_history),
                    "completed": completed,
                    "success_rate": completed / len(routine_history) * 100,
                }

        return stats


# Global instance for easy access
_scheduler_state_manager: Optional[SchedulerStateManager] = None


def get_scheduler_state_manager() -> SchedulerStateManager:
    """Get global scheduler state manager instance."""
    global _scheduler_state_manager
    if _scheduler_state_manager is None:
        _scheduler_state_manager = SchedulerStateManager()
    return _scheduler_state_manager
