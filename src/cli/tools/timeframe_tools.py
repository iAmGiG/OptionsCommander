"""
Timeframe CLI Tools - FunctionTool wrappers for timeframe management.

Issue #456: Extract timeframe tools from cli_session.py.
Issue #365: Natural language timeframe commands (already implemented).

This module wraps the existing TimeframeCommands class methods as FunctionTool
instances for integration with the AutoGen agent architecture.

Original Implementation: src/cli/timeframe_commands.py
Pattern: Pure function wrappers → FunctionTool → Registry
"""

from typing import Dict, Optional

from agent_framework import FunctionTool

from src.autogen_agents.agents.multi_timeframe_voter import MULTI_TIMEFRAME_PRESETS
from src.cli.commands.timeframe_commands import get_timeframe_commands
from src.utils.safe_print import get_symbol

# Get singleton instance for all tool functions
_tf_commands = get_timeframe_commands()


# ============================================================================
# Pure Function Wrappers
# ============================================================================
# These functions wrap the TimeframeCommands methods to provide a functional
# interface that works well with FunctionTool


def list_timeframes(verbose: bool = False) -> str:
    """
    List all available timeframes.

    Shows all supported trading timeframes with optional descriptions.
    Indicates the currently active timeframe.

    Args:
        verbose: Show detailed descriptions for each timeframe

    Returns:
        Formatted string listing all timeframes

    Example:
        >>> list_timeframes()
        '📊 Available Timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1wk, 1mo'
        >>> list_timeframes(verbose=True)
        '📊 Available Timeframes:\\n  [✓] 1d - Daily bars (recommended)\\n  [ ] 1h - Hourly bars...'
    """
    return _tf_commands.list_timeframes(verbose=verbose)


def set_timeframe(timeframe: str) -> str:
    """
    Change the active trading timeframe.

    Updates the global timeframe used for all trading operations.
    Validates the timeframe before applying.

    Args:
        timeframe: New timeframe to set (e.g., '1d', '1h', '5m')

    Returns:
        Success or error message

    Example:
        >>> set_timeframe('1h')
        '✅ Timeframe changed from 1d to 1h'
        >>> set_timeframe('invalid')
        '❌ Invalid timeframe: invalid'
    """
    return _tf_commands.set_timeframe(timeframe)


def show_current_timeframe() -> str:
    """
    Show the currently active timeframe.

    Returns:
        Formatted string showing current timeframe with description

    Example:
        >>> show_current_timeframe()
        '📍 Current Timeframe: 1d (Daily bars - recommended for swing trading)'
    """
    return _tf_commands.show_current_timeframe()


def show_timeframe_recommendations() -> str:
    """
    Show timeframe recommendations for different trading styles.

    Provides guidance on which timeframes are best suited for different
    trading strategies (scalping, day trading, swing trading, etc.).

    Returns:
        Formatted string with recommendations by trading style

    Example:
        >>> show_timeframe_recommendations()
        '📈 Timeframe Recommendations\\n🎯 Scalping: 1m, 5m\\n📊 Day Trading: 15m, 30m, 1h...'
    """
    return _tf_commands.show_timeframe_recommendations()


def validate_and_info(timeframe: str) -> str:
    """
    Validate a timeframe and show detailed information.

    Checks if a timeframe is valid and provides detailed information
    about it if valid.

    Args:
        timeframe: Timeframe to validate and describe

    Returns:
        Validation result and info, or error message

    Example:
        >>> validate_and_info('1d')
        'ℹ️  1d (current)\\nDaily bars - recommended for swing trading'
        >>> validate_and_info('invalid')
        '❌ Invalid timeframe: invalid'
    """
    return _tf_commands.validate_and_info(timeframe)


def get_timeframe_for_agent(command: str, arg: Optional[str] = None) -> Dict:
    """
    Get timeframe data for agents (structured data, no formatting).

    Provides timeframe information in a structured format suitable for
    programmatic consumption by agents.

    Args:
        command: Command to execute ('current', 'set', 'list', 'validate', 'info',
            'recommendations')
        arg: Optional argument for the command

    Returns:
        Dictionary with structured timeframe data

    Example:
        >>> get_timeframe_for_agent('current')
        {'type': 'current_timeframe', 'timeframe': '1d'}
        >>> get_timeframe_for_agent('set', '1h')
        {'success': True, 'message': 'Timeframe changed...'}
    """
    return _tf_commands.get_for_agent(command, arg)


# ============================================================================
# Enhanced Timeframe Tools (Issue #489)
# ============================================================================
# Multi-timeframe and custom timeframe support


