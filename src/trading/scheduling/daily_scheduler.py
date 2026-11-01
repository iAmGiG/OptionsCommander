#!/usr/bin/env python3
"""
Daily GTC Order Scheduler - Issue #287

Automates daily trading execution with:
- Scheduled morning/evening routines
- Comprehensive retry logic
- Robust error handling
- "Set it and forget it" automation
- Local notices for failed, missed and stuck routines (#342, see notices.py)

Foundation: Built on Issue #313 (Order Management System)
Issue #478: State persistence via SQLite (state/user.db)
"""

import argparse
import asyncio
import datetime as datetime_module
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .notices import (
    CALENDAR_DISAGREES,
    CRITICAL,
    ROUTINE_FAILED,
    ROUTINE_MISSED,
    ROUTINE_STUCK,
    STATE_DB_ERROR,
    STOP_BREACH,
    TARGET_BREACH,
    UNPROTECTED,
    WARNING,
    NoticeStore,
    routine_key,
)
from .scheduler_state import SchedulerExecution, SchedulerStateManager, get_scheduler_state_manager
from .trading_cycle import CostEfficientTradeCycle, RoutineType
from .watchdog import RunDueTimeoutError
from src.trading.positions.position_tracker import AlertType
from src.trading.state.local_state_manager import write_json_atomic
from src.utils.date_utils import add_business_days, get_datetime_now, now_iso, parse_date_string
from src.utils.market_hours import (
    DEFAULT_MARKET_CLOSE,
    EARLY_MARKET_CLOSE,
    calendar_disagreement,
    get_market_hours_for_date,
    is_nyse_trading_day,
    nyse_early_closes,
)
from src.utils.state_paths import get_state_dir

try:
    import yaml
except ImportError:
    yaml = None

try:
    import fcntl
except ImportError:  # Windows: no run lock
    fcntl = None

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

logger = logging.getLogger(__name__)

dt_time = datetime_module.time  # time of day (the time module is imported as time)

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,  # without a readable config file, nothing runs until enabled
    "market_timezone": "America/New_York",
    "morning_routine_time": "09:20:00",
    "evening_routine_time": "15:50:00",
    "morning_routine_enabled": True,
    "evening_routine_enabled": True,
    "max_retries": 3,
    "retry_delay_seconds": 60,
    "enable_notifications": False,
    # Routines send no orders and change no saved state; reports show what they would do (#601)
    "dry_run": False,
}

# A task runs if a check happens within this long after its scheduled time
RUN_WINDOW = timedelta(minutes=5)

# A routine that hasn't run yet may start late (cron skipped ticks, the host was down, a
# routine ran long): the morning up to this long after its time; the evening until a minute
# before the close, so it still starts while the market is open (see run_deadline)
MORNING_CATCH_UP = timedelta(minutes=60)
# Runs the watchdog may cut off per routine and day before it stays off until tomorrow
MAX_TIMEOUTS_PER_DAY = 2
EVENING_CLOSE_MARGIN = timedelta(minutes=1)

# A routine counts as missed this long after its run deadline passed: a slow cron tick or a
# routine still in its retries is not a miss
OUTCOME_GRACE = timedelta(minutes=5)

# A run recorded as started and never finished this long ago is stuck (a routine with its
# retries takes under ten minutes)
STUCK_AFTER = timedelta(minutes=30)


