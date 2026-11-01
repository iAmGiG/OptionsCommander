"""
Slash Command Handlers - CLI commands starting with /.

Issue #468: Self-registering commands using CommandRegistry.

All slash commands are registered here and auto-discovered
when this module is imported.
"""

import logging

from src.cli.commands.registry import command
from src.cli.utils.ui_utils import clear_screen
from src.utils.safe_print import safe_print

logger = logging.getLogger(__name__)


# =============================================================================
# EXIT COMMANDS
# =============================================================================


@command("/exit", aliases=["/quit"], help_text="Exit the CLI")
def cmd_exit(session) -> bool:
    """Exit the CLI session."""
    return False  # Signal to exit


# =============================================================================
# HELP COMMAND
# =============================================================================


@command("/help", help_text="Show help", has_args=True)
def cmd_help(session, args: str = None):
    """Show help information."""
    if args:
        # Delegate to help system for topic-specific help
        help_output = session.help_system.handle_help_command(f"/help {args}")
    else:
        help_output = session.help_system.handle_help_command("/help")
    safe_print(help_output)


# =============================================================================
# MODE TOGGLE
# =============================================================================


@command("/toggle", help_text="Switch CONFIRM/AUTO mode")
def cmd_toggle(session):
    """Toggle between confirm and auto execution modes."""
    from config_defaults.cli_messages import CLIMessages as MSG  # noqa: N814

    if session.autonomy_mode == "confirm":
        session.autonomy_mode = "auto"
        print(MSG.MODE_SWITCHED_AUTO)
    else:
        session.autonomy_mode = "confirm"
        print(MSG.MODE_SWITCHED_CONFIRM)


# =============================================================================
# SCHEDULER
# =============================================================================


@command("/schedule", help_text="Scheduler management")
async def cmd_schedule(session):
    """Enter scheduler management mode."""
    from src.cli.scheduler_cli import SchedulerCLI

    scheduler_cli = SchedulerCLI(session.scheduler)
    await scheduler_cli.run()


# =============================================================================
# FAQ (replaces /tips)
# =============================================================================


@command("/faq", aliases=["/guide"], help_text="Features & how-to guide", has_args=True)
def cmd_faq(session, args: str = None):
    """Show FAQ with optional section filter."""
    from src.cli.utils.faq import display_faq, get_available_sections

    section = args.strip().lower() if args else None

    if section and section not in get_available_sections():
        safe_print(f"Unknown section: {section}")
        safe_print(f"Available: {', '.join(get_available_sections())}")
        return

    display_faq(section)


@command("/tips", help_text="(moved to /faq)")
def cmd_tips_legacy(session):
    """Legacy /tips - redirect to /faq."""
    safe_print("Tip: /tips has moved to /faq")
    from src.cli.utils.faq import display_faq

    display_faq()


# =============================================================================
# ABOUT / STATUS
# =============================================================================


@command("/about", aliases=["/status"], help_text="Status dashboard")
def cmd_about(session):
    """Show status dashboard."""
    from src.cli.utils.about_page import display_about

    display_about(session.account_monitor)


# =============================================================================
# CLEAR / HOME
# =============================================================================


@command("/clear", aliases=["/home", "/cls"], help_text="Clear screen, show home")
def cmd_clear(session):
    """Clear screen and show welcome/home page."""
    clear_screen()
    # Show welcome page
    session._print_welcome()


# =============================================================================
# MODE - Trading risk mode
# =============================================================================


@command("/mode", help_text="Show/set trading mode", has_args=True)
def cmd_mode(session, args: str = None):
    """Show or set trading risk mode (conservative/moderate/aggressive)."""
    from src.cli.tools.mode_tools import set_mode, show_current_mode

    if args:
        # Set mode
        mode_arg = args.strip().lower()
        result = set_mode(mode_arg)
        safe_print(result)
    else:
        # Show current mode
        result = show_current_mode()
        safe_print(result)


# =============================================================================
# TIMEFRAME - Multi-timeframe management (#489)
# =============================================================================


