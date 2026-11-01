"""
Trailing Stop CLI Tools - FunctionTool wrappers for trailing stop management.

Issue #414: Advanced Trailing Stop Automation (KILLER FEATURE)
Issue #424: Trailing Stop CLI Commands - Visibility & Control

This module wraps TrailingStopManager functions as FunctionTool instances
for integration with the AutoGen agent architecture.

Original Implementation: src/trading/orders/trailing_stop_manager.py
Pattern: Pure function wrappers → FunctionTool → Registry
"""

from dataclasses import replace
from typing import Optional

from agent_framework import FunctionTool

from config_defaults.trading_config import ClimbRate, TradingConfig

from src.utils.safe_print import get_symbol

VALID_CLIMB_RATES = ("slow", "medium", "fast")


def _live_climb_rate() -> str:
    """The climb rate the routines' TrailingStopManager reads."""
    return TradingConfig().get_trailing_stop_config().climb_rate


# ============================================================================
# Pure Function Wrappers
# ============================================================================


def show_trailing_stop_config() -> str:
    """
    Display current trailing stop configuration.

    Shows all trailing stop settings including climb rate,
    profit zone thresholds, and volatility awareness settings.

    Returns:
        Formatted string with configuration details

    Example:
        >>> show_trailing_stop_config()
        '⚙️ Trailing Stop Configuration
        Climb Rate: medium
        Volatility Aware: No
        Profit Zone Start: 2.0%'
    """
    # What the routines' TrailingStopManager reads, not the dataclass's defaults (#637)
    config = TradingConfig().get_trailing_stop_config()

    output = f"{get_symbol('GEAR')} Trailing Stop Configuration\n"
    output += "=" * 50 + "\n"

    # Basic settings
    output += f"  Enabled: {'Yes' if config.enabled else 'No'}\n"
    output += f"  Progressive Mode: {'Yes' if config.progressive_enabled else 'No'}\n"
    output += "-" * 50 + "\n"

    # Issue #414: Advanced features
    output += f"{get_symbol('CHART')} Climb Rate Settings\n"
    output += f"  Climb Rate: {config.climb_rate}\n"

    # Get gain lock percentages
    gain_locks = ClimbRate.get_gain_locks(config.climb_rate)
    output += f"  Gain Locks: {gain_locks[1] * 100:.0f}% / {gain_locks[2] * 100:.0f}% / {gain_locks[3] * 100:.0f}%\n"
    output += "-" * 50 + "\n"

    # Progressive thresholds
    output += f"{get_symbol('INFO')} Profit Zone Thresholds\n"
    output += f"  Profit Zone Start: {config.profit_zone_start_pct * 100:.1f}%\n"
    output += f"  Breakeven Trigger: {config.progressive_breakeven_pct * 100:.1f}%\n"
    output += f"  Lock 25% Trigger:  {config.progressive_lock_25_pct * 100:.1f}%\n"
    output += f"  Trail 50% Trigger: {config.progressive_trail_50_pct * 100:.1f}%\n"
    output += "-" * 50 + "\n"

    # Volatility settings
    output += f"{get_symbol('WAVE')} Volatility Settings\n"
    output += f"  Volatility Aware: {'Yes' if config.volatility_aware else 'No'}\n"
    if config.volatility_aware:
        output += f"  ATR Multiplier: {config.atr_multiplier}x\n"
        output += f"  ATR Period: {config.atr_period}\n"
    output += "-" * 50 + "\n"

    # Safety settings
    output += f"{get_symbol('SHIELD')} Safety Settings\n"
    output += f"  Never Move Down: {'Yes' if config.never_move_stop_down else 'No'}\n"
    output += f"  Min Update Interval: {config.min_update_interval_seconds}s\n"

    return output


