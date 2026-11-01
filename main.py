#!/usr/bin/env python3
"""
OptionsCommander Main Runner
Unified entry point for all trading system operations.

Usage:
    python main.py                           # Launch interactive trading assistant
    python main.py --help                    # Show all commands
    python main.py --daemon                  # Run automated scheduler
    python main.py --run-due                 # Run any routine due now, then exit (cron)

In the CLI, set the trading mode with a command:
    > set mode aggressive
    > set risk mode to moderate
    > show mode
"""

import argparse
import asyncio
import os
import sys
import traceback

import yaml

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Import all trading system components at startup for efficiency
from src.trading.accounts.account_manager import get_account_manager
from src.trading.scheduling.daily_scheduler import (
    DailyScheduler,
    configure_scheduler_logging,
    load_scheduler_config,
)
from src.trading.scheduling.watchdog import (
    RunDueTimeoutError,
    run_due_timeout_seconds,
    watchdog,
)
from src.utils.safe_print import get_symbol, safe_print


def _load_cli_help() -> str:
    """Load and format CLI help text from YAML config."""
    config_path = os.path.join(os.path.dirname(__file__), "config_defaults", "cli_help.yaml")

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError):
        return ""  # Fall back to no epilog if config unavailable

    lines = [
        "",
        f"{'=' * 66}",
        f"  {config['title']} v{config['version']}".center(66),
        f"  {config['subtitle']}".center(66),
        f"{'=' * 66}",
        "",
        "QUICK START:",
    ]

    # Quick start commands
    for item in config["quick_start"]:
        lines.append(f"  {item['command']:<36} # {item['description']}")

    # Interactive commands sections
    lines.append("")
    lines.append("INTERACTIVE CLI COMMANDS (type /help for full list):")

    for section in config["interactive_commands"].values():
        lines.append("")
        lines.append(f"  {section['title']}:")
        for cmd in section["commands"]:
            lines.append(f"    > {cmd['example']:<28} # {cmd['description']}")

    # Features
    lines.append("")
    lines.append("FEATURES:")
    for feature in config["features"]:
        lines.append(f"  - {feature}")

    return "\n".join(lines)


# The CLI (src/cli, its tools and the agent framework) loads only on the interactive and
# --account paths: cron runs --run-due every 5 minutes and needs none of it. Set by
# _load_cli(); None until then.
CLI_AVAILABLE = None
CLISession = None
OrchestratorFactory = None
get_mode_manager = None


def _load_cli() -> bool:
    """Import the CLI components (they may not be available in all environments)."""
    global CLI_AVAILABLE, CLISession, OrchestratorFactory, get_mode_manager
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
        import cli as cli_package
        from core import factory, trading_modes

        CLISession = cli_package.CLISession
        OrchestratorFactory = factory.OrchestratorFactory
        get_mode_manager = trading_modes.get_mode_manager
        CLI_AVAILABLE = True
    except ImportError:
        CLI_AVAILABLE = False
    return CLI_AVAILABLE


def list_accounts():
    """
    List all configured trading accounts.

    Issue #401: Multi-account portfolio management.
    """
    safe_print(f"{get_symbol('INFO')} Configured Trading Accounts:")
    print("=" * 60)

    manager = get_account_manager()

    # Discover all accounts (query Alpaca API for details)
    safe_print(f"{get_symbol('GEAR')} Discovering account details...")
    manager.discover_all_accounts()

    accounts = manager.list_accounts()

    if not accounts:
        safe_print(f"{get_symbol('WARNING')} No accounts configured.")
        print("\nTo add accounts, update config/config.json with:")
        print('  "accounts": [')
        print('    {"id": "paper_main", "api_key": "...", "api_secret": "...", "alias": "Paper"}')
        print("  ]")
        return False

    for acc in accounts:
        status_icon = get_symbol("SUCCESS") if acc.get("has_info") else get_symbol("WARNING")
        active_tag = " [ACTIVE]" if acc.get("is_active") else ""
        enabled_tag = "" if acc.get("enabled", True) else " [DISABLED]"

        print(f"\n{status_icon} {acc['id']}{active_tag}{enabled_tag}")

        if acc.get("alias"):
            print(f"   Alias: {acc['alias']}")

        if acc.get("has_info"):
            acc_type = acc.get("account_type", "unknown").upper()
            print(f"   Type: {acc_type}")
            print(f"   Account #: {acc.get('account_number', 'N/A')}")
            print(f"   Portfolio: ${acc.get('portfolio_value', 0):,.2f}")
            print(f"   Buying Power: ${acc.get('buying_power', 0):,.2f}")
            print(f"   Status: {acc.get('status', 'Unknown')}")
        else:
            error = acc.get("last_error", "Discovery pending")
            print(f"   Status: Not discovered ({error})")

    print("\n" + "=" * 60)
    print(f"Total: {len(accounts)} account(s) configured")
    print("\nUsage: python main.py --account <ACCOUNT_ID>")
    return True