@command("/timeframe", aliases=["/tf"], help_text="Timeframe management", has_args=True)
def cmd_timeframe(session, args: str = None):  # noqa: C901
    """
    Manage trading timeframes.

    Usage:
        /timeframe              Show current timeframe mode
        /tf mode                Show current mode (single/multi)
        /tf list                List available timeframes
        /tf presets             List multi-TF presets
        /tf preset NAME         Apply a multi-TF preset
        /tf single TIMEFRAME    Set single timeframe mode
        /tf validate VALUE      Validate custom timeframe notation
    """
    from src.cli.tools.timeframe_tools import (
        list_multi_timeframe_presets,
        list_timeframes,
        set_multi_timeframe_preset,
        set_single_timeframe,
        show_timeframe_mode,
        validate_custom_timeframe,
    )

    if not args:
        safe_print(show_timeframe_mode())
        return

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower()
    subarg = parts[1] if len(parts) > 1 else None

    if subcommand in ("mode", "info", "current"):
        safe_print(show_timeframe_mode())
    elif subcommand == "list":
        safe_print(list_timeframes())
    elif subcommand == "presets":
        safe_print(list_multi_timeframe_presets())
    elif subcommand == "preset":
        if not subarg:
            safe_print("Usage: /tf preset <name>")
            safe_print(list_multi_timeframe_presets())
        else:
            safe_print(set_multi_timeframe_preset(subarg))
    elif subcommand == "single":
        if not subarg:
            safe_print("Usage: /tf single <timeframe>")
            safe_print(list_timeframes())
        else:
            safe_print(set_single_timeframe(subarg))
    elif subcommand == "validate":
        if not subarg:
            safe_print("Usage: /tf validate <value>  (e.g., 65m, 1.5h, 2d)")
        else:
            safe_print(validate_custom_timeframe(subarg))
    else:
        safe_print(f"Unknown subcommand: {subcommand}")
        safe_print("Use /timeframe for help")


# =============================================================================
# BACKUP - Database backup & restore (#490)
# =============================================================================


@command("/backup", help_text="Database backup & restore", has_args=True)
def cmd_backup(session, args: str = None):
    """
    Manage database backups.

    Usage:
        /backup                 Show backup status
        /backup list            List available backups
        /backup create          Create a new backup
        /backup restore NAME    Restore from backup
        /backup export TABLE    Export table to JSON
        /backup status          Show backup system status
    """
    from src.cli.tools.backup_tools import (
        backup_database,
        export_table,
        list_backups,
        restore_backup,
        show_backup_info,
    )

    if not args:
        safe_print(show_backup_info())
        return

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower()
    subarg = parts[1] if len(parts) > 1 else None

    if subcommand == "list":
        safe_print(list_backups())
    elif subcommand in ("create", "database"):
        safe_print(backup_database())
    elif subcommand == "restore":
        if not subarg:
            safe_print("Usage: /backup restore <name>")
            safe_print(list_backups())
        else:
            safe_print(restore_backup(subarg))
    elif subcommand == "export":
        if not subarg:
            safe_print("Usage: /backup export <table_name>")
        else:
            safe_print(export_table(subarg))
    elif subcommand == "status":
        safe_print(show_backup_info())
    else:
        safe_print(f"Unknown subcommand: {subcommand}")
        safe_print("Use /backup for help")


# =============================================================================
# VOTER - Ranked voting management (#488)
# =============================================================================


def _voter_handle_subcommand(vc, subcommand: str, subarg: str | None) -> str:
    """Handle voter subcommands. Returns output string."""
    if subcommand in ("list", "ls"):
        return vc.list_voters(verbose=bool(subarg))
    if subcommand == "info":
        return vc.show_info()
    if subcommand == "presets":
        return vc.list_presets()
    if subcommand == "preset":
        if not subarg:
            return f"Usage: /voter preset <name>\n{vc.list_presets()}"
        return vc.apply_preset(subarg)
    if subcommand == "promote":
        return vc.promote_voter(subarg) if subarg else "Usage: /voter promote <name>"
    if subcommand == "demote":
        return vc.demote_voter(subarg) if subarg else "Usage: /voter demote <name>"
    if subcommand in ("role", "set-role"):
        if not subarg or " " not in subarg:
            return "Usage: /voter role <name> <active|review>"
        name, role = subarg.split(maxsplit=1)
        return vc.set_voter_role(name, role)
    return f"Unknown subcommand: {subcommand}\nUse /voter for help"