def explain_climb_rate(rate: Optional[str] = None) -> str:
    """
    Explain what a climb rate means for trailing stops.

    Climb rate controls how aggressively stops lock in gains
    as the position moves into profit.

    Args:
        rate: Climb rate to explain - 'slow', 'medium', or 'fast' (default: the live one)

    Returns:
        Formatted explanation of the climb rate

    Example:
        >>> explain_climb_rate('fast')
        f"'{get_symbol('ROCKET')} Fast Climb Rate
        Aggressively locks gains: 33% → 60% → 80%
        Best for: Momentum trades, volatile stocks'
    """
    rate = (rate or _live_climb_rate()).lower()

    if rate not in VALID_CLIMB_RATES:
        return f"{get_symbol('ERROR')} Invalid climb rate: {rate}. Valid: slow, medium, fast"

    gain_locks = ClimbRate.get_gain_locks(rate)

    descriptions = {
        "slow": {
            "emoji": get_symbol("TURTLE"),
            "title": "Slow (Conservative)",
            "style": "Conservative trailing - gives positions room to breathe",
            "best_for": "Swing trades, low volatility stocks, trending markets",
            "risk": "May give back more gains on reversals",
        },
        "medium": {
            "emoji": get_symbol("SCALES"),
            "title": "Medium (Balanced)",
            "style": "Balanced approach - standard trailing behavior",
            "best_for": "Most trades, general use, mixed market conditions",
            "risk": "Good balance of gain protection vs room to run",
        },
        "fast": {
            "emoji": get_symbol("ROCKET"),
            "title": "Fast (Aggressive)",
            "style": "Aggressive trailing - quickly locks in gains",
            "best_for": "Day trades, momentum plays, volatile stocks",
            "risk": "May exit too early on pullbacks",
        },
    }

    desc = descriptions[rate]

    output = f"{desc['emoji']} {desc['title']} Climb Rate\n"
    output += "=" * 50 + "\n"
    output += f"  Style: {desc['style']}\n"
    output += "-" * 50 + "\n"
    output += "  Gain Lock Schedule:\n"
    output += "    +2-4%:  Move the stop to breakeven\n"
    output += f"    +4-6%:  Lock {gain_locks[1] * 100:.0f}% of gains\n"
    output += f"    +6-10%: Lock {gain_locks[2] * 100:.0f}% of gains\n"
    output += f"    +10%+:  Lock {gain_locks[3] * 100:.0f}% of gains\n"
    output += "-" * 50 + "\n"
    output += f"  Best For: {desc['best_for']}\n"
    output += f"  Trade-off: {desc['risk']}\n"

    return output


def compare_climb_rates() -> str:
    """
    Compare all available climb rates side by side.

    Shows how each climb rate locks gains at different
    profit levels to help choose the right setting.

    Returns:
        Formatted comparison table

    Example:
        >>> compare_climb_rates()
        'Climb Rate Comparison
        Profit Zone | Slow  | Medium | Fast
        +4% profit  |  20%  |  25%   | 33%'
    """
    output = f"{get_symbol('INFO')} Climb Rate Comparison\n"
    output += "=" * 60 + "\n\n"

    # Get all climb rates
    slow = ClimbRate.get_gain_locks("slow")
    medium = ClimbRate.get_gain_locks("medium")
    fast = ClimbRate.get_gain_locks("fast")

    # Header
    output += f"{'Profit Level':<20} {'Slow':>10} {'Medium':>10} {'Fast':>10}\n"
    output += "-" * 60 + "\n"

    # The share of the gain each tier locks; the first moves the stop to breakeven
    output += f"{'+2-4%':<20} {'breakeven':>10} {'breakeven':>10} {'breakeven':>10}\n"
    zones = [
        ("+4-6%", 1),
        ("+6-10%", 2),
        ("+10%+", 3),
    ]

    for zone_name, idx in zones:
        output += f"{zone_name:<20} {slow[idx] * 100:>9.0f}% {medium[idx] * 100:>9.0f}% {fast[idx] * 100:>9.0f}%\n"

    output += "-" * 60 + "\n"
    output += "\n"
    output += f"  {get_symbol('TURTLE')} Slow: Conservative, lets winners run\n"
    output += f"  {get_symbol('SCALES')} Medium: Balanced default setting\n"
    output += f"  {get_symbol('ROCKET')} Fast: Aggressive profit protection\n"

    return output


def _routine_stop_level(
    entry_price: float, current_price: float, stop: float, climb_rate: str
) -> Optional[float]:
    """The stop the routines' own code gives these prices, or None to leave it where it is."""
    from src.trading.orders.trailing_stop_manager import TrailingStopManager
    from src.trading.state.state_reconciler import StateReconciler

    config = TradingConfig()
    trailing = replace(config.get_trailing_stop_config(), climb_rate=climb_rate)
    manager = TrailingStopManager(order_manager=None, config=config, trailing_config=trailing)
    reconciler = StateReconciler(
        config=config, trailing_stop_manager=manager, position_tracker=None
    )
    return reconciler._trailing_level(
        "EXAMPLE",
        {"EXAMPLE": {"entry_price": entry_price}},
        stop,
        1,
        current_price,
        adopt_order_id="EXAMPLE",
    )