def trade_assist(account_id: str = None):
    """Interactive CLI trading assistant.

    Set the trading mode with a command:
        > set mode aggressive
        > set risk mode to conservative

    Args:
        account_id: Optional account ID to use (#401)
    """
    if not _load_cli():
        safe_print(f"{get_symbol('ERROR')} CLI components not available")
        sys.exit(1)

    try:
        # Multi-account setup (#401)
        alpaca_mode = "paper"  # Default
        active_account_info = None

        if account_id:
            manager = get_account_manager()

            # Try to set the requested account as active
            if manager.set_active_account(account_id):
                active_account = manager.get_active_account()
                if active_account and active_account.info:
                    active_account_info = active_account.info
                    alpaca_mode = active_account_info.account_type.value
                    safe_print(
                        f"{get_symbol('SUCCESS')} Using account: {account_id} "
                        f"({alpaca_mode.upper()})"
                    )
            else:
                safe_print(f"{get_symbol('ERROR')} Account '{account_id}' not found or not ready")
                safe_print(f"{get_symbol('INFO')} Use --list-accounts to see available accounts")
                return False

        # Get default mode info for display
        mode_manager = get_mode_manager()
        mode_params = mode_manager.get_parameters()

        safe_print(f"\n{get_symbol('ROCKET')} Starting Trade Assistant (Production Mode)...")
        print(
            f"   - Default Mode: {mode_params.mode.value} (say 'aggressive' or 'conservative' to change)"
        )
        print("   - LLM Parser: gpt-4o-mini")
        print("   - Strategy: RealVoterStrategy (MACD+RSI voting)")

        if active_account_info:
            print(f"   - Account: {active_account_info.alias or account_id} ({alpaca_mode})")
            print(f"   - Portfolio: ${active_account_info.portfolio_value:,.2f}")
        else:
            print(f"   - Execution: AlpacaOrderManager ({alpaca_mode} trading)")
        print()

        # Create orchestrator with real components
        factory = OrchestratorFactory()
        orchestrator = factory.create(
            order_manager=None,  # Auto-create from factory
            use_real_voter=True,  # Use the production voting signal
            use_real_alpaca=True,  # Use real Alpaca OrderManager
            alpaca_mode=alpaca_mode,  # Use detected mode from account
        )

        # Create CLI session
        session = CLISession(orchestrator)

        # Run REPL
        asyncio.run(session.run())

        return True
    except (ImportError, ValueError, RuntimeError) as e:
        safe_print(f"{get_symbol('ERROR')} Error starting trade assistant: {e}")
        traceback.print_exc()
        return False


# =============================================================================
# CLI Command Handlers
# =============================================================================


def _run_interactive_cli(account_id: str = None) -> None:
    """Run interactive trading CLI, optionally with specific account."""
    if account_id:
        safe_print(f"{get_symbol('ROCKET')} Launching Trading Assistant with account: {account_id}")
    else:
        safe_print(f"{get_symbol('ROCKET')} Launching Interactive Trading Assistant...")
        print("   (Use --help to see all options)")
    print()

    try:
        success = trade_assist(account_id=account_id)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        safe_print(f"\n{get_symbol('STOP')} Cancelled by user")
        sys.exit(0)
    except (ImportError, ValueError, RuntimeError) as e:
        safe_print(f"\n{get_symbol('EXPLOSION')} Error: {e}")
        sys.exit(1)


