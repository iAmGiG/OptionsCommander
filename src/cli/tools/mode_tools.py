"""
Trading Mode CLI Tools for trading mode management.

Issue #456: Extract mode tools from cli_session.py.
Issue #400: Trading Modes Configuration System (already implemented).

This module wraps the existing TradingModeManager functions for the CLI.

Original Implementation: src/core/trading_modes.py
"""

from typing import Any, Dict

from src.core.trading_modes import (
    TradingMode,
    get_current_mode,
    get_mode_manager,
    get_mode_parameters,
    set_trading_mode,
)
from src.utils.safe_print import get_symbol

# ============================================================================
# Pure Function Wrappers
# ============================================================================
# These functions wrap the trading_modes module functions


def list_trading_modes() -> str:
    """
    List all available trading modes with descriptions.

    Shows all supported trading modes (Conservative, Moderate, Aggressive)
    with their descriptions and key parameters.

    Returns:
        Formatted string listing all modes

    Example:
        >>> list_trading_modes()
        '📊 Available Trading Modes:\\n  [✓] moderate - Balanced risk/reward...'
    """
    manager = get_mode_manager()
    all_modes = manager.get_all_modes()
    current = get_current_mode()

    output = f"{get_symbol('INFO')} Available Trading Modes:\n"
    output += "=" * 50 + "\n"

    for mode_name, params in all_modes.items():
        marker = "*" if params.mode == current else " "
        output += f"  [{marker}] {mode_name:15s} - {params.description}\n"
        output += f"      Position Size: {params.max_position_pct:.1%}, "
        output += f"Stop: {params.stop_loss:.1%}, "
        output += f"Target: {params.take_profit:.1%}\n"

    output += "\n" + "-" * 50 + "\n"
    output += f"{get_symbol('TARGET')} Current: {current.value}\n"

    return output


def set_mode(mode: str) -> str:
    """
    Change the active trading mode.

    Updates the global trading mode used for all position sizing,
    stop losses, and risk management decisions.

    Args:
        mode: New mode to set ('conservative', 'moderate', or 'aggressive')

    Returns:
        Success or error message

    Example:
        >>> set_mode('conservative')
        '✅ Trading mode changed to: conservative'
        >>> set_mode('invalid')
        '❌ Invalid mode: invalid. Valid: conservative, moderate, aggressive'
    """
    try:
        trading_mode = TradingMode.from_string(mode)
        old_mode = get_current_mode()
        set_trading_mode(trading_mode)
        return f"{get_symbol('SUCCESS')} Trading mode changed from {old_mode.value} to {trading_mode.value}"
    except ValueError as e:
        return f"{get_symbol('ERROR')} {str(e)}"


def show_current_mode() -> str:
    """
    Show the currently active trading mode with full details.

    Returns:
        Formatted string showing current mode and all parameters

    Example:
        >>> show_current_mode()
        '📍 Current Trading Mode: moderate\\n...\\nPosition Size: 10.0%...'
    """
    manager = get_mode_manager()
    current = get_current_mode()
    params = manager.get_parameters()

    output = f"{get_symbol('TARGET')} Current Trading Mode: {current.value}\n"
    output += "=" * 50 + "\n"
    output += f"{params.description}\n\n"

    output += "Position Sizing:\n"
    output += f"  Max Position Size: {params.max_position_pct:.1%} (${params.max_position_value:,.0f} max)\n"
    output += f"  Max Portfolio Exposure: {params.max_portfolio_pct:.1%}\n"
    output += f"  Max Positions: {params.max_positions}\n\n"

    output += "Risk Management:\n"
    output += f"  Stop Loss: {params.stop_loss:.1%}\n"
    output += f"  Take Profit: {params.take_profit:.1%}\n"
    output += f"  Risk Per Trade: {params.risk_per_trade:.1%}\n"
    output += f"  Min Confidence: {params.min_confidence:.0%}\n\n"

    output += "Trailing Stops:\n"
    output += f"  Enabled: {'Yes' if params.trailing_enabled else 'No'}\n"
    output += f"  Progressive: {'Yes' if params.progressive_enabled else 'No'}\n"
    if params.progressive_enabled:
        output += f"    Breakeven: {params.progressive_breakeven_pct:.1%}\n"
        output += f"    Lock 25%: {params.progressive_lock_25_pct:.1%}\n"
        output += f"    Trail 50%: {params.progressive_trail_50_pct:.1%}\n"

    return output


