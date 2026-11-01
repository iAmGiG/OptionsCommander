"""
Scheduler Tools for daily scheduler.

Issue #433/#458: Extract scheduler display commands from cli_session.py.
Issue #478/#481: Add SQLite-backed scheduler state control.

These tools handle:
- Viewing scheduler status and configuration
- Getting execution history
- Calculating next scheduled run
- Enabling/disabling scheduler (SQLite-backed state)
- Viewing scheduler state from database
"""

import logging
from typing import Any, Dict, Optional

from src.utils.safe_print import get_symbol

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_scheduler():
    """
    Get DailyScheduler instance.

    Returns singleton or creates new instance.
    """
    try:
        from src.trading.scheduling.daily_scheduler import DailyScheduler

        return DailyScheduler()
    except Exception as e:
        logger.error("Failed to get scheduler: %s", e)
        return None


def _open_notice_count(scheduler) -> Optional[int]:
    """Notices nobody has acknowledged (read-only); None if they can't be read."""
    try:
        return len(scheduler.notice_store.open_notices())
    except Exception as e:
        logger.warning("Open notices not counted: %s", e)
        return None


def load_scheduler_config():
    """Scheduler config file settings (light: no trading clients)."""
    from src.trading.scheduling.daily_scheduler import load_scheduler_config as load

    return load()