@command("/voter", help_text="Ranked voter management", has_args=True)
def cmd_voter(session, args: str = None):
    """
    Manage ranked voters for trading decisions.

    Usage:
        /voter              Show voter rankings
        /voter list         Show active/review voters
        /voter info         Show voting configuration
        /voter presets      List available presets
        /voter preset NAME  Apply a preset (default, macd_primary, rsi_primary)
        /voter promote NAME Promote voter one rank
        /voter demote NAME  Demote voter one rank
    """
    from src.cli.commands.voter_commands import get_voter_commands

    vc = get_voter_commands()

    if not args:
        safe_print(vc.list_voters())
        return

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower()
    subarg = parts[1] if len(parts) > 1 else None

    safe_print(_voter_handle_subcommand(vc, subcommand, subarg))


# =============================================================================
# GTT - Good-Till-Triggered order management (#506)
# =============================================================================


def _gtt_try_trigger_action(action_func, trigger_id_str: str, action_name: str) -> str:
    """Execute GTT trigger action. Returns result string."""
    try:
        trigger_id = int(trigger_id_str)
        result = action_func(trigger_id)
        return f"[OK] {result.get('message', f'Trigger {action_name}')}"
    except ValueError:
        return f"[ERROR] Invalid trigger ID: {trigger_id_str}"


def _gtt_show_trigger(trigger_id_str: str) -> str:
    """Show one GTT trigger's details. Returns result string."""
    from src.cli.tools.gtt_tools import show_gtt_trigger

    try:
        trigger_id = int(trigger_id_str)
    except ValueError:
        return f"[ERROR] Invalid trigger ID: {trigger_id_str}"
    return show_gtt_trigger(trigger_id)


def _gtt_create_trigger(args_str: str) -> str:
    """Parse and create a GTT trigger. Format: SYMBOL CONDITION VALUE [notes]"""
    from src.cli.tools.gtt_tools import create_gtt_trigger

    if not args_str:
        return (
            "Usage: /gtt create <symbol> <condition> <value> [notes]\n\n"
            "Conditions: above, below, gain, loss, trailing\n"
            "Examples:\n"
            "  /gtt create AAPL above 200      # Alert when AAPL hits $200\n"
            "  /gtt create SPY below 450       # Alert when SPY drops to $450\n"
            "  /gtt create TSLA gain 10        # Alert on a 10% gain from now\n"
            "  /gtt create NVDA trailing 5     # Alert 5% below its high\n\n"
            "Checked at the morning and evening routines, for symbols you hold."
        )

    parts = args_str.split(maxsplit=3)
    if len(parts) < 3:
        return "Usage: /gtt create <symbol> <condition> <value> [notes]"

    symbol = parts[0].upper()
    condition = parts[1].lower()
    try:
        value = float(parts[2])
    except ValueError:
        return f"[ERROR] Invalid value: {parts[2]} (must be a number)"

    notes = parts[3] if len(parts) > 3 else None

    result = create_gtt_trigger(
        symbol=symbol,
        condition=condition,
        value=value,
        notes=notes,
    )

    if result.get("status") == "success":
        return f"[OK] {result.get('message', 'GTT trigger created')}"
    return f"[ERROR] {result.get('message', 'Failed to create trigger')}"