def _note_run_due_error(error: BaseException) -> None:
    """
    Leave a notice that the cron check crashed (see notices.py). Best effort: if even that
    fails, say nothing, so the original error is the one that surfaces.
    """
    try:
        from src.trading.scheduling.notices import record_run_due_error

        record_run_due_error(error)
    except Exception:
        pass  # the caller re-raises the original error


def _run_due_checks(limit_seconds: int) -> int:
    """The cron check itself (see _run_due_mode). Returns the exit code."""
    configure_scheduler_logging()
    scheduler = DailyScheduler()
    scheduler.run_limit_seconds = limit_seconds
    ran = scheduler.run_scheduled_tasks()
    return 1 if any(entry.status == "failed" for entry in ran) else 0


def _run_due_mode() -> int:
    """
    Run any scheduled routine that is due now (for cron). Returns the exit code.

    A wall-clock limit (watchdog.py) ends a check that hangs: a routine cut off is
    recorded as failed with its own notice; a timeout anywhere else leaves a run_due_error
    notice. Either way the exit code is 1.
    """
    try:
        limit = run_due_timeout_seconds(load_scheduler_config())
    except Exception:
        limit = run_due_timeout_seconds()  # unreadable config: the scheduler reports that
    try:
        with watchdog(limit):
            return _run_due_checks(limit)
    except RunDueTimeoutError as timeout:
        if not timeout.routine_recorded:  # a routine's timeout already has its notice
            _note_run_due_error(timeout)
        traceback.print_exc()  # cron's stderr (state/scheduler-cron.err)
        return 1
    except Exception as error:
        _note_run_due_error(error)
        raise  # the traceback still reaches cron's stderr (state/scheduler-cron.err)


def _run_daemon_mode() -> None:
    """Run scheduler in daemon mode."""
    safe_print(f"{get_symbol('ROBOT')} Starting Daily Scheduler Daemon...")
    print("   Press Ctrl+C to stop")
    print()

    try:
        log_path = configure_scheduler_logging()
        print(f"   Logging to {log_path}")
        scheduler = DailyScheduler()
        asyncio.run(scheduler.run_daemon(check_interval_seconds=60))
        sys.exit(0)
    except KeyboardInterrupt:
        safe_print(f"\n{get_symbol('STOP')} Scheduler stopped by user")
        sys.exit(0)
    except (ImportError, ValueError, RuntimeError) as e:
        safe_print(f"\n{get_symbol('EXPLOSION')} Scheduler error: {e}")
        sys.exit(1)


def _create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser for main()."""
    parser = argparse.ArgumentParser(
        description="OptionsCommander - Paper Trading Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_load_cli_help(),
    )

    parser.add_argument(
        "--daemon", action="store_true", help="Run daily scheduler in background (daemon mode)"
    )
    parser.add_argument(
        "--run-due",
        action="store_true",
        help="Run any scheduled routine that is due now, then exit (for cron)",
    )
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        metavar="ACCOUNT_ID",
        help="Select trading account (e.g., --account paper_main)",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="List all configured trading accounts",
    )

    return parser


def main():
    """
    Main entry point - Unified Interactive CLI.

    Default: Launches interactive trading assistant
    --daemon: Runs scheduler in background
    --list-accounts: List configured accounts
    --account: Use specific account
    """
    # No arguments = launch interactive CLI
    if len(sys.argv) == 1:
        _run_interactive_cli()
        return

    parser = _create_argument_parser()
    args = parser.parse_args()

    # Handle each mode
    if args.list_accounts:
        try:
            success = list_accounts()
            sys.exit(0 if success else 1)
        except (ImportError, ValueError, RuntimeError) as e:
            safe_print(f"{get_symbol('ERROR')} Error listing accounts: {e}")
            sys.exit(1)

    if args.run_due:  # before --account, which would start the interactive CLI
        sys.exit(_run_due_mode())

    if args.account and not args.daemon:
        _run_interactive_cli(account_id=args.account)

    if args.daemon:
        _run_daemon_mode()


if __name__ == "__main__":
    main()