# Multi-timeframe presets: the weights the voter actually uses (#637), with a trading style
_PRESET_STYLES = {
    "trend_following": "swing",
    "intraday": "day",
    "position": "position",
    "scalping": "scalp",
}
MULTI_TF_PRESETS = {
    name: {
        "description": preset["description"],
        "timeframes": preset["timeframes"],
        "style": _PRESET_STYLES.get(name, "custom"),
    }
    for name, preset in MULTI_TIMEFRAME_PRESETS.items()
}

# Track current mode
_current_mode = {"mode": "single", "preset": None}


def list_multi_timeframe_presets() -> str:
    """
    List available multi-timeframe voting presets.

    Shows all presets with their timeframe weights and trading styles.
    Multi-timeframe analysis combines signals from multiple timeframes
    for more robust trading decisions.

    Returns:
        Formatted string listing all presets

    Example:
        >>> list_multi_timeframe_presets()
        'Multi-Timeframe Presets
        trend_following: 1d (50%), 4h (30%), 1h (20%)
        intraday: 1h (50%), 15m (30%), 5m (20%)'
    """
    output = f"{get_symbol('INFO')} Multi-Timeframe Presets\n"
    output += "=" * 60 + "\n\n"

    current_preset = _current_mode.get("preset")
    current_mode = _current_mode.get("mode", "single")

    for name, config in MULTI_TF_PRESETS.items():
        marker = (
            f"{get_symbol('TARGET')}"
            if name == current_preset and current_mode == "multi"
            else "  "
        )
        output += f"{marker} {name}\n"
        output += f"   {config['description']}\n"

        # Format timeframes with weights
        tf_str = ", ".join(
            [f"{tf} ({int(weight * 100)}%)" for tf, weight in config["timeframes"].items()]
        )
        output += f"   Timeframes: {tf_str}\n"
        output += f"   Style: {config['style']}\n\n"

    output += "-" * 60 + "\n"
    if current_mode == "multi":
        output += f"{get_symbol('TARGET')} Current: Multi-timeframe ({current_preset})\n"
    else:
        output += f"{get_symbol('TARGET')} Current: Single timeframe mode\n"

    return output


def set_multi_timeframe_preset(preset: str) -> str:
    """
    Switch to multi-timeframe mode with a specific preset.

    Enables multi-timeframe analysis where signals from multiple
    timeframes are combined using weighted voting.

    Args:
        preset: Preset name ('trend_following', 'intraday', 'position', 'scalping')

    Returns:
        Success or error message

    Example:
        >>> set_multi_timeframe_preset('trend_following')
        'Switched to multi-timeframe: trend_following
        Timeframes: 1d (50%), 4h (30%), 1h (20%)'
    """
    preset = preset.lower()

    if preset not in MULTI_TF_PRESETS:
        valid = ", ".join(MULTI_TF_PRESETS.keys())
        return f"{get_symbol('ERROR')} Invalid preset: {preset}. Valid: {valid}"

    config = MULTI_TF_PRESETS[preset]
    _current_mode["mode"] = "multi"
    _current_mode["preset"] = preset

    tf_str = ", ".join(
        [f"{tf} ({int(weight * 100)}%)" for tf, weight in config["timeframes"].items()]
    )

    output = f"{get_symbol('SUCCESS')} Switched to multi-timeframe: {preset}\n"
    output += f"   {config['description']}\n"
    output += f"   Timeframes: {tf_str}\n"
    output += "-" * 50 + "\n"
    output += "   Note: Multi-TF voting combines signals from all timeframes\n"
    output += "   with the specified weights for consensus decisions.\n"

    return output


def set_single_timeframe(timeframe: str) -> str:
    """
    Switch to single timeframe mode.

    Disables multi-timeframe analysis and uses a single timeframe
    for all trading decisions.

    Args:
        timeframe: Timeframe to use (e.g., '1d', '1h', '5m')

    Returns:
        Success or error message

    Example:
        >>> set_single_timeframe('1d')
        'Switched to single timeframe: 1d'
    """
    # First validate and set the timeframe
    result = _tf_commands.set_timeframe(timeframe)

    # Check if result indicates an error (by looking for common error patterns)
    if "Invalid" in result or "Error" in result or get_symbol("ERROR") in result:
        return result

    # Switch mode
    _current_mode["mode"] = "single"
    _current_mode["preset"] = None

    return f"{get_symbol('SUCCESS')} Switched to single timeframe mode: {timeframe}\n" + result