def _gtt_handle_subcommand(subcommand: str, subarg: str | None) -> str:
    """Handle GTT subcommands. Returns output string."""
    from src.cli.tools.gtt_tools import (
        delete_gtt_trigger,
        disable_gtt_trigger,
        enable_gtt_trigger,
        show_gtt_summary,
        show_gtt_triggers,
    )

    if subcommand in ("list", "ls"):
        return show_gtt_triggers()
    if subcommand == "summary":
        return show_gtt_summary()
    if subcommand == "status":
        return (
            f"Usage: /gtt status <id>\n{show_gtt_summary()}"
            if not subarg
            else _gtt_show_trigger(subarg)
        )
    if subcommand == "create":
        return _gtt_create_trigger(subarg)
    if subcommand in ("delete", "cancel"):
        return (
            "Usage: /gtt delete <trigger_id>"
            if not subarg
            else _gtt_try_trigger_action(delete_gtt_trigger, subarg, "deleted")
        )
    if subcommand == "enable":
        return (
            "Usage: /gtt enable <trigger_id>"
            if not subarg
            else _gtt_try_trigger_action(enable_gtt_trigger, subarg, "enabled")
        )
    if subcommand == "disable":
        return (
            "Usage: /gtt disable <trigger_id>"
            if not subarg
            else _gtt_try_trigger_action(disable_gtt_trigger, subarg, "disabled")
        )
    return f"Unknown subcommand: {subcommand}\nUse /gtt for help"


@command("/records", help_text="Daily equity, fills and realized P&L", has_args=True)
def cmd_records(session, args: str = None):
    """
    Show the daily records (read-only, no broker calls).

    Usage:
        /records         # The last 10 recorded days
        /records 30      # The last 30 recorded days
        /records all     # Everything recorded
    """
    from src.cli.commands.records_commands import records_command

    safe_print(records_command(args))


@command("/gtt", help_text="GTT order management", has_args=True)
def cmd_gtt(session, args: str = None):
    """
    Manage Good-Till-Triggered persistent orders.

    Usage:
        /gtt                              # Show active GTT triggers
        /gtt list                         # List all triggers
        /gtt create SYM COND VAL [notes]  # Create trigger (above/below/gain/loss/trailing)
        /gtt summary                      # Show GTT summary
        /gtt status <id>                  # Show trigger details
        /gtt delete <id>                  # Delete a trigger
        /gtt cancel <id>                  # Cancel a trigger (alias for delete)
        /gtt enable <id>                  # Enable trigger
        /gtt disable <id>                 # Disable trigger
    """
    from src.cli.tools.gtt_tools import show_gtt_triggers

    if not args:
        safe_print(show_gtt_triggers())
        return

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower()
    subarg = parts[1] if len(parts) > 1 else None

    safe_print(_gtt_handle_subcommand(subcommand, subarg))


# =============================================================================
# WATCHLIST - Tiered watchlist management (#507)
# =============================================================================

# Default watchlist name for simple add/remove operations
_WL_DEFAULT = "default"


def _watchlist_add(subarg: str | None) -> str:
    """Handle /watchlist add subcommand."""
    from src.cli.tools.watchlist_tools import add_to_watchlist, create_watchlist

    if not subarg:
        return "Usage: /watchlist add <symbol> [watchlist_name]"
    parts = subarg.split()
    symbol = parts[0].upper()
    wl_name = parts[1] if len(parts) > 1 else _WL_DEFAULT
    if wl_name == _WL_DEFAULT:
        create_watchlist(name=_WL_DEFAULT, description="Default watchlist", is_default=True)
    result = add_to_watchlist(watchlist_name=wl_name, symbol=symbol)
    if result.get("status") == "success":
        return f"[OK] Added {symbol} to '{wl_name}'"
    return f"[ERROR] {result.get('message', 'Failed to add symbol')}"


def _watchlist_remove(subarg: str | None) -> str:
    """Handle /watchlist remove subcommand."""
    from src.cli.tools.watchlist_tools import remove_from_watchlist

    if not subarg:
        return "Usage: /watchlist remove <symbol> [watchlist_name]"
    parts = subarg.split()
    symbol = parts[0].upper()
    wl_name = parts[1] if len(parts) > 1 else _WL_DEFAULT
    result = remove_from_watchlist(watchlist_name=wl_name, symbol=symbol)
    if result.get("status") == "success":
        return f"[OK] Removed {symbol} from '{wl_name}'"
    return f"[ERROR] {result.get('message', 'Failed to remove symbol')}"