def _tier_text(profit_pct: float, climb_rate: str) -> str:
    """The profit tier a position is in, as explain_climb_rate names them."""
    gain_locks = ClimbRate.get_gain_locks(climb_rate)
    if profit_pct < 2.0:
        return "Under +2%: no change"
    if profit_pct < 4.0:
        return "+2-4%: move the stop to breakeven"
    if profit_pct < 6.0:
        return f"+4-6%: lock {gain_locks[1] * 100:.0f}% of gains"
    if profit_pct < 10.0:
        return f"+6-10%: lock {gain_locks[2] * 100:.0f}% of gains"
    return f"+10%+: lock {gain_locks[3] * 100:.0f}% of gains"


def calculate_stop_example(
    entry_price: float,
    current_price: float,
    initial_stop: float,
    climb_rate: Optional[str] = None,
) -> str:
    """
    Calculate where the trailing stop would be for a position.

    Uses the routines' own code, so the tiers, the rule that a stop never moves down and
    the minimum gap below the price all apply. Places and changes nothing.

    Args:
        entry_price: Original entry price
        current_price: Current market price
        initial_stop: The stop in force now
        climb_rate: Climb rate setting (default: the live one)

    Returns:
        Formatted calculation showing stop placement
    """
    rate = (climb_rate or _live_climb_rate()).lower()
    if rate not in VALID_CLIMB_RATES:
        return f"{get_symbol('ERROR')} Invalid climb rate: {rate}. Valid: slow, medium, fast"
    if min(entry_price, current_price, initial_stop) <= 0:
        return f"{get_symbol('ERROR')} Entry, price and stop must be positive"
    if initial_stop >= current_price:
        return f"{get_symbol('ERROR')} A stop at or above the price would already have fired"

    profit_pct = ((current_price - entry_price) / entry_price) * 100
    level = _routine_stop_level(entry_price, current_price, initial_stop, rate)

    output = f"{get_symbol('RULER')} Stop Calculation Example\n"
    output += "=" * 50 + "\n"
    output += f"  Entry Price:   ${entry_price:.2f}\n"
    output += f"  Current Price: ${current_price:.2f} ({profit_pct:+.1f}%)\n"
    output += f"  Current Stop:  ${initial_stop:.2f}\n"
    output += f"  Climb Rate:    {rate}\n"
    output += "-" * 50 + "\n"
    output += f"  Tier:     {_tier_text(profit_pct, rate)}\n"
    if level is None:
        output += f"  New Stop: none, it stays at ${initial_stop:.2f} (stops never move down)\n"
    else:
        output += f"  New Stop: ${level:.2f} (up ${level - initial_stop:.2f})\n"
    output += "-" * 50 + "\n"
    output += "  The evening routine raises stops, while the market is open.\n"

    return output


# ============================================================================
# FunctionTool Definitions
# ============================================================================

show_trailing_stop_config_tool = FunctionTool(
    func=show_trailing_stop_config,
    name="show_trailing_stop_config",
    description=(
        "Display current trailing stop configuration including climb rate, "
        "profit zone thresholds, and volatility awareness settings."
    ),
)

explain_climb_rate_tool = FunctionTool(
    func=explain_climb_rate,
    name="explain_climb_rate",
    description=(
        "Explain what a specific climb rate (slow/medium/fast) means "
        "for trailing stop behavior and when to use it."
    ),
)

compare_climb_rates_tool = FunctionTool(
    func=compare_climb_rates,
    name="compare_climb_rates",
    description=(
        "Compare all climb rates side by side showing how each "
        "locks gains at different profit levels."
    ),
)

calculate_stop_example_tool = FunctionTool(
    func=calculate_stop_example,
    name="calculate_stop_example",
    description=(
        "Calculate where the trailing stop would be for a specific "
        "position based on entry, current price, and climb rate."
    ),
)


# ============================================================================
# Tool Collection for Registry
# ============================================================================

CLI_TRAILING_STOP_TOOLS = [
    show_trailing_stop_config_tool,
    explain_climb_rate_tool,
    compare_climb_rates_tool,
    calculate_stop_example_tool,
]

__all__ = [
    "show_trailing_stop_config",
    "explain_climb_rate",
    "compare_climb_rates",
    "calculate_stop_example",
    "show_trailing_stop_config_tool",
    "explain_climb_rate_tool",
    "compare_climb_rates_tool",
    "calculate_stop_example_tool",
    "CLI_TRAILING_STOP_TOOLS",
]