def show_mode_comparison() -> str:
    """
    Show side-by-side comparison of all trading modes.

    Displays key parameters for all modes to help users understand
    the differences and choose the right mode for their risk tolerance.

    Returns:
        Formatted comparison table

    Example:
        >>> show_mode_comparison()
        '📊 Trading Mode Comparison:\\n...\\n| Mode | Position | Stop | Target |...'
    """
    manager = get_mode_manager()
    all_modes = manager.get_all_modes()
    current = get_current_mode()

    output = f"{get_symbol('INFO')} Trading Mode Comparison:\n"
    output += "=" * 80 + "\n\n"

    output += (
        f"{'Mode':<15} {'Position':<10} {'Stop':<8} {'Target':<8} {'Risk/Trade':<12} {'Status'}\n"
    )
    output += "-" * 80 + "\n"

    for mode_name, params in all_modes.items():
        status = f"{get_symbol('TARGET')} Current" if params.mode == current else ""
        output += f"{mode_name:<15} "
        output += f"{params.max_position_pct:>8.1%}  "
        output += f"{params.stop_loss:>6.1%}  "
        output += f"{params.take_profit:>6.1%}  "
        output += f"{params.risk_per_trade:>10.1%}  "
        output += f"{status}\n"

    return output


def get_mode_parameters_dict(mode: str = None) -> Dict[str, Any]:
    """
    Get mode parameters as structured data for agents.

    Provides trading mode configuration in a structured format suitable
    for programmatic consumption by agents.

    Args:
        mode: Mode to query (default: current mode)

    Returns:
        Dictionary with all mode parameters

    Example:
        >>> get_mode_parameters_dict('moderate')
        {'mode': 'moderate', 'max_position_pct': 0.10, ...}
    """
    if mode:
        try:
            trading_mode = TradingMode.from_string(mode)
        except ValueError:
            return {"error": f"Invalid mode: {mode}"}
    else:
        trading_mode = None

    params = get_mode_parameters(trading_mode)

    return {
        "mode": params.mode.value,
        "description": params.description,
        "max_position_pct": params.max_position_pct,
        "max_position_value": params.max_position_value,
        "max_portfolio_pct": params.max_portfolio_pct,
        "max_positions": params.max_positions,
        "stop_loss": params.stop_loss,
        "take_profit": params.take_profit,
        "trailing_enabled": params.trailing_enabled,
        "progressive_enabled": params.progressive_enabled,
        "risk_per_trade": params.risk_per_trade,
        "min_confidence": params.min_confidence,
    }


def validate_mode(mode: str) -> str:
    """
    Validate a trading mode string.

    Checks if a mode name is valid and provides information about it.

    Args:
        mode: Mode to validate

    Returns:
        Validation result with mode info or error message

    Example:
        >>> validate_mode('moderate')
        '✅ Valid mode: moderate - Balanced risk/reward...'
        >>> validate_mode('invalid')
        '❌ Invalid mode: invalid. Valid: conservative, moderate, aggressive'
    """
    try:
        trading_mode = TradingMode.from_string(mode)
        params = get_mode_parameters(trading_mode)
        return f"{get_symbol('SUCCESS')} Valid mode: {trading_mode.value} - {params.description}"
    except ValueError as e:
        return f"{get_symbol('ERROR')} {str(e)}"


# Export tools for direct import (registered via __init__.py auto-discovery)


__all__ = [
    "list_trading_modes",
    "set_mode",
    "show_current_mode",
    "show_mode_comparison",
    "get_mode_parameters_dict",
    "validate_mode",
]