def get_scheduler_status() -> Dict[str, Any]:
    """
    Get current scheduler status and configuration.

    Returns:
        Dict with:
        - status: "success" or "error"
        - enabled: Whether scheduler is enabled (saved state, else the config file)
        - morning_enabled / evening_enabled: Whether each routine is enabled
        - morning_time: Morning routine time (e.g., "09:20")
        - evening_time: Evening routine time (e.g., "15:50")
        - max_retries: Maximum retry attempts
        - timezone: Market timezone
        - open_notices: Failures and warnings from the unattended loop nobody has
          acknowledged (a person does that, in the scheduler CLI); None if unreadable
    """
    try:
        scheduler = _get_scheduler()
        if not scheduler:
            return {"status": "not_initialized", "message": "Scheduler not available"}

        config = scheduler.config
        flags = scheduler.effective_settings()

        return {
            "status": "success",
            "enabled": flags["enabled"],
            "morning_enabled": flags["morning"],
            "evening_enabled": flags["evening"],
            "flags_source": flags["source"],
            "morning_time": config.get("morning_routine_time", "09:20:00")[:5],
            "evening_time": config.get("evening_routine_time", "15:50:00")[:5],
            "max_retries": config.get("max_retries", 3),
            "retry_delay_seconds": config.get("retry_delay_seconds", 60),
            "timezone": config.get("market_timezone", "America/New_York"),
            "dry_run": config.get("dry_run", False),
            "open_notices": _open_notice_count(scheduler),
        }

    except Exception as e:
        logger.error("Error getting scheduler status: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


def get_execution_history(days: int = 7, limit: int = 10) -> Dict[str, Any]:
    """
    Get scheduler execution history.

    Args:
        days: Number of days to retrieve (default 7)
        limit: Maximum entries to return (default 10)

    Returns:
        Dict with:
        - status: "success" or "error"
        - history: List of execution log entries
        - summary: Statistics (completed, failed, success_rate)
    """
    try:
        scheduler = _get_scheduler()
        if not scheduler:
            return {"status": "not_initialized", "message": "Scheduler not available"}

        history = scheduler.get_execution_history(days=days)
        history = history[:limit]

        history_dicts = [
            {
                "task_name": entry.task_name,
                "scheduled_time": entry.scheduled_time,
                "actual_start_time": entry.actual_start_time,
                "actual_end_time": entry.actual_end_time,
                "status": entry.status,
                "attempt": entry.attempt,
                "error_message": entry.error_message,
                "api_calls_used": entry.api_calls_used,
            }
            for entry in history
        ]

        total = len(history)
        completed = sum(1 for h in history_dicts if h["status"] == "completed")
        failed = sum(1 for h in history_dicts if h["status"] == "failed")
        success_rate = (completed / total * 100) if total > 0 else 0

        return {
            "status": "success",
            "history": history_dicts,
            "total_entries": total,
            "summary": {
                "completed": completed,
                "failed": failed,
                "retrying": total - completed - failed,
                "success_rate": round(success_rate, 1),
            },
        }

    except Exception as e:
        logger.error("Error getting execution history: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


def get_next_scheduled_run() -> Dict[str, Any]:
    """
    Calculate the next scheduled routine run time (trading days only, enabled routines).

    Returns:
        Dict with:
        - status: "success" or "error"
        - next_routine: "morning" or "evening"
        - next_time: Next run time as ISO string
        - time_until: Human-readable time until next run
        - minutes_until: Minutes until next run
    """
    try:
        scheduler = _get_scheduler()
        if not scheduler:
            return {"status": "not_initialized", "message": "Scheduler not available"}

        if not scheduler.effective_settings()["enabled"]:
            return {"status": "disabled", "message": "Scheduler is disabled"}

        now = scheduler._market_now()
        upcoming = scheduler.next_run(now)
        if upcoming is None:
            return {"status": "disabled", "message": "No routine is enabled"}
        task, next_datetime = upcoming
        next_routine = task.name.replace("_routine", "")
        next_time_of_day = task.scheduled_time

        total_minutes = max(int((next_datetime - now).total_seconds() // 60), 0)
        hours, minutes = divmod(total_minutes, 60)

        time_until = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        return {
            "status": "success",
            "next_routine": next_routine,
            "next_time": next_datetime.isoformat(),
            "next_time_display": next_time_of_day.strftime("%H:%M"),
            "time_until": time_until,
            "minutes_until": total_minutes,
            "current_time_et": now.strftime("%H:%M:%S"),
        }

    except Exception as e:
        logger.error("Error calculating next run: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# =============================================================================
# Display Functions (Issue #459)
# =============================================================================


def show_scheduler() -> str:
    """Display scheduler status with formatted output."""
    status = get_scheduler_status()
    if status["status"] == "not_initialized":
        return f"{get_symbol('ERROR')} Scheduler not initialized"
    if status["status"] == "error":
        return f"{get_symbol('ERROR')} Error: {status.get('error', 'Unknown')}"

    lines = [f"{get_symbol('CYCLE')} Daily Scheduler Status", "=" * 40]
    enabled = (
        f"{get_symbol('SUCCESS')} Enabled"
        if status.get("enabled")
        else f"{get_symbol('ERROR')} Disabled"
    )
    lines.append(f"Status: {enabled}")
    lines.append(f"Morning Routine: {status.get('morning_time', '09:20')} ET")
    lines.append(f"Evening Routine: {status.get('evening_time', '15:50')} ET")
    lines.append(f"Max Retries: {status.get('max_retries', 3)}")

    next_run = get_next_scheduled_run()
    if next_run["status"] == "success":
        lines.extend(["", f"{get_symbol('TARGET')} Next Scheduled Run"])
        lines.append(f"   {next_run['next_routine'].title()}: {next_run['next_time_display']} ET")
        lines.append(f"   Time until: {next_run['time_until']}")

    history = get_execution_history(limit=3)
    if history.get("history"):
        lines.extend(["", f"{get_symbol('INFO')} Recent Executions"])
        for entry in history["history"][:3]:
            status_emoji = {"completed": get_symbol("SUCCESS"), "dry_run": "🧪"}.get(
                entry["status"], get_symbol("ERROR")
            )
            lines.append(f"   {status_emoji} {entry['task_name']}")

    return "\n".join(lines)


# Control tools (Issue #478/#481)


__all__ = [
    "get_scheduler_status",
    "get_execution_history",
    "get_next_scheduled_run",
    "show_scheduler",
]