def load_scheduler_config(config_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Scheduler settings: DEFAULT_CONFIG updated from the config file (YAML or JSON).

    Args:
        config_file: Path; default config_defaults/scheduler_config.yaml (or .json)
    """
    if config_file is None:
        yaml_file = "config_defaults/scheduler_config.yaml"
        json_file = "config_defaults/scheduler_config.json"
        config_file = (
            json_file if not os.path.exists(yaml_file) and os.path.exists(json_file) else yaml_file
        )

    config = dict(DEFAULT_CONFIG)
    if not os.path.exists(config_file):
        logger.info("Scheduler config %s not found. Using defaults.", config_file)
        return config
    try:
        with open(config_file, "r") as f:
            if config_file.endswith((".yaml", ".yml")):
                if yaml is None:
                    raise ImportError("PyYAML not installed. Install with: pip install pyyaml")
                user_config = yaml.safe_load(f)
            else:
                user_config = json.load(f)
        config.update(user_config or {})
        logger.info("Loaded scheduler config from %s", config_file)
    except Exception as e:
        logger.warning("Failed to load config from %s: %s. Using defaults.", config_file, e)
    return config


def effective_flags(config: Dict[str, Any], state_manager: SchedulerStateManager) -> Dict[str, Any]:
    """
    Whether the scheduler and each routine are enabled.

    Flags saved in state/user.db (by the CLI, the config editor or the setup wizard)
    win. Until any are saved, the config file's flags apply. Routine times always come
    from the config file.

    Returns:
        {"enabled", "morning", "evening": bool, "source": "saved state" | "config"}
    """
    if state_manager.has_saved_state():
        state = state_manager.get_state()
        return {
            "enabled": state.enabled,
            "morning": state.morning_enabled,
            "evening": state.evening_enabled,
            "source": "saved state",
        }
    return {
        "enabled": bool(config.get("enabled", False)),
        "morning": bool(config.get("morning_routine_enabled", True)),
        "evening": bool(config.get("evening_routine_enabled", True)),
        "source": "config",
    }


def save_flags(
    config: Dict[str, Any],
    state_manager: SchedulerStateManager,
    enabled: Optional[bool] = None,
    morning: Optional[bool] = None,
    evening: Optional[bool] = None,
) -> bool:
    """
    Save enable flags to state/user.db; flags not given keep their effective value.

    Returns:
        True if saved
    """
    current = effective_flags(config, state_manager)
    state = state_manager.get_state()
    state.enabled = current["enabled"] if enabled is None else enabled
    state.morning_enabled = current["morning"] if morning is None else morning
    state.evening_enabled = current["evening"] if evening is None else evening
    return state_manager.save_state(state)


def configure_scheduler_logging(level: int = logging.INFO) -> Path:
    """
    Log to stdout and to scheduler.log in the state directory (rotated at 5 MB, five
    kept), so a background daemon leaves a record. Safe to call more than once.

    Returns:
        The log file path
    """
    log_path = get_state_dir() / "scheduler.log"
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > level:  # never raise a lower level
        root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    if not any(
        isinstance(h, RotatingFileHandler) and h.baseFilename == os.path.abspath(log_path)
        for h in root.handlers
    ):
        file_handler = RotatingFileHandler(
            log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        # stdout, so a daemon's stderr holds only crashes (see DaemonManager.start)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)
    return log_path


class ScheduleStatus(Enum):
    """Status of scheduled task execution"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"  # cut off by the cron watchdog; may run again (MAX_TIMEOUTS_PER_DAY)
    RETRYING = "retrying"
    DRY_RUN = "dry_run"  # ran without sending orders; not the day's routine (#601)


@dataclass
class ScheduledTask:
    """Represents a scheduled trading routine"""

    name: str
    routine_type: RoutineType
    scheduled_time: dt_time  # e.g., time(9, 20) for 9:20 AM
    timezone: str = "America/New_York"  # ET timezone for market hours
    retry_count: int = 3
    retry_delay_seconds: int = 60


@dataclass
class ExecutionLog:
    """Log entry for task execution"""

    task_name: str
    scheduled_time: str
    actual_start_time: str
    actual_end_time: Optional[str] = None
    status: str = ScheduleStatus.PENDING.value
    attempt: int = 1
    error_message: Optional[str] = None
    report_path: Optional[str] = None
    api_calls_used: int = 0
    market_date: Optional[str] = None  # the trading day (ET) the run is for


class RunOutcome(Enum):
    """How a routine's recorded runs for a trading day stand."""

    OK = "ok"  # a run completed (or ran dry)
    FAILED = "failed"  # no run completed, and one failed after its retries
    STUCK = "stuck"  # a run started, never finished, and is too old to be still running
    RUNNING = "running"  # a run started and hasn't finished, recently
    MISSED = "missed"  # no run is recorded


def _local_time(stamp: str) -> datetime:
    """A time saved by now_iso() (the host's local time, no zone) as an aware datetime."""
    moment = datetime.fromisoformat(stamp)
    return moment if moment.tzinfo else moment.astimezone()


def run_outcome(
    runs: Sequence[SchedulerExecution], now: datetime
) -> Tuple[RunOutcome, Optional[SchedulerExecution]]:
    """
    How a routine's runs for a trading day stand, and the run that says so.

    A completed run (or a dry run) settles it, then a failure; a run that started and never
    finished is stuck once it is STUCK_AFTER old; with no runs the routine was missed.

    Args:
        runs: The day's runs of one routine, oldest first (SchedulerStateManager.executions_on)
        now: The current time (aware)
    """
    for outcome, statuses in (
        (RunOutcome.OK, ("completed", "dry_run")),
        (RunOutcome.FAILED, ("failed", "timed_out")),
    ):
        match = next((run for run in reversed(runs) if run.status in statuses), None)
        if match is not None:
            return outcome, match
    if not runs:
        return RunOutcome.MISSED, None
    oldest = runs[0]
    stuck = now - _local_time(oldest.started_at) >= STUCK_AFTER
    return (RunOutcome.STUCK if stuck else RunOutcome.RUNNING), oldest


# What a position alert becomes: (notice kind, severity, what the price did)
_BREACH_NOTICES = {
    AlertType.LOSS_THRESHOLD_REACHED.value: (STOP_BREACH, CRITICAL, "at or below its stop"),
    AlertType.PROFIT_TARGET_REACHED.value: (TARGET_BREACH, WARNING, "at or above its target"),
}


def _items(value: Any) -> Tuple[Any, ...]:
    """A routine's list of alerts or discrepancies as a tuple; anything else is none."""
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def _text_or_none(value: Any) -> Optional[str]:
    """A string as it is, anything else (say, a mock's attribute) as None."""
    return value if isinstance(value, str) else None


class DailyScheduler:
    """
    Automated daily trading scheduler with retry logic and error handling.

    Features:
    - Two daily routines: morning (9:20 AM ET), evening (3:50 PM ET)
    - Automatic retry with exponential backoff
    - Comprehensive error handling and logging
    - Crash recovery and state persistence
    - Minimal API calls (3-5 per routine)
    """

    def __init__(
        self, config_file: str = None, trading_cycle=None, state_manager=None, notice_store=None
    ):
        """
        Initialize the daily scheduler.

        Args:
            config_file: Path to scheduler configuration file (YAML or JSON)
            trading_cycle: Optional CostEfficientTradeCycle instance to reuse
                (reduces client instantiation)
            state_manager: Optional SchedulerStateManager (default: the shared one)
            notice_store: Optional NoticeStore (default: state/notices.json, opened on first use)
        """
        # Load paths configuration
        paths_config_file = "config_defaults/paths_config.yaml"
        try:
            with open(paths_config_file) as f:
                self.paths = yaml.safe_load(f) if yaml else {}
                logger.info(f"Loaded paths config from {paths_config_file}")
        except (FileNotFoundError, TypeError):
            logger.warning(
                f"Paths config not found at {paths_config_file}, using hardcoded defaults"
            )
            self.paths = {
                "state_files": {"scheduler_log": "state/scheduler_execution_log.json"},
                "log_files": {"scheduler": "state/scheduler.log"},
            }

        self.config = load_scheduler_config(config_file)
        self._notice_store = notice_store
        # Reuse provided trading_cycle, else create one when a routine first needs it
        self._trading_cycle = trading_cycle
        if trading_cycle is not None:
            self._share_notice_store(trading_cycle)
        self.execution_log: List[ExecutionLog] = []
        self.run_limit_seconds: Optional[int] = None  # the cron watchdog's limit, when armed
        # The market calendar's answers during one check, so the lookup should_run_task makes
        # is reused by check_routine_outcomes (see run_scheduled_tasks)
        self._hours_memo: Dict[date, Tuple[dt_time, dt_time, bool]] = {}

        # Execution log file name from config, kept in the state directory
        log_file_path = self.paths.get("state_files", {}).get(
            "scheduler_log", "state/scheduler_execution_log.json"
        )
        self.log_file = get_state_dir() / Path(log_file_path).name

        # Initialize state manager for SQLite persistence (#478)
        self.state_manager = state_manager or get_scheduler_state_manager()

        # Define scheduled tasks
        self.tasks = self._create_default_tasks()

        # Load previous execution log (migrates from JSON to SQLite if needed)
        self._load_execution_log()

        logger.info("DailyScheduler initialized with %d tasks", len(self.tasks))

    def warn_if_retries_exceed(self, limit_seconds: int) -> None:
        """Warn when a routine's retry delays alone could outlast the cron watchdog limit."""
        for task in self.tasks:
            sleep = sum(task.retry_delay_seconds * 2**n for n in range(task.retry_count))
            if sleep >= limit_seconds:
                logger.warning(
                    "%s: retry delays total %d s, past the %d s run limit; "
                    "a routine that keeps failing is cut off before its last retry",
                    task.name,
                    sleep,
                    limit_seconds,
                )

    @property
    def trading_cycle(self) -> CostEfficientTradeCycle:
        """The trading cycle, created on first use: a check with nothing due needs none."""
        if self._trading_cycle is None:
            self._trading_cycle = CostEfficientTradeCycle()
            self._share_notice_store(self._trading_cycle)
        return self._trading_cycle

    @property
    def notice_store(self) -> NoticeStore:
        """The notices (state/notices.json), opened on first use."""
        if self._notice_store is None:
            self._notice_store = NoticeStore()
        return self._notice_store

    @notice_store.setter
    def notice_store(self, value: NoticeStore) -> None:
        self._notice_store = value
        if self._trading_cycle is not None:
            self._share_notice_store(self._trading_cycle)

    def _share_notice_store(self, cycle) -> None:
        """Give the trading cycle the notices, so its reports list the open ones."""
        cycle.notice_store = self.notice_store

    @property
    def dry_run(self) -> bool:
        """The config's dry_run: routines send no orders and change no saved state (#601)."""
        return bool(self.config.get("dry_run", False))

    @trading_cycle.setter
    def trading_cycle(self, value: CostEfficientTradeCycle) -> None:
        self._trading_cycle = value
        self._share_notice_store(value)

    def _create_default_tasks(self) -> List[ScheduledTask]:
        """Create default morning and evening tasks"""
        # Parse times from config
        morning_time = dt_time.fromisoformat(self.config["morning_routine_time"])
        evening_time = dt_time.fromisoformat(self.config["evening_routine_time"])

        return [
            ScheduledTask(
                name="morning_routine",
                routine_type=RoutineType.MORNING,
                scheduled_time=morning_time,
                timezone=self.config["market_timezone"],
                retry_count=self.config["max_retries"],
                retry_delay_seconds=self.config["retry_delay_seconds"],
            ),
            ScheduledTask(
                name="evening_routine",
                routine_type=RoutineType.EVENING,
                scheduled_time=evening_time,
                timezone=self.config["market_timezone"],
                retry_count=self.config["max_retries"],
                retry_delay_seconds=self.config["retry_delay_seconds"],
            ),
        ]

    def _load_execution_log(self):
        """Load execution log from disk"""
        if self.log_file.exists():
            try:
                with open(self.log_file, "r") as f:
                    log_data = json.load(f)
                    self.execution_log = [ExecutionLog(**entry) for entry in log_data]
                logger.info("Loaded %d execution log entries", len(self.execution_log))
            except Exception as e:
                logger.warning("Failed to load execution log: %s", e)
                self.execution_log = []

    def _save_execution_log(self):
        """Save execution log to disk"""
        try:
            write_json_atomic(str(self.log_file), [asdict(entry) for entry in self.execution_log])
        except Exception as e:
            logger.error("Failed to save execution log: %s", e)

    def _execute_task_with_retry(self, task: ScheduledTask) -> ExecutionLog:
        """
        Execute a scheduled task with retry logic.

        Args:
            task: The scheduled task to execute

        Returns:
            ExecutionLog with execution results
        """
        log_entry = ExecutionLog(
            task_name=task.name,
            scheduled_time=task.scheduled_time.isoformat(),
            actual_start_time=now_iso(),
            status=ScheduleStatus.RUNNING.value,
        )

        # Record execution start in SQLite (#478), under the trading day it runs for
        routine_type = task.name.replace("_routine", "")
        market_date = self._market_now().date().isoformat()
        log_entry.market_date = market_date
        execution_id = self.state_manager.record_execution_start(
            routine_type, attempt=1, market_date=market_date
        )

        try:
            self._run_attempts(task, log_entry, execution_id)
        except RunDueTimeoutError as timeout:
            # Not an Exception: the attempts' handlers let it through, so no retry
            self._record_timeout(log_entry, execution_id, timeout)
            timeout.log_entry = log_entry
            raise
        return log_entry

    def _record_timeout(
        self, log_entry: ExecutionLog, execution_id: int, timeout: RunDueTimeoutError
    ) -> None:
        """
        Record a run cut off by the cron watchdog as timed out (see watchdog.py). Unlike a
        failure, a timeout does not settle the day: the routine may start again on a later
        tick (MAX_TIMEOUTS_PER_DAY). A run that had already finished keeps its result.
        """
        finished = (ScheduleStatus.COMPLETED.value, ScheduleStatus.DRY_RUN.value)
        if log_entry.status in finished:  # the alarm landed after the routine's last step
            self.state_manager.record_execution_complete(
                execution_id,
                status=log_entry.status,
                report_path=log_entry.report_path,
                api_calls_used=log_entry.api_calls_used,
                only_if_running=True,
            )
            return
        log_entry.error_message = f"Attempt {log_entry.attempt} {timeout}"
        log_entry.status = ScheduleStatus.TIMED_OUT.value
        log_entry.actual_end_time = now_iso()
        logger.error("❌ %s - %s", log_entry.task_name, log_entry.error_message)
        self.state_manager.record_execution_complete(
            execution_id,
            status=ScheduleStatus.TIMED_OUT.value,
            error_message=log_entry.error_message,
            only_if_running=True,
        )

    def _run_attempts(self, task: ScheduledTask, log_entry: ExecutionLog, execution_id: int):
        """The attempts of _execute_task_with_retry: fills in log_entry."""
        max_attempts = task.retry_count + 1  # Initial attempt + retries
        base_delay = task.retry_delay_seconds

        for attempt in range(1, max_attempts + 1):
            log_entry.attempt = attempt

            try:
                logger.info("Executing %s (attempt %d/%d)", task.name, attempt, max_attempts)
                # Each attempt starts from fresh broker state: a retry within the cache's
                # TTL would otherwise re-place stops that the failed attempt already placed
                invalidate = getattr(self.trading_cycle, "invalidate_broker_cache", None)
                if callable(invalidate):
                    invalidate()

                # Execute the appropriate routine; failures raise RoutineError, including
                # protective stops that couldn't be checked, placed or renewed
                if self.dry_run:
                    logger.info("DRY RUN: %s sends no orders and changes no saved state", task.name)
                if task.routine_type == RoutineType.MORNING:
                    self.trading_cycle.morning_routine(raise_errors=True, dry_run=self.dry_run)
                elif task.routine_type == RoutineType.EVENING:
                    self.trading_cycle.evening_routine(raise_errors=True, dry_run=self.dry_run)
                elif task.routine_type == RoutineType.RECOVERY:
                    self.trading_cycle.recover_from_crash(raise_errors=True, dry_run=self.dry_run)
                else:
                    raise ValueError(f"Unknown routine type: {task.routine_type}")

                # Success! A dry run is recorded as such, so it doesn't count as the
                # day's routine: switched off within the window, the routine still runs
                status = ScheduleStatus.DRY_RUN if self.dry_run else ScheduleStatus.COMPLETED
                log_entry.status = status.value
                log_entry.actual_end_time = now_iso()
                log_entry.api_calls_used = 3  # Typical API calls per routine

                report_path = getattr(self.trading_cycle, "last_report_path", None)
                log_entry.report_path = report_path

                # Record completion in SQLite (#478)
                self.state_manager.record_execution_complete(
                    execution_id,
                    status=status.value,
                    report_path=report_path,
                    api_calls_used=log_entry.api_calls_used,
                )

                logger.info(
                    "✅ %s completed successfully%s",
                    task.name,
                    " (dry run)" if self.dry_run else "",
                )
                break

            except Exception as e:
                error_msg = f"Attempt {attempt} failed: {str(e)}"
                logger.error("❌ %s - %s", task.name, error_msg)

                log_entry.error_message = error_msg

                if attempt < max_attempts:
                    # Calculate exponential backoff with jitter
                    delay = base_delay * (2 ** (attempt - 1))
                    jitter = delay * 0.1  # 10% jitter
                    actual_delay = delay + (time.time() % jitter)

                    log_entry.status = ScheduleStatus.RETRYING.value
                    logger.info("Retrying in %.1f seconds...", actual_delay)
                    time.sleep(actual_delay)
                else:
                    # All attempts failed
                    log_entry.status = ScheduleStatus.FAILED.value
                    log_entry.actual_end_time = now_iso()
                    logger.error("❌ %s failed after %d attempts", task.name, max_attempts)

                    # Record failure in SQLite (#478)
                    self.state_manager.record_execution_complete(
                        execution_id,
                        status="failed",
                        error_message=log_entry.error_message,
                    )

    def _market_now(self) -> datetime:
        """Current time in the market timezone (ET by default), whatever the host's."""
        return datetime.now(ZoneInfo(self.config["market_timezone"]))

    def effective_settings(self) -> Dict[str, Any]:
        """Enable flags in force (see effective_flags())."""
        return effective_flags(self.config, self.state_manager)

    def save_flags(self, **flags: Optional[bool]) -> bool:
        """Save enable flags (enabled / morning / evening) to state/user.db."""
        return save_flags(self.config, self.state_manager, **flags)

    def scheduled_at(self, task: ScheduledTask, day) -> Optional[datetime]:
        """
        When a task runs on a day, in the market timezone; None if the market is closed.

        On an early close the evening routine moves back as far as the close does
        (15:50 becomes 12:50 for a 13:00 close), so it still runs before the close.

        The calendar is asked once per day until the next run_scheduled_tasks(), however
        many times a check needs the time.
        """
        _, close, trading_day = self._market_hours(day)
        return self._run_time(task, day, close) if trading_day else None

    def _market_hours(self, day: date) -> Tuple[dt_time, dt_time, bool]:
        """(open, close, is a trading day) for a day, from the calendar once per check."""
        if day not in self._hours_memo:
            self._hours_memo[day] = get_market_hours_for_date(day)
            self._note_calendar_disagreement(day)
        return self._hours_memo[day]

    def _note_calendar_disagreement(self, day: date) -> None:
        """
        A WARNING notice when Alpaca's calendar and the NYSE rules disagreed on a day (the
        rules' answer was used). One notice per day, however many ticks see it. Contained.
        """
        try:
            why = calendar_disagreement(day)
            if why is None:
                return
            self.notice_store.raise_notice(
                key=f"{CALENDAR_DISAGREES}:{day.isoformat()}",
                kind=CALENDAR_DISAGREES,
                severity=WARNING,
                title=f"Market calendar disagrees with the NYSE rules on {day.isoformat()}",
                detail=(
                    f"{why}. If NYSE announced a closure or a change of hours, add it to "
                    "config_defaults/market_hours.yaml or the NYSE rules."
                ),
                market_date=day.isoformat(),
                update_existing=False,
            )
        except Exception as e:
            logger.warning("Calendar notice not recorded for %s: %s", day, e)

    def _nyse_scheduled_at(self, task: ScheduledTask, day: date) -> Optional[datetime]:
        """
        When a task runs on a day, from the NYSE rules alone (no calendar call):
        scheduled_at() without the one-off closures only the calendar knows.
        """
        if not is_nyse_trading_day(day):
            return None
        early = day in nyse_early_closes(day.year)
        return self._run_time(task, day, EARLY_MARKET_CLOSE if early else DEFAULT_MARKET_CLOSE)

    def run_deadline(
        self, task: ScheduledTask, day: date, rules_only: bool = False
    ) -> Optional[datetime]:
        """
        The last moment a task may start on a day, in the market timezone; None if the market
        is closed. At least RUN_WINDOW after its time. A routine that has not run yet may
        start late: the morning until MORNING_CATCH_UP after its time, the evening until
        EVENING_CLOSE_MARGIN before the close (12:59 on a 13:00 early close). A dry run
        keeps RUN_WINDOW, since it never counts as the day's run. So does a routine
        switched on after its window: turning it on late does not start a late run.

        Args:
            rules_only: Take the close from the NYSE rules, with no calendar call (the
                previous day, or a check that didn't get the run lock)
        """
        if rules_only:
            when = self._nyse_scheduled_at(task, day)
        else:
            when = self.scheduled_at(task, day)
        if when is None:
            return None
        deadline = when + RUN_WINDOW
        if self.dry_run or self._enabled_after(task, deadline):
            return deadline
        if task.routine_type == RoutineType.MORNING:
            deadline = max(deadline, when + MORNING_CATCH_UP)
        elif task.routine_type == RoutineType.EVENING:
            close = self._close_time(day, rules_only)
            closing = datetime.combine(day, close, tzinfo=ZoneInfo(task.timezone))
            deadline = max(deadline, closing - EVENING_CLOSE_MARGIN)
        return deadline

    def _enabled_after(self, task: ScheduledTask, moment: datetime) -> bool:
        """Was the routine last switched on after a moment (an unreadable state: no)?"""
        try:
            enabled_at = self._routines_enabled_at().get(task.name)
        except Exception:
            return False
        return enabled_at is not None and enabled_at > moment

    def _close_time(self, day: date, rules_only: bool) -> dt_time:
        """The close on a trading day: the calendar's, or the NYSE rules'."""
        if rules_only:
            early = day in nyse_early_closes(day.year)
            return EARLY_MARKET_CLOSE if early else DEFAULT_MARKET_CLOSE
        return self._market_hours(day)[1]

    @staticmethod
    def _run_time(task: ScheduledTask, day: date, close: dt_time) -> datetime:
        """A task's time on a trading day that closes at `close` (see scheduled_at)."""
        when = datetime.combine(day, task.scheduled_time, tzinfo=ZoneInfo(task.timezone))
        if task.routine_type == RoutineType.EVENING and close < DEFAULT_MARKET_CLOSE:
            when -= datetime.combine(day, DEFAULT_MARKET_CLOSE) - datetime.combine(day, close)
        return when

    def should_run_task(self, task: ScheduledTask, now: Optional[datetime] = None) -> bool:
        """
        Check if a task should run now: the scheduler and the routine are enabled, the
        market is open that day, it's past the routine's time in the market timezone but not
        past its deadline (run_deadline: 5 minutes, or longer to catch up on a routine that
        has not run), and the routine hasn't completed or failed that day.

        Args:
            task: The scheduled task to check
            now: Current time (default: now in the task's timezone)

        Returns:
            True if the task should run now
        """
        routine_type = task.name.replace("_routine", "")
        settings = self.effective_settings()
        if not settings["enabled"] or not settings.get(routine_type, True):
            return False

        now = now or datetime.now(ZoneInfo(task.timezone))
        scheduled = self.scheduled_at(task, now.date())
        if scheduled is None:
            logger.debug("Skipping %s: %s is not a trading day", task.name, now.date())
            return False
        deadline = self.run_deadline(task, now.date())
        if now < scheduled or deadline is None or now > deadline:
            return False

        if self.state_manager.was_routine_finished_today(routine_type, now.date().isoformat()):
            logger.debug("Task %s already completed or failed today (from DB)", task.name)
            return False
        if self.state_manager.count_timeouts(routine_type, now.date().isoformat()) >= (
            MAX_TIMEOUTS_PER_DAY
        ):
            logger.debug("Task %s timed out %d times today", task.name, MAX_TIMEOUTS_PER_DAY)
            return False

        if now - scheduled > RUN_WINDOW:
            logger.info(
                "Starting %s late: scheduled %s, now %s",
                task.name,
                f"{scheduled:%H:%M}",
                f"{now:%H:%M}",
            )
        return True

    def next_run(self, now: Optional[datetime] = None) -> Optional[Tuple[ScheduledTask, datetime]]:
        """
        The next enabled routine on a trading day and when it runs (market timezone), or
        None if the scheduler or every routine is disabled.
        """
        settings = self.effective_settings()
        tasks = [t for t in self.tasks if settings.get(t.name.replace("_routine", ""), True)]
        if not settings["enabled"] or not tasks:
            return None

        now = now or self._market_now()
        for offset in range(15):  # covers any run of holidays and weekends
            day = (now + timedelta(days=offset)).date()
            runs = [(self.scheduled_at(task, day), task) for task in tasks]
            for when, task in sorted((r for r in runs if r[0] is not None), key=lambda r: r[0]):
                routine = task.name.replace("_routine", "")
                done = self.state_manager.was_routine_finished_today(routine, day.isoformat()) or (
                    self.state_manager.count_timeouts(routine, day.isoformat())
                    >= (MAX_TIMEOUTS_PER_DAY)
                )
                deadline = self.run_deadline(task, day)
                if deadline is not None and deadline >= now and not done:
                    return task, max(when, now)  # not in the past while it may still start late
        return None

    @contextmanager
    def run_lock(self):
        """
        Exclusive lock while routines run, so a daemon, a cron run and a manual run (or
        two cron runs during a long retry) never run them at the same time.

        Yields:
            True if acquired, False if another process holds it
        """
        if fcntl is None:
            yield True
            return
        with open(get_state_dir() / "scheduler.lock", "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _run_task(self, task: ScheduledTask) -> ExecutionLog:
        """Execute a task with retries, then log and save the result and note its outcome."""
        if self.run_limit_seconds:
            self.warn_if_retries_exceed(self.run_limit_seconds)  # only when a routine starts
        try:
            log_entry = self._execute_task_with_retry(task)
        except RunDueTimeoutError as timeout:
            if timeout.log_entry is not None:  # cut off mid-routine: leave its record, then stop
                self._finish_run(task, timeout.log_entry)
                timeout.routine_recorded = True
            raise
        self._finish_run(task, log_entry)
        return log_entry

    def _finish_run(self, task: ScheduledTask, log_entry: ExecutionLog) -> None:
        """Log and save a run's result and note its outcome."""
        self.execution_log.append(log_entry)
        self._save_execution_log()
        self._log_execution_summary(log_entry)
        self._record_notices(task, log_entry)

    def run_scheduled_tasks(self) -> List[ExecutionLog]:
        """
        Run any tasks that are due now, then check the routines' outcomes (notices).
        This should be called periodically (e.g., every minute).

        Returns:
            Log entries of the tasks run (none if another run holds the lock)
        """
        ran: List[ExecutionLog] = []
        self._hours_memo.clear()
        with self.run_lock() as acquired:
            if acquired:
                for task in self.tasks:
                    if self.should_run_task(task):
                        logger.info("🚀 Starting scheduled task: %s", task.name)
                        ran.append(self._run_task(task))
            else:
                logger.info("Another scheduler run is in progress; skipping this check")
        # Lock or not: a run that never finishes is one that keeps others from getting it
        self.check_routine_outcomes()
        return ran

    # -------------------------------------------------------------------------
    # Notices: what a person should look at (#342)
    # -------------------------------------------------------------------------

    def _record_notices(self, task: ScheduledTask, log_entry: ExecutionLog) -> None:
        """
        Notices for a run's outcome: a routine that failed after its retries; a completed
        run clears its routine's earlier notices for the day; and, for a real run, the
        positions it found through their stop or target or without a protective stop.

        Contained: a notice problem never fails a routine or hides its result.
        """
        try:
            routine = task.name.replace("_routine", "")
            market_date = log_entry.market_date or self._market_now().date().isoformat()
            completed = log_entry.status == ScheduleStatus.COMPLETED.value
            if log_entry.status in (ScheduleStatus.FAILED.value, ScheduleStatus.TIMED_OUT.value):
                self._notice_failure(routine, market_date, log_entry)
            elif completed or log_entry.status == ScheduleStatus.DRY_RUN.value:
                self.notice_store.resolve_kinds(routine, market_date)
            if not self.dry_run and task.routine_type in (RoutineType.MORNING, RoutineType.EVENING):
                self._notice_positions(market_date, completed)
        except Exception as e:
            logger.warning("Notices not recorded for %s: %s", task.name, e, exc_info=True)

    def _notice_failure(self, routine: str, market_date: str, log_entry: ExecutionLog) -> None:
        """The notice for a routine that failed on every attempt."""
        report_path = _text_or_none(log_entry.report_path) or _text_or_none(
            getattr(self._trading_cycle, "last_report_path", None)
        )
        attempts = f"{log_entry.attempt} attempt" + ("s" if log_entry.attempt != 1 else "")
        self.notice_store.raise_notice(
            key=routine_key(ROUTINE_FAILED, routine, market_date),
            kind=ROUTINE_FAILED,
            severity=CRITICAL,
            title=f"{routine.title()} routine failed after {attempts}",
            detail=log_entry.error_message or "",
            market_date=market_date,
            report_path=report_path,
        )

    def _notice_positions(self, market_date: str, run_completed: bool) -> None:
        """
        Notices for what the latest routine found: positions through their stop or target
        while still open, and positions left without a protective stop. A completed run
        that finds a position protected again clears its "unprotected" notice, and one
        that finds a position gone clears its breach notices (#634).
        """
        cycle = self._trading_cycle
        report_path = _text_or_none(getattr(cycle, "last_report_path", None))
        for alert in _items(getattr(cycle, "last_alerts", ())):
            self._notice_breach(alert, market_date, report_path)
        unprotected = {
            d.symbol: d
            for d in _items(getattr(cycle, "last_discrepancies", ()))
            if d.type == "MISSING_STOP" and d.action == "NEEDS_HUMAN_REVIEW"
        }
        for symbol, discrepancy in unprotected.items():
            details = discrepancy.details or {}
            self.notice_store.raise_notice(
                key=f"{UNPROTECTED}:{symbol}",
                kind=UNPROTECTED,
                severity=CRITICAL,
                title=f"{symbol} has no protective stop",
                detail=details.get("error") or details.get("reason") or "",
                market_date=market_date,
                symbol=symbol,
                report_path=report_path,
            )
        if run_completed:
            self.notice_store.resolve_cleared(UNPROTECTED, unprotected)
            open_symbols = getattr(cycle, "last_open_symbols", None)
            if isinstance(open_symbols, frozenset):
                for kind in (STOP_BREACH, TARGET_BREACH):
                    self.notice_store.resolve_cleared(kind, open_symbols)

    def _notice_breach(self, alert, market_date: str, report_path: Optional[str]) -> None:
        """A notice for a CRITICAL alert: the price is through the stop or target, position open."""
        spec = _BREACH_NOTICES.get(alert.alert_type)
        if spec is None or alert.severity != "CRITICAL":
            return
        kind, severity, what = spec
        self.notice_store.raise_notice(
            key=f"{kind}:{alert.symbol}:{market_date}",
            kind=kind,
            severity=severity,
            title=f"{alert.symbol} is {what}, still open",
            detail=re.sub(r"^[^\w$]+", "", alert.message),  # without the severity emoji
            market_date=market_date,
            symbol=alert.symbol,
            report_path=report_path,
        )

    def check_routine_outcomes(self, now: Optional[datetime] = None) -> None:
        """
        Notices for routines that failed, got stuck or never ran, found from the saved
        history: today's once the run window and a grace period have passed, and the
        previous trading day's.

        Cron runs this on every tick, so it makes no broker call and no calendar call of
        its own: today's time comes from the calendar answer should_run_task got, or from
        the NYSE rules when the tick didn't get the lock, and the previous day's from the
        NYSE rules. It never calls should_run_task and never raises: a problem here must
        not stop the tick.

        Args:
            now: Current time (default: now in the market timezone)
        """
        try:
            if now is None:
                now = self._market_now()
            else:
                zone = ZoneInfo(self.config["market_timezone"])
                now = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
            self._check_outcomes(now)
        except Exception as e:
            logger.warning("Routine outcomes not checked: %s", e, exc_info=True)

    def _check_outcomes(self, now: datetime) -> None:
        """The outcome check for the previous trading day and today (see check_routine_outcomes)."""
        today = now.date()
        days = [date.fromisoformat(add_business_days(today.isoformat(), -1)), today]
        try:
            settings = self.effective_settings()
            if not settings["enabled"]:
                return
            enabled_at = self._routines_enabled_at()
            runs = self.state_manager.executions_on([day.isoformat() for day in days])
        except Exception as e:
            self._note_state_db_error(now, e)
            return
        for task in self.tasks:
            if settings.get(task.name.replace("_routine", ""), True):
                for day in days:
                    self._check_day(task, day, runs, enabled_at.get(task.name), now)
        self.notice_store.resolve(f"{STATE_DB_ERROR}:{today}")

    def _check_day(
        self,
        task: ScheduledTask,
        day: date,
        runs: Dict[Tuple[str, str], List[SchedulerExecution]],
        enabled_at: Optional[datetime],
        now: datetime,
    ) -> None:
        """
        One routine's notice, or the clearing of its notices, for one trading day. A miss is
        excused when the routine was switched on (enabled_at) only after its window.
        """
        is_today = day == now.date()
        # today's calendar answer should_run_task got; otherwise (the previous day, or a
        # tick that didn't get the lock) no calendar call
        rules_only = not (is_today and day in self._hours_memo)
        deadline = self.run_deadline(task, day, rules_only=rules_only)
        if deadline is None:
            return  # no session that day
        when = self._nyse_scheduled_at(task, day) if rules_only else self.scheduled_at(task, day)
        if is_today and now < when + RUN_WINDOW + OUTCOME_GRACE:
            return  # nothing is overdue yet
        routine = task.name.replace("_routine", "")
        market_date = day.isoformat()
        outcome, run = run_outcome(runs.get((routine, market_date), []), now)
        if outcome is RunOutcome.MISSED and is_today and now < deadline + OUTCOME_GRACE:
            return  # a routine with no run may still start late (run_deadline)
        if outcome is RunOutcome.OK:
            self.notice_store.resolve_kinds(routine, market_date)
        elif outcome is RunOutcome.FAILED:
            self._ensure_notice(
                ROUTINE_FAILED, routine, market_date, "failed", run.error_message, run.report_path
            )
        elif outcome is RunOutcome.STUCK:
            started = _local_time(run.started_at).astimezone(now.tzinfo)
            detail = (
                f"A run started at {started:%H:%M} ET and never finished; a hung or crashed "
                "process may be holding state/scheduler.lock."
            )
            self._ensure_notice(
                ROUTINE_STUCK, routine, market_date, "started and never finished", detail
            )
        elif outcome is RunOutcome.MISSED and not (enabled_at and enabled_at > deadline):
            detail = (
                f"No run is recorded for the {when:%H:%M} ET {routine} routine on {market_date}. "
                "Is cron running? See state/scheduler.log and state/scheduler-cron.err."
            )
            self._ensure_notice(
                ROUTINE_MISSED, routine, market_date, f"did not run on {market_date}", detail
            )

    def _ensure_notice(
        self,
        kind: str,
        routine: str,
        market_date: str,
        what: str,
        detail: Optional[str],
        report_path: Optional[str] = None,
    ) -> None:
        """
        Raise a routine's notice unless it is already open: the history is read on every
        tick, so a condition that lasts is one notice, not one per tick.
        """
        self.notice_store.raise_notice(
            key=routine_key(kind, routine, market_date),
            kind=kind,
            severity=CRITICAL,
            title=f"{routine.title()} routine {what}",
            detail=detail or "",
            market_date=market_date,
            report_path=_text_or_none(report_path),
            update_existing=False,
        )

    def _routines_enabled_at(self) -> Dict[str, datetime]:
        """
        When each routine was last switched on, by task name (state/user.db). Saves that
        change nothing else (a time, the other routine) don't count; empty while the config
        file's flags apply, or for a database saved before the stamps existed.
        """
        if not self.state_manager.has_saved_state():
            return {}
        state = self.state_manager.get_state()
        stamps = {f"{r}_routine": getattr(state, f"{r}_enabled_at") for r in ("morning", "evening")}
        return {name: _local_time(stamp) for name, stamp in stamps.items() if stamp}

    def _note_state_db_error(self, now: datetime, error: Exception) -> None:
        """A notice that the scheduler's saved flags or history can't be read."""
        logger.error("Scheduler state unreadable: %s", error, exc_info=True)
        today = now.date().isoformat()
        self.notice_store.raise_notice(
            key=f"{STATE_DB_ERROR}:{today}",
            kind=STATE_DB_ERROR,
            severity=CRITICAL,
            title="Scheduler state database unreadable",
            detail=f"{type(error).__name__}: {error}",
            market_date=today,
            update_existing=False,
        )

    def _log_execution_summary(self, log_entry: ExecutionLog):
        """Log execution summary for monitoring"""
        duration = "N/A"
        if log_entry.actual_end_time:
            start = parse_date_string(log_entry.actual_start_time)
            end = parse_date_string(log_entry.actual_end_time)
            duration = f"{(end - start).total_seconds():.1f}s"

        status_emoji = {
            ScheduleStatus.COMPLETED.value: "✅",
            ScheduleStatus.FAILED.value: "❌",
            ScheduleStatus.TIMED_OUT.value: "⏱",
            ScheduleStatus.RETRYING.value: "🔄",
            ScheduleStatus.DRY_RUN.value: "🧪",
        }.get(log_entry.status, "⚠️")

        logger.info(
            "%s Task: %s | Status: %s | Duration: %s | Attempt: %d | API Calls: %d",
            status_emoji,
            log_entry.task_name,
            log_entry.status,
            duration,
            log_entry.attempt,
            log_entry.api_calls_used,
        )

        if log_entry.error_message:
            logger.error("Error: %s", log_entry.error_message)

    async def run_daemon(self, check_interval_seconds: int = 60):
        """
        Run scheduler as a daemon process.

        Args:
            check_interval_seconds: How often to check for scheduled tasks (default: 60s)
        """
        logger.info(
            "🤖 Starting DailyScheduler daemon (check interval: %ds)", check_interval_seconds
        )
        settings = self.effective_settings()
        logger.info(
            "Scheduler %s (%s); trading days only, times in %s",
            "enabled" if settings["enabled"] else "DISABLED",
            settings["source"],
            self.config["market_timezone"],
        )
        for task in self.tasks:
            routine_on = settings.get(task.name.replace("_routine", ""), True)
            logger.info(
                "  - %s at %s%s",
                task.name,
                task.scheduled_time.strftime("%H:%M:%S"),
                "" if routine_on else " (disabled)",
            )

        try:
            while True:
                try:
                    # Check and run scheduled tasks
                    self.run_scheduled_tasks()

                    # Sleep until next check
                    await asyncio.sleep(check_interval_seconds)

                except KeyboardInterrupt:
                    logger.info("Received shutdown signal")
                    break
                except Exception as e:
                    logger.error("Error in daemon loop: %s", e)
                    # Continue running despite errors
                    await asyncio.sleep(check_interval_seconds)

        finally:
            logger.info("DailyScheduler daemon stopped")

    def run_once(self, task_name: Optional[str] = None):
        """
        Run a specific task once (for testing/manual execution).

        Args:
            task_name: Name of task to run, or None to run all pending tasks
        """
        if task_name:
            # Run specific task
            task = next((t for t in self.tasks if t.name == task_name), None)
            if not task:
                logger.error("Task not found: %s", task_name)
                return

            # A manual run ignores the schedule and calendar, but not another run
            with self.run_lock() as acquired:
                if not acquired:
                    logger.error("Another scheduler run is in progress; not running %s", task_name)
                    return
                logger.info("Running task once: %s", task_name)
                self._run_task(task)
        else:
            # Run all pending tasks
            logger.info("Running all scheduled tasks once")
            self.run_scheduled_tasks()

    def get_execution_history(self, days: int = 7) -> List[ExecutionLog]:
        """
        Get execution history for the last N days.

        Args:
            days: Number of days to retrieve

        Returns:
            List of ExecutionLog entries
        """
        cutoff = get_datetime_now() - timedelta(days=days)

        history = [
            entry
            for entry in self.execution_log
            if parse_date_string(entry.actual_start_time) >= cutoff
        ]

        return sorted(history, key=lambda x: x.actual_start_time, reverse=True)

    def generate_status_report(self) -> str:
        """Generate a status report of recent executions"""
        recent = self.get_execution_history(days=7)

        if not recent:
            return "No execution history available"

        lines = [
            "# Daily Scheduler Status Report",
            f"Generated: {get_datetime_now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Recent Executions (Last 7 Days)",
            "",
        ]

        # Group by task
        by_task: Dict[str, List[ExecutionLog]] = {}
        for entry in recent:
            if entry.task_name not in by_task:
                by_task[entry.task_name] = []
            by_task[entry.task_name].append(entry)

        for task_name, entries in by_task.items():
            lines.append(f"### {task_name}")
            lines.append(f"Total executions: {len(entries)}")

            completed = sum(1 for e in entries if e.status == ScheduleStatus.COMPLETED.value)
            failed = sum(
                1
                for e in entries
                if e.status in (ScheduleStatus.FAILED.value, ScheduleStatus.TIMED_OUT.value)
            )

            lines.append(f"✅ Completed: {completed}")
            lines.append(f"❌ Failed: {failed}")
            lines.append(f"Success rate: {(completed / len(entries) * 100):.1f}%")
            lines.append("")

        lines.extend(
            [
                "## Configuration",
                f"Morning routine: {self.config['morning_routine_time']} ET",
                f"Evening routine: {self.config['evening_routine_time']} ET",
                f"Max retries: {self.config['max_retries']}",
                f"Enabled: {self.effective_settings()['enabled']}",
                "",
            ]
        )

        return "\n".join(lines)


def main():
    """Main entry point for the daily scheduler"""
    parser = argparse.ArgumentParser(description="AutoGen Daily Trading Scheduler")
    parser.add_argument(
        "--mode",
        choices=["daemon", "once", "status", "test"],
        default="daemon",
        help="Execution mode",
    )
    parser.add_argument(
        "--task",
        choices=["morning_routine", "evening_routine", "all"],
        help="Specific task to run (for 'once' mode)",
    )
    parser.add_argument(
        "--interval", type=int, default=60, help="Check interval in seconds (for daemon mode)"
    )

    args = parser.parse_args()

    configure_scheduler_logging()

    # Create scheduler
    scheduler = DailyScheduler()

    if args.mode == "daemon":
        # Run as daemon
        print("Starting scheduler daemon...")
        print("Press Ctrl+C to stop")
        asyncio.run(scheduler.run_daemon(check_interval_seconds=args.interval))

    elif args.mode == "once":
        # Run once
        task_name = args.task if args.task != "all" else None
        scheduler.run_once(task_name)

    elif args.mode == "status":
        # Show status report
        report = scheduler.generate_status_report()
        print(report)

    elif args.mode == "test":
        # Test mode - run morning routine immediately
        print("Test mode: Running morning routine...")
        scheduler.run_once("morning_routine")


if __name__ == "__main__":
    main()