def _watchlist_create(subarg: str | None) -> str:
    """Handle /watchlist create subcommand."""
    from src.cli.tools.watchlist_tools import create_watchlist

    if not subarg:
        return "Usage: /watchlist create <name> [description]"
    parts = subarg.split(maxsplit=1)
    wl_name = parts[0]
    desc = parts[1] if len(parts) > 1 else None
    result = create_watchlist(name=wl_name, description=desc)
    if result.get("status") == "success":
        return f"[OK] Created watchlist '{wl_name}'"
    return f"[ERROR] {result.get('message', 'Failed to create watchlist')}"


def _watchlist_limits() -> str:
    """Handle /watchlist limits subcommand."""
    from src.cli.tools.watchlist_tools import list_watchlists

    result = list_watchlists()
    if result.get("status") != "success":
        return "[ERROR] Failed to get watchlist limits"
    watchlists = result.get("watchlists", [])
    output = "Watchlist Limits & Statistics\n"
    output += "=" * 40 + "\n"
    output += f"Total Watchlists: {len(watchlists)}\n"
    output += "Max Watchlists: Unlimited\n"
    output += "Max Symbols/Watchlist: Unlimited\n"
    output += "-" * 40 + "\n"
    for wl in watchlists:
        default = " [DEFAULT]" if wl.get("is_default") else ""
        output += f"  {wl['name']}: {wl.get('symbol_count', 0)} symbols{default}\n"
    return output


def _watchlist_scanner() -> str:
    """Show the scanner's tiered watchlist status (read-only)."""
    try:
        from src.autogen_agents.agents.scanner_config import (
            TieredWatchlistConfig,
            load_discovery_tickers,
        )

        config = TieredWatchlistConfig.from_config()
        limits = config.tier_limits

        output = "Scanner Tiered Watchlist\n"
        output += "=" * 50 + "\n"
        output += f"Status: {'Enabled' if config.enabled else 'Disabled'}\n"
        output += f"Max Symbols Per Scan: {config.max_symbols_per_scan}\n"
        output += "-" * 50 + "\n"
        output += "Tier Limits:\n"
        output += f"  [0] Positions:      {limits.positions} (from broker)\n"
        output += f"  [1] Pending Orders: {limits.pending_orders} (from broker)\n"
        output += f"  [2] Strategy:       {limits.strategy} (from config)\n"
        output += f"  [3] Discovery:      {limits.discovery} (user-added)\n"
        output += "-" * 50 + "\n"

        # Show discovery tickers
        discovery = load_discovery_tickers()
        output += f"Discovery Tickers ({len(discovery)}):\n"
        if discovery:
            output += f"  {', '.join(discovery[:10])}"
            if len(discovery) > 10:
                output += f"... (+{len(discovery) - 10} more)"
            output += "\n"
        else:
            output += "  (none)\n"

        output += "\nNote: Positions and pending orders are fetched from broker at scan time."
        return output

    except Exception as e:
        return f"[ERROR] Failed to load scanner config: {e}"


def _watchlist_handle_subcommand(subcommand: str, subarg: str | None) -> str:
    """Handle watchlist subcommands. Returns output string."""
    from src.cli.tools.watchlist_tools import show_watchlist, show_watchlists

    if subcommand == "list":
        return show_watchlists()
    if subcommand == "add":
        return _watchlist_add(subarg)
    if subcommand == "remove":
        return _watchlist_remove(subarg)
    if subcommand in ("show", "view"):
        if not subarg:
            return "Usage: /watchlist show <name>\nUse '/watchlist list' to see all watchlists"
        return show_watchlist(subarg)
    if subcommand == "create":
        return _watchlist_create(subarg)
    if subcommand == "limits":
        return _watchlist_limits()
    if subcommand in ("scanner", "tiers", "scan"):
        return _watchlist_scanner()
    return f"Unknown subcommand: {subcommand}\nUse /watchlist for help"