def show_timeframe_mode() -> str:
    """
    Show current timeframe mode (single or multi).

    Displays whether single or multi-timeframe mode is active
    and the current configuration.

    Returns:
        Formatted string showing current mode

    Example:
        >>> show_timeframe_mode()
        'Mode: Multi-timeframe (trend_following)
        Timeframes: 1d (50%), 4h (30%), 1h (20%)'
    """
    mode = _current_mode.get("mode", "single")

    output = f"{get_symbol('TARGET')} Timeframe Mode\n"
    output += "=" * 50 + "\n"

    if mode == "multi":
        preset = _current_mode.get("preset")
        config = MULTI_TF_PRESETS.get(preset, {})

        output += "  Mode: Multi-timeframe\n"
        output += f"  Preset: {preset}\n"
        output += f"  Description: {config.get('description', 'N/A')}\n"
        output += "-" * 50 + "\n"
        output += "  Timeframe Weights:\n"
        for tf, weight in config.get("timeframes", {}).items():
            output += f"    {tf}: {int(weight * 100)}%\n"
    else:
        current_tf = _tf_commands.manager.get_current_timeframe()
        output += "  Mode: Single timeframe\n"
        output += f"  Timeframe: {current_tf}\n"
        output += "-" * 50 + "\n"
        output += "  Use '/timeframe preset <name>' to enable multi-timeframe\n"

    return output


def _parse_timeframe_to_minutes(timeframe: str) -> int | None:
    """Parse a timeframe string to total minutes. Returns None if invalid."""
    import re

    unit_multipliers = {
        "minutes": 1,
        "hours": 60,
        "days": 60 * 24,
        "weeks": 60 * 24 * 7,
    }

    patterns = [
        (r"^(\d+)m$", "minutes"),
        (r"^(\d+\.?\d*)h$", "hours"),
        (r"^(\d+)d$", "days"),
        (r"^(\d+)w$", "weeks"),
    ]

    for pattern, unit in patterns:
        match = re.match(pattern, timeframe.lower())
        if match:
            value = float(match.group(1))
            return int(value * unit_multipliers[unit])
    return None


def _get_base_timeframe(minutes: int) -> str:
    """Get the appropriate base timeframe for aggregation."""
    if minutes <= 60:
        return "1m"
    elif minutes <= 240:
        return "5m"
    elif minutes <= 1440:
        return "15m"
    return "1h"


def validate_custom_timeframe(timeframe: str) -> str:
    """
    Validate a timeframe including custom notations.

    Validates standard timeframes (1d, 1h, etc.) and custom notations
    (65m, 1.5h, 2d) that can be built from base data.

    Args:
        timeframe: Timeframe string to validate

    Returns:
        Validation result with details

    Example:
        >>> validate_custom_timeframe('65m')
        'Valid custom timeframe: 65m
        Minutes: 65
        Requires: 5m base data aggregation'
    """
    # Standard timeframes
    standard_tfs = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"]

    if timeframe in standard_tfs:
        return _tf_commands.validate_and_info(timeframe)

    # Parse custom timeframe
    minutes = _parse_timeframe_to_minutes(timeframe)

    if minutes is None:
        return f"{get_symbol('ERROR')} Invalid timeframe: {timeframe}\n\nValid formats: 15m, 1h, 1.5h, 2d, 1w"

    base_tf = _get_base_timeframe(minutes)

    output = f"{get_symbol('SUCCESS')} Valid custom timeframe: {timeframe}\n"
    output += "=" * 50 + "\n"
    output += f"  Total Minutes: {minutes}\n"
    output += f"  Base Timeframe: {base_tf} (for aggregation)\n"
    output += "-" * 50 + "\n"
    output += "  Note: Custom timeframes are built by aggregating\n"
    output += f"  {base_tf} bars into {timeframe} bars.\n"

    return output


def get_multi_timeframe_params() -> Dict:
    """
    Get multi-timeframe parameters as structured data for agents.

    Provides multi-timeframe configuration in a structured format
    suitable for programmatic consumption.

    Returns:
        Dictionary with mode and configuration

    Example:
        >>> get_multi_timeframe_params()
        {'mode': 'multi', 'preset': 'trend_following', 'timeframes': {...}}
    """
    mode = _current_mode.get("mode", "single")

    if mode == "multi":
        preset = _current_mode.get("preset")
        config = MULTI_TF_PRESETS.get(preset, {})
        return {
            "mode": "multi",
            "preset": preset,
            "timeframes": config.get("timeframes", {}),
            "description": config.get("description", ""),
            "style": config.get("style", ""),
        }
    else:
        return {
            "mode": "single",
            "timeframe": _tf_commands.manager.get_current_timeframe(),
            "preset": None,
        }


