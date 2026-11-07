"""
Scheduler monitor - status, history, and log displays.

Provides monitoring views for scheduler status and execution history.
Extracted from scheduler_cli.py (Issue #440).
"""

import logging
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .daemon_manager import SchedulerDaemonManager
from .message_loader import get_emoji, get_messages
from src.trading.scheduling.notices import NoticeStore
from src.trading.scheduling.notifier import email_settings, heartbeat_url
from src.utils.state_paths import get_state_dir

if TYPE_CHECKING:
    from src.trading.scheduling.daily_scheduler import DailyScheduler

logger = logging.getLogger(__name__)

# Notices listed in the status (the latest ones; 'notices' lists them all)
STATUS_NOTICES = 5


class SchedulerMonitor:
    """
    Scheduler monitoring and status display.

    Provides:
    - Status overview
    - Configuration info
    - Execution history
    - Log viewing
    - Next run calculation
    """

    def __init__(self, scheduler: Optional["DailyScheduler"] = None):
        """
        Initialize monitor.

        Args:
            scheduler: Optional scheduler instance for status queries
        """
        self.scheduler = scheduler
        self.daemon_manager = SchedulerDaemonManager()

    def show_status(self) -> None:
        """Show detailed scheduler status."""
        print(f"\n{get_emoji('clock', '⏰')} Scheduler Status")
        print("=" * 70)

        if not self.scheduler:
            print(f"{get_emoji('cross_red', '❌')} Scheduler not initialized")
            return

        # Check daemon status (or cron, which runs `main.py --run-due` without a daemon)
        daemon_running = self.daemon_manager.is_running()
        cron_line = self.daemon_manager.cron_entry()
        cron_running = cron_line is not None
        enabled = self.scheduler.effective_settings()["enabled"]

        # Config status
        config_emoji = get_emoji("green_circle", "🟢") if enabled else get_emoji("red_circle", "🔴")
        print(f"\n{config_emoji} Config: {'ENABLED' if enabled else 'DISABLED'}")

        # Daemon/service status
        if daemon_running:
            print(f"{get_emoji('check_green', '✅')} Service: RUNNING (automatic execution active)")
            if cron_running:
                print(
                    f"{get_emoji('warning', '⚠️')} A cron entry runs it too; keep only one of them."
                )
        elif cron_running:
            schedule = " ".join(cron_line.split()[:5])
            print(
                f"{get_emoji('check_green', '✅')} Service: CRON entry installed "
                f"({schedule}: main.py --run-due runs routines when due)"
            )
            if not enabled:
                print("   The scheduler is disabled, so cron runs no routines until 'enable'.")
        else:
            print(f"{get_emoji('cross_red', '❌')} Service: NOT RUNNING (no automatic execution)")
            if enabled:
                print(f"\n{get_emoji('light', '💡')} Config is enabled but nothing runs it.")
                print("   To start automatic execution, exit this CLI and either:")
                print("   - add the cron entry: ./scripts/deployment/setup_cron.sh (recommended)")
                print("   - or run the daemon: python main.py --daemon")
                print(
                    "\n   Or use 'test morning/evening' to run now (acts on the broker), "
                    "or 'test morning dry' to send no orders"
                )

        # Configuration details
        print(f"\n{get_emoji('gear', '⚙️')} Configuration:")
        print(f"   Morning: {self.scheduler.config.get('morning_routine_time', 'N/A')} ET")
        print(f"   Evening: {self.scheduler.config.get('evening_routine_time', 'N/A')} ET")
        print(f"   Timezone: {self.scheduler.config.get('market_timezone', 'N/A')}")
        print(f"   Max Retries: {self.scheduler.config.get('max_retries', 3)}")
        dry_run = bool(self.scheduler.config.get("dry_run", False))
        note = " (no orders are sent: stops are not placed, renewed or raised)" if dry_run else ""
        print(f"   Dry Run: {dry_run}{note}")
        self._show_notifications()

        # Recent executions
        recent = self.scheduler.get_execution_history(days=1)
        if recent:
            print(f"\n{get_emoji('clipboard', '📋')} Today's Executions: {len(recent)}")
            for entry in recent[:5]:
                status = (
                    get_emoji("check_green", "✅")
                    if entry.status == "completed"
                    else "🧪"
                    if entry.status == "dry_run"
                    else get_emoji("cross_red", "❌")
                )
                print(f"   {status} {entry.task_name} - {entry.status}")
        else:
            print(f"\n{get_emoji('clipboard', '📋')} No executions today")
            if enabled and not (daemon_running or cron_running):
                print("   (Neither cron nor the daemon is running - no automatic executions)")

        self._show_open_notices()

    def _show_notifications(self) -> None:
        """Whether email and the heartbeat are set up (key names only, never values)."""
        settings, missing = email_settings()
        if settings is not None:
            print(f"   Email: configured ({len(settings['to_addrs'])} recipients)")
        else:
            print(f"   Email: off (missing: {', '.join(missing)})")
        print(f"   Heartbeat: {'set' if heartbeat_url() else 'not set'}")

    def _show_open_notices(self) -> None:
        """The notices nobody has acknowledged (the latest few), or that there are none."""
        try:
            store = getattr(self.scheduler, "notice_store", None) or NoticeStore()
            notices = list(store.open_notices())
            lines = [notice.line() for notice in notices[-STATUS_NOTICES:]]
        except Exception as e:
            print(f"\n{get_emoji('warning', '⚠️')} Open notices unavailable: {e}")
            return
        if not notices:
            print(f"\n{get_emoji('check_green', '✅')} No open notices")
            return
        print(f"\n{get_emoji('warning', '⚠️')} Open notices: {len(notices)}")
        for line in lines:
            print(f"   {line}")
        if len(notices) > STATUS_NOTICES:
            print(f"   ... and {len(notices) - STATUS_NOTICES} older ones")
        print("   'notices' lists them with details; 'ack <number|all>' acknowledges")

    def show_config(self, config_file: str = None) -> None:
        """
        Display current scheduler configuration file.

        Args:
            config_file: Path to config file
        """
        if config_file is None:
            config_file = "config_defaults/scheduler_config.yaml"

        print(f"\n{get_emoji('gear', '⚙️')} Scheduler Configuration")
        print("=" * 70)

        config_path = Path(config_file)
        if config_path.exists():
            print(f"\nConfig file: {config_file}\n")
            print(config_path.read_text())
        else:
            print(f"{get_emoji('cross_red', '❌')} Config file not found: {config_file}")
            print(f"{get_emoji('light', '💡')} Will be created with defaults on first run")

    def show_config_info(self) -> None:  # noqa: C901
        """Display detailed explanations for configuration settings."""
        info = get_messages().get("config_info", {})
        settings = info.get("settings", {})

        # Header
        print(f"\n{get_emoji('book', '📖')} {info.get('header', 'Scheduler Configuration Guide')}")
        print("=" * 70)

        # Editable settings
        print(f"\n{get_emoji('wrench', '🔧')} {info.get('editable_header', 'EDITABLE SETTINGS')}")
        print("-" * 70)

        setting_order = ["enabled", "morning_time", "evening_time", "max_retries", "dry_run"]
        config_keys = {
            "enabled": "enabled",
            "morning_time": "morning_routine_time",
            "evening_time": "evening_routine_time",
            "max_retries": "max_retries",
            "dry_run": "dry_run",
        }

        for i, setting_key in enumerate(setting_order, 1):
            setting = settings.get(setting_key, {})
            if not setting:
                continue

            config_key = config_keys.get(setting_key, setting_key)
            current = self.scheduler.config.get(config_key, "N/A") if self.scheduler else "N/A"

            print(f"\n{i}. {setting.get('name', setting_key)}")
            print(f"   Current: {current}")
            print(f"   Purpose: {setting.get('purpose', '')}")

            if "default" in setting:
                print(f"   Default: {setting['default']}")

            for val in setting.get("values", []):
                print(f"   Values:  {val}")

            if "warning" in setting:
                print(f"   {get_emoji('warning', '⚠️')} NOTE: {setting['warning']}")

            if "status" in setting:
                print(f"   Status:  {setting['status']}")

            if "tip" in setting:
                print(f"   Tip:     {setting['tip']}")

        # Read-only settings
        print(
            f"\n\n{get_emoji('clipboard', '📋')} {info.get('readonly_header', 'READ-ONLY SETTINGS')}"
        )
        print("-" * 70)

        for setting in info.get("readonly_settings", []):
            print(f"\n• {setting.get('name', '')}: {setting.get('value', '')}")
            print(f"  {setting.get('description', '')}")

        # Quick reference
        qref = info.get("quick_reference", {})
        print(f"\n\n{get_emoji('light', '💡')} {qref.get('header', 'QUICK REFERENCE')}")
        print("-" * 70)

        paper_dry = qref.get("paper_vs_dry", {})
        if paper_dry:
            print(f"\n{paper_dry.get('title', '')}:")
            for item in paper_dry.get("items", []):
                print(f"  • {item}")

        safety = qref.get("safety_levels", {})
        if safety:
            print(f"\n{safety.get('title', '')}:")
            for item in safety.get("items", []):
                print(f"  {item}")

        issues = qref.get("related_issues", {})
        if issues:
            print(f"\n{issues.get('title', '')}:")
            for item in issues.get("items", []):
                print(f"  • {item}")

        print("\n" + "=" * 70)
        print(info.get("footer", "Type 'edit' to modify settings or 'config' to view raw file"))
        print("")

    def show_history(self, days: int = 7, limit: int = 20) -> None:
        """
        Show execution history.

        Args:
            days: Number of days to look back
            limit: Maximum entries to show
        """
        if not self.scheduler:
            print(f"{get_emoji('cross_red', '❌')} Scheduler not initialized")
            return

        print(f"\n{get_emoji('scroll', '📜')} Execution History")
        print("=" * 70)

        history = self.scheduler.get_execution_history(days=days)

        if not history:
            print("No execution history found")
            return

        print(f"\nShowing last {limit} executions ({days} days):\n")

        for entry in history[:limit]:
            status_emoji = (
                get_emoji("check_green", "✅")
                if entry.status == "completed"
                else "🧪"
                if entry.status == "dry_run"
                else get_emoji("cross_red", "❌")
            )
            time_str = (
                entry.actual_end_time.strftime("%Y-%m-%d %H:%M")
                if entry.actual_end_time
                else "In Progress"
            )

            print(f"{status_emoji} {entry.task_name:20s} {entry.status:12s} {time_str}")
            if entry.error_message:
                print(f"   {get_emoji('warning', '⚠️')} {entry.error_message[:60]}...")

    def show_next_run(self) -> None:
        """Calculate and show next scheduled run."""
        if not self.scheduler:
            print(f"{get_emoji('cross_red', '❌')} Scheduler not initialized")
            return

        print(f"\n{get_emoji('crystal_ball', '🔮')} Next Scheduled Run")
        print("=" * 70)

        try:
            # Trading days only, per-routine flags honoured (DailyScheduler.next_run)
            now = self.scheduler._market_now()
            upcoming = self.scheduler.next_run(now)
            if upcoming is None:
                print(
                    f"\n{get_emoji('info', 'ℹ️')} Nothing scheduled: scheduler or routines disabled"
                )
                return
            task, next_run = upcoming
            next_task = task.name.replace("_", " ").title()

            time_until = max(next_run - now, timedelta(0))
            hours = int(time_until.total_seconds() // 3600)
            minutes = int((time_until.total_seconds() % 3600) // 60)

            print(f"\n{get_emoji('clock', '⏰')} {next_task}")
            print(f"   Time: {next_run.strftime('%H:%M %p')} ET")
            print(f"   Countdown: {hours}h {minutes}m")
            print(f"   Date: {next_run.strftime('%Y-%m-%d')}")

        except Exception as e:
            print(f"{get_emoji('cross_red', '❌')} Error calculating next run: {e}")

    def show_logs(self, lines: int = 50) -> None:
        """
        Show recent scheduler logs.

        Args:
            lines: Number of log lines to show
        """
        print(f"\n{get_emoji('scroll', '📜')} Recent Scheduler Logs")
        print("=" * 70)

        log_paths = [
            get_state_dir() / "scheduler.log",
            Path("logs/scheduler.log"),
            Path("logs/autotrader.log"),
            Path("state/scheduler_history.json"),
        ]

        log_file = None
        for path in log_paths:
            if path.exists():
                log_file = path
                break

        if not log_file:
            print(f"{get_emoji('info', 'ℹ️')} No log files found")
            print("   Logs are created when the scheduler runs")
            print("   Try 'test morning' or 'start' first")
            return

        print(f"\nShowing last {lines} lines from: {log_file}\n")
        print("-" * 70)

        try:
            with open(log_file, "r") as f:
                all_lines = f.readlines()
                recent = all_lines[-lines:] if len(all_lines) > lines else all_lines

                for line in recent:
                    line_lower = line.lower()
                    if "error" in line_lower:
                        print(f"{get_emoji('cross_red', '❌')} {line.rstrip()}")
                    elif "warning" in line_lower or "warn" in line_lower:
                        print(f"{get_emoji('warning', '⚠️')} {line.rstrip()}")
                    elif "success" in line_lower or "completed" in line_lower:
                        print(f"{get_emoji('check_green', '✅')} {line.rstrip()}")
                    else:
                        print(f"   {line.rstrip()}")

        except Exception as e:
            print(f"{get_emoji('cross_red', '❌')} Error reading logs: {e}")

        print("-" * 70)
        print(f"\n{get_emoji('light', '💡')} Full logs at: {log_file}")