@command("/watchlist", aliases=["/wl"], help_text="Watchlist management", has_args=True)
def cmd_watchlist(session, args: str = None):
    """
    Manage watchlists for trading.

    Usage:
        /watchlist                      # Show all watchlists
        /watchlist list                 # List all watchlists
        /watchlist add <sym> [name]     # Add symbol to watchlist (default: 'default')
        /watchlist remove <sym> [name]  # Remove symbol from watchlist
        /watchlist show <name>          # Show specific watchlist details
        /watchlist create <name> [desc] # Create a new watchlist
        /watchlist limits               # Show watchlist statistics
        /watchlist scanner              # Show scanner tier status
    """
    from src.cli.tools.watchlist_tools import show_watchlists

    if not args:
        safe_print(show_watchlists())
        return

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower()
    subarg = parts[1] if len(parts) > 1 else None

    safe_print(_watchlist_handle_subcommand(subcommand, subarg))


# =============================================================================
# PARTIAL - Partial exit management (#508)
# =============================================================================


def _partial_handle_modify(subarg: str | None) -> str:
    """Handle /partial modify subcommand. Format: SYMBOL TARGET_NUM PRICE"""
    from src.cli.tools.partial_exit_tools import modify_exit_target

    if not subarg:
        return (
            "Usage: /partial modify <symbol> <target_num> <new_price>\n\n"
            "Examples:\n"
            "  /partial modify AAPL 1 205.50    # Change target 1 to $205.50\n"
            "  /partial modify TSLA 2 310.00    # Change target 2 to $310.00"
        )

    parts = subarg.split()
    if len(parts) < 3:
        return "Usage: /partial modify <symbol> <target_num> <new_price>"

    symbol = parts[0].upper()
    try:
        target_num = int(parts[1])
    except ValueError:
        return f"[ERROR] Invalid target number: {parts[1]} (must be an integer)"

    try:
        new_price = float(parts[2])
    except ValueError:
        return f"[ERROR] Invalid price: {parts[2]} (must be a number)"

    return modify_exit_target(symbol, target_num, new_price)


@command("/partial", aliases=["/pe"], help_text="Partial exit management", has_args=True)
def cmd_partial(session, args: str = None):
    """
    Manage partial exit strategies for positions.

    Usage:
        /partial                           # Show all partial exit plans
        /partial list                      # List all plans
        /partial summary                   # Summary of active targets
        /partial <symbol>                  # Show plan for specific symbol
        /partial levels <sym>              # Show exit target levels
        /partial active                    # Show only active targets
        /partial modify <sym> <n> <price>  # Modify target n exit price
    """
    from src.cli.tools.partial_exit_tools import (
        get_exit_summary,
        list_active_exits,
        show_all_partial_exits,
        show_exit_targets,
        show_partial_exit_plan,
    )

    if not args:
        safe_print(show_all_partial_exits())
        return

    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0].lower()
    subarg = parts[1] if len(parts) > 1 else None

    if subcommand in ("list", "ls"):
        safe_print(show_all_partial_exits())
    elif subcommand == "summary":
        safe_print(get_exit_summary())
    elif subcommand == "active":
        safe_print(list_active_exits())
    elif subcommand == "levels":
        if not subarg:
            safe_print("Usage: /partial levels <symbol>")
        else:
            safe_print(show_exit_targets(subarg.upper()))
    elif subcommand == "modify":
        safe_print(_partial_handle_modify(subarg))
    else:
        # Assume first arg is symbol
        symbol = subcommand.upper()
        if subcommand in ("symbols", "help"):
            safe_print(show_all_partial_exits())
        else:
            safe_print(show_partial_exit_plan(symbol))


# =============================================================================
# Ensure commands are registered on import
# =============================================================================

logger.debug(
    f"Slash commands registered: {len(__import__('src.cli.commands.registry', fromlist=['CommandRegistry']).CommandRegistry.list_commands())}"
)
