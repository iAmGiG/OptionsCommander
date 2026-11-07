"""
Scheduler CLI - Interactive scheduler management interface.

Provides dedicated commands for:
- Viewing scheduler status with detailed breakdown
- Editing scheduler configuration interactively
- Testing scheduler routines
- Monitoring execution history
- Starting/stopping scheduler service

Messages are loaded from config_defaults/scheduler_cli_messages.yaml

Refactored in Issue #440: Extracted components to src/cli/scheduler/
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from .scheduler import (
    SchedulerConfigEditor,
    SchedulerDaemonManager,
    SchedulerMonitor,
    SchedulerSetupWizard,
    get_emoji,
)
from .scheduler.message_loader import get_messages
from src.trading.scheduling.daily_scheduler import DailyScheduler
from src.trading.scheduling.notices import NoticeStore, is_notice_number
from src.trading.scheduling.notifier import send_test_email

logger = logging.getLogger(__name__)


class SchedulerCLI:
    """
    Interactive CLI for scheduler management.

    Commands are defined in config_defaults/scheduler_cli_messages.yaml.
    To add/remove/disable commands, edit the 'commands' section in that file.

    Components extracted to src/cli/scheduler/:
    - SchedulerMessageLoader: Message loading from YAML
    - SchedulerDaemonManager: Start/stop/status of daemon
    - SchedulerConfigEditor: Config editing with validation
    - SchedulerMonitor: Status/history/logs display
    - SchedulerSetupWizard: First-time setup flow
    """

    def __init__(self, scheduler: Optional[DailyScheduler] = None):
        """
        Initialize scheduler CLI with config-driven command registry.

        Args:
            scheduler: Optional existing DailyScheduler instance
        """
        self.scheduler = scheduler
        self._notice_store: Optional[NoticeStore] = None
        self._running = True

        # Initialize extracted components
        self.config_editor = SchedulerConfigEditor()
        self.daemon_manager = SchedulerDaemonManager()
        self.monitor = SchedulerMonitor(scheduler)
        self.setup_wizard = SchedulerSetupWizard(scheduler)

        # Build command registry from config
        self._command_registry = self._build_command_registry()

    def _build_command_registry(self) -> Dict[str, Dict[str, Any]]:
        """
        Build command lookup table from config.

        Returns:
            Dict mapping command names and aliases to their definitions.
        """
        registry = {}
        commands = get_messages().get("commands", {})

        for cmd_name, cmd_def in commands.items():
            if not cmd_def.get("enabled", True):
                continue

            registry[cmd_name] = {
                "name": cmd_name,
                "handler": cmd_def.get("handler", cmd_name),
                "requires_scheduler": cmd_def.get("requires_scheduler", False),
                "category": cmd_def.get("category", ""),
                "description": cmd_def.get("description", ""),
                "usage": cmd_def.get("usage", ""),
            }

            for alias in cmd_def.get("aliases", []):
                registry[alias] = registry[cmd_name]

        return registry

    @property
    def notice_store(self) -> NoticeStore:
        """The notices: the scheduler's, or state/notices.json before there is a scheduler."""
        if self.scheduler is not None:
            return self.scheduler.notice_store
        if self._notice_store is None:
            self._notice_store = NoticeStore()
        return self._notice_store

    async def run(self):
        """Main scheduler CLI loop."""
        self._print_welcome()

        while self._running:
            try:
                command = input("\nScheduler> ").strip().lower()

                if not command:
                    continue

                if command.startswith("/"):
                    command = command[1:]

                await self._handle_command(command)

            except KeyboardInterrupt:
                print(f"\n\n{get_emoji('wave', '👋')} Exiting scheduler CLI...")
                break
            except Exception as e:
                print(f"\n{get_emoji('cross_red', '❌')} Error: {e}")
                logger.error(f"Scheduler CLI error: {e}", exc_info=True)

    def _print_welcome(self):
        """Print scheduler CLI welcome message with auto-generated command list."""
        messages = get_messages()
        welcome = messages.get("welcome", {})
        categories = messages.get("categories", {})

        banner = welcome.get("banner", "=" * 70)
        print("\n" + banner)
        print(
            f"   {get_emoji('calendar', '📅')} {welcome.get('title', 'Scheduler Management CLI')}"
        )
        print(banner)

        # Group commands by category
        commands_by_category: Dict[str, list] = {}
        for cmd_name, cmd_def in messages.get("commands", {}).items():
            if not cmd_def.get("enabled", True):
                continue
            category = cmd_def.get("category", "other")
            if category not in commands_by_category:
                commands_by_category[category] = []
            commands_by_category[category].append(
                {
                    "name": cmd_name,
                    "description": cmd_def.get("description", ""),
                    "usage": cmd_def.get("usage", ""),
                }
            )

        # Sort and print categories
        sorted_cats = sorted(categories.items(), key=lambda x: x[1].get("order", 99))

        for cat_key, cat_def in sorted_cats:
            cmds = commands_by_category.get(cat_key, [])
            if not cmds:
                continue

            emoji_name = cat_def.get("emoji", "")
            emoji = get_emoji(emoji_name, "") if emoji_name else ""
            header = cat_def.get("header", cat_key.title())
            print(f"\n{emoji} {header}")

            for cmd in cmds:
                display_name = cmd.get("usage") or cmd["name"]
                print(f"  {display_name:16s} - {cmd['description']}")

        print("")

    async def _handle_command(self, command: str):
        """Handle scheduler CLI commands using config-driven routing."""
        parts = command.split()
        cmd_name = parts[0] if parts else ""
        args = parts[1:] if len(parts) > 1 else []

        cmd_def = self._command_registry.get(cmd_name)

        if not cmd_def:
            print(f"{get_emoji('cross_red', '❌')} Unknown command: {command}")
            print("Type 'help' for available commands")
            return

        # Initialize scheduler if required
        if cmd_def.get("requires_scheduler") and self.scheduler is None:
            try:
                self.scheduler = DailyScheduler()
                self.monitor.scheduler = self.scheduler
                self.setup_wizard.scheduler = self.scheduler
            except Exception as e:
                print(f"{get_emoji('cross_red', '❌')} Failed to initialize scheduler: {e}")
                print(f"{get_emoji('light', '💡')} Try running: python main.py --daemon")
                return

        # Get and call handler
        handler_name = cmd_def.get("handler", cmd_name)
        handler = getattr(self, f"_{handler_name}", None)

        if handler is None or not callable(handler):
            print(f"{get_emoji('cross_red', '❌')} Handler not implemented: {handler_name}")
            return

        # handler is guaranteed callable at this point
        try:
            if asyncio.iscoroutinefunction(handler):  # type: ignore
                await handler(*args)  # type: ignore
            else:
                handler(*args)  # type: ignore
        except TypeError:
            usage = cmd_def.get("usage", "")
            if usage:
                print(f"Usage: {usage}")
            else:
                raise

    # =========================================================================
    # Command Handlers - Delegate to extracted components
    # =========================================================================

    def _show_status(self):
        """Show scheduler status."""
        self.monitor.show_status()

    def _show_config(self):
        """Show configuration file."""
        self.monitor.show_config(self.config_editor.config_file)

    def _show_config_info(self):
        """Show configuration info/guide."""
        self.monitor.show_config_info()

    async def _edit_config(self):
        """Interactive config editor."""
        await self.config_editor.edit_interactive(self.scheduler)

    def _set_enabled_true(self):
        """Enable scheduler."""
        self.config_editor.set_enabled(True, self.scheduler)

    def _set_enabled_false(self):
        """Disable scheduler."""
        self.config_editor.set_enabled(False, self.scheduler)

    async def _test_routine(self, routine_type: str = "", mode: str = ""):
        """
        Run a routine now. With 'dry' (or the dry_run setting on) it sends no orders and
        changes no saved state; otherwise it acts on the broker (#601).
        """
        if routine_type not in ["morning", "evening"] or mode not in ("", "dry"):
            print("Usage: test [morning|evening] [dry]")
            return

        if not self.scheduler:
            print(f"{get_emoji('cross_red', '❌')} Scheduler not initialized")
            return

        dry_run = mode == "dry" or self.scheduler.dry_run
        label = "dry run: no orders are sent" if dry_run else "acts on the broker"
        print(f"\n{get_emoji('test', '🧪')} Testing {routine_type} routine ({label})...")

        try:
            # The scheduler's lock: a test run never overlaps the daemon, cron or a retry
            with self.scheduler.run_lock() as acquired:
                if not acquired:
                    print(
                        f"{get_emoji('warning', '⚠️')} A scheduled run is in progress; "
                        "try again in a few minutes"
                    )
                    return
                if routine_type == "morning":
                    report = self.scheduler.trading_cycle.morning_routine(dry_run=dry_run)
                else:
                    report = self.scheduler.trading_cycle.evening_routine(dry_run=dry_run)

            print(
                f"\n{get_emoji('check_green', '✅')} {routine_type.capitalize()} routine completed"
            )
            print("\nReport preview:")
            print("-" * 70)
            preview = report[:500] + "..." if len(report) > 500 else report
            print(preview)
            print("-" * 70)
            print("\nFull report saved to: reports/daily/")
        except Exception as e:
            print(f"{get_emoji('cross_red', '❌')} Test failed: {e}")
            logger.error(f"Routine test error: {e}", exc_info=True)

    def _show_notices(self, which: str = ""):
        """List the open notices (failures and warnings from the loop); 'all' adds the acknowledged."""
        if which not in ("", "all"):
            print("Usage: notices [all]")
            return
        try:
            notices = self.notice_store.all_notices() if which else self.notice_store.open_notices()
        except Exception as e:
            print(f"{get_emoji('warning', '⚠️')} Notices unavailable: {e}")
            return
        print(f"\n{get_emoji('warning', '⚠️')} Notices" + (" (all)" if which else " (open)"))
        print("=" * 70)
        if not notices:
            print(f"{get_emoji('check_green', '✅')} None")
            return
        for notice in notices:
            print(f"\n{notice.line()}" + (" [acknowledged]" if notice.acked_at else ""))
            if notice.detail:
                print(f"     {notice.detail}")
            if notice.report_path:
                print(f"     Report: {notice.report_path}")
        print("\n'ack <number>' or 'ack all' acknowledges; a condition that returns reopens it")

    def _ack_notice(self, target: str):
        """Acknowledge a notice by its number, or every open one with 'all'."""
        if target != "all" and not is_notice_number(target):
            print("Usage: ack <number|all>")
            return
        count = self.notice_store.ack(target)
        if count:
            print(f"{get_emoji('check_green', '✅')} Acknowledged {count} notice(s)")
        else:
            print(f"{get_emoji('info', 'ℹ️')} No open notice to acknowledge for '{target}'")

    def _notify_test(self, which: str):
        """Send one test email, to check the settings ('notify test')."""
        if which != "test":
            raise TypeError("notify takes 'test'")  # _handle_command prints the usage
        sent, message = send_test_email()
        icon = get_emoji("check_green", "✅") if sent else get_emoji("cross_red", "❌")
        print(f"{icon} {message}")

    def _show_history(self):
        """Show execution history."""
        self.monitor.show_history()

    def _show_next_run(self):
        """Show next scheduled run."""
        self.monitor.show_next_run()

    async def _run_setup_wizard(self):
        """Run first-time setup wizard."""
        await self.setup_wizard.run()

    def _start_daemon(self):
        """Start scheduler daemon."""
        self.daemon_manager.start()

    def _stop_daemon(self):
        """Stop scheduler daemon."""
        self.daemon_manager.stop()

    def _show_logs(self, lines: str = "50"):
        """Show scheduler logs."""
        try:
            num_lines = int(lines)
        except ValueError:
            num_lines = 50
        self.monitor.show_logs(num_lines)

    def _exit_cli(self):
        """Exit the scheduler CLI."""
        print(f"\n{get_emoji('wave', '👋')} Exiting scheduler CLI...")
        self._running = False


async def main():
    """Main entry point for standalone scheduler CLI."""
    cli = SchedulerCLI()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(main())