# ============================================================================
# FunctionTool Instances
# ============================================================================
# Each function is wrapped in a FunctionTool for AutoGen integration


list_timeframes_tool = FunctionTool(
    func=list_timeframes,
    name="list_timeframes",
    description="List all available trading timeframes with optional descriptions",
)

set_timeframe_tool = FunctionTool(
    func=set_timeframe,
    name="set_timeframe",
    description="Change the active trading timeframe (e.g., '1d', '1h', '5m')",
)

show_current_timeframe_tool = FunctionTool(
    func=show_current_timeframe,
    name="show_current_timeframe",
    description="Show the currently active trading timeframe",
)

show_timeframe_recommendations_tool = FunctionTool(
    func=show_timeframe_recommendations,
    name="show_timeframe_recommendations",
    description="Show timeframe recommendations for different trading styles",
)

validate_timeframe_tool = FunctionTool(
    func=validate_and_info,
    name="validate_timeframe",
    description="Validate a timeframe and show detailed information about it",
)

get_timeframe_for_agent_tool = FunctionTool(
    func=get_timeframe_for_agent,
    name="get_timeframe_for_agent",
    description="Get structured timeframe data for programmatic use by agents",
)

# Enhanced timeframe tools (Issue #489)
list_multi_timeframe_presets_tool = FunctionTool(
    func=list_multi_timeframe_presets,
    name="list_multi_timeframe_presets",
    description="List available multi-timeframe voting presets with weights",
)

set_multi_timeframe_preset_tool = FunctionTool(
    func=set_multi_timeframe_preset,
    name="set_multi_timeframe_preset",
    description="Switch to multi-timeframe mode with a preset (trend_following, intraday, etc.)",
)

set_single_timeframe_tool = FunctionTool(
    func=set_single_timeframe,
    name="set_single_timeframe",
    description="Switch to single timeframe mode with a specific timeframe",
)

show_timeframe_mode_tool = FunctionTool(
    func=show_timeframe_mode,
    name="show_timeframe_mode",
    description="Show current timeframe mode (single or multi) and configuration",
)

validate_custom_timeframe_tool = FunctionTool(
    func=validate_custom_timeframe,
    name="validate_custom_timeframe",
    description="Validate standard and custom timeframe notations (65m, 1.5h, 2d)",
)

get_multi_timeframe_params_tool = FunctionTool(
    func=get_multi_timeframe_params,
    name="get_multi_timeframe_params",
    description="Get multi-timeframe parameters as structured data for agents",
)


# Export tools for direct import (registered via __init__.py auto-discovery)
CLI_TIMEFRAME_TOOLS = [
    # Original timeframe tools
    list_timeframes_tool,
    set_timeframe_tool,
    show_current_timeframe_tool,
    show_timeframe_recommendations_tool,
    validate_timeframe_tool,
    get_timeframe_for_agent_tool,
    # Enhanced timeframe tools (Issue #489)
    list_multi_timeframe_presets_tool,
    set_multi_timeframe_preset_tool,
    set_single_timeframe_tool,
    show_timeframe_mode_tool,
    validate_custom_timeframe_tool,
    get_multi_timeframe_params_tool,
]


__all__ = [
    # Functions (original)
    "list_timeframes",
    "set_timeframe",
    "show_current_timeframe",
    "show_timeframe_recommendations",
    "validate_and_info",
    "get_timeframe_for_agent",
    # Functions (enhanced - Issue #489)
    "list_multi_timeframe_presets",
    "set_multi_timeframe_preset",
    "set_single_timeframe",
    "show_timeframe_mode",
    "validate_custom_timeframe",
    "get_multi_timeframe_params",
    # Tools (original)
    "list_timeframes_tool",
    "set_timeframe_tool",
    "show_current_timeframe_tool",
    "show_timeframe_recommendations_tool",
    "validate_timeframe_tool",
    "get_timeframe_for_agent_tool",
    # Tools (enhanced - Issue #489)
    "list_multi_timeframe_presets_tool",
    "set_multi_timeframe_preset_tool",
    "set_single_timeframe_tool",
    "show_timeframe_mode_tool",
    "validate_custom_timeframe_tool",
    "get_multi_timeframe_params_tool",
    # Collections
    "CLI_TIMEFRAME_TOOLS",
    # Constants
    "MULTI_TF_PRESETS",
]
