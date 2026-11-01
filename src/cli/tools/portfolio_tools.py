"""
Portfolio and Position Display Tools - FunctionTool implementations.

Issue #433/#457: Extract portfolio display commands from cli_session.py.

These tools handle:
- Portfolio status display (account equity, cash, buying power)
- Position listing with P/L calculations
- Individual position details with stop/target levels
- Orders display with visual hierarchy

Architecture:
- Pure functions for easy testing
- Type hints for auto-generated schemas
- Reusable by both CLI and AutoGen agents
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from agent_framework import FunctionTool

from config_defaults.message_loader import CLIMessages as MSG  # noqa: N814
from config_defaults.message_loader import get_pl_emoji

from src.utils.safe_print import get_symbol

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Structured Returns
# =============================================================================


@dataclass
class PositionInfo:
    """Structured position data for display."""

    symbol: str
    qty: int
    avg_entry: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    stop_price: Optional[float] = None
    target_price: Optional[float] = None


@dataclass
class AccountInfo:
    """Structured account data for display."""

    equity: float
    cash: float
    buying_power: float
    pattern_day_trader: bool = False


@dataclass
class PortfolioSummary:
    """Complete portfolio summary with account and positions."""

    account: AccountInfo
    positions: List[PositionInfo]
    total_positions: int


# =============================================================================
# Core Portfolio Functions
# =============================================================================


def get_account_status(account_monitor: Any) -> Optional[AccountInfo]:
    """
    Get current account status from broker.

    Args:
        account_monitor: AlpacaAccountMonitor instance

    Returns:
        AccountInfo with equity, cash, buying power, and PDT status
    """
    if not account_monitor:
        return None

    try:
        account = account_monitor.get_account_status()
        return AccountInfo(
            equity=float(account.get("equity", 0)),
            cash=float(account.get("cash", 0)),
            buying_power=float(account.get("buying_power", 0)),
            pattern_day_trader=account.get("pattern_day_trader", False),
        )
    except Exception as e:
        logger.error(f"Error getting account status: {e}", exc_info=True)
        return None


def get_positions_list(account_monitor: Any) -> List[PositionInfo]:
    """
    Get list of all current positions from broker.

    Args:
        account_monitor: AlpacaAccountMonitor instance

    Returns:
        List of PositionInfo with calculated P/L
    """
    if not account_monitor:
        return []

    try:
        positions = account_monitor.get_positions()
        result = []

        for pos in positions:
            qty = int(pos.get("qty", 0))
            symbol = pos.get("symbol", "UNKNOWN")
            avg_entry = float(pos.get("avg_entry_price", 0))

            # Calculate current price from market value
            market_value = float(pos.get("market_value", 0))
            current_price = (market_value / qty) if qty > 0 else 0.0

            # Use cost_basis as fallback if avg_entry_price is 0
            if avg_entry == 0.0:
                cost_basis = float(pos.get("cost_basis", 0))
                avg_entry = (cost_basis / qty) if qty > 0 else 0.0

            unrealized_pl = float(pos.get("unrealized_pl", 0))
            unrealized_plpc = float(pos.get("unrealized_plpc", 0)) * 100

            result.append(
                PositionInfo(
                    symbol=symbol,
                    qty=qty,
                    avg_entry=avg_entry,
                    current_price=current_price,
                    market_value=market_value,
                    unrealized_pl=unrealized_pl,
                    unrealized_pl_pct=unrealized_plpc,
                )
            )

        return result

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        return []


def get_position_details(
    symbol: str,
    account_monitor: Any,
    trading_cycle: Any = None,
) -> Optional[PositionInfo]:
    """
    Get detailed info for a specific position including stop/target levels.

    Args:
        symbol: Stock ticker symbol
        account_monitor: AlpacaAccountMonitor instance
        trading_cycle: Optional CostEfficientTradeCycle for stop/target data

    Returns:
        PositionInfo with stop/target if available, or None if not found
    """
    if not account_monitor:
        return None

    try:
        positions = account_monitor.get_positions()
        position = next((p for p in positions if p.get("symbol") == symbol.upper()), None)

        if not position:
            return None

        qty = int(position.get("qty", 0))
        avg_entry = float(position.get("avg_entry_price", 0))

        # Calculate current price from market value
        market_value = float(position.get("market_value", 0))
        current_price = (market_value / qty) if qty > 0 else 0.0

        # Use cost_basis as fallback
        if avg_entry == 0.0:
            cost_basis = float(position.get("cost_basis", 0))
            avg_entry = (cost_basis / qty) if qty > 0 else 0.0

        unrealized_pl = float(position.get("unrealized_pl", 0))
        unrealized_plpc = float(position.get("unrealized_plpc", 0)) * 100

        # Get stop/target from trading_cycle local state
        stop_price = None
        target_price = None

        if trading_cycle:
            local_pos = trading_cycle.local_state.get("positions", {}).get(symbol.upper())
            if local_pos:
                stop_price = local_pos.get("stop_price")
                target_price = local_pos.get("target_price")

        return PositionInfo(
            symbol=symbol.upper(),
            qty=qty,
            avg_entry=avg_entry,
            current_price=current_price,
            market_value=market_value,
            unrealized_pl=unrealized_pl,
            unrealized_pl_pct=unrealized_plpc,
            stop_price=stop_price,
            target_price=target_price,
        )

    except Exception as e:
        logger.error(f"Error getting position details for {symbol}: {e}", exc_info=True)
        return None


# =============================================================================
# Display Formatting Functions
# =============================================================================


def format_account_display(account: AccountInfo) -> str:
    """
    Format account info for CLI display.

    Args:
        account: AccountInfo dataclass

    Returns:
        Formatted string for CLI output
    """
    return MSG.ACCOUNT_INFO(
        equity=account.equity,
        cash=account.cash,
        buying_power=account.buying_power,
        pdt=account.pattern_day_trader,
    )


def format_position_item(position: PositionInfo) -> str:
    """
    Format a single position for list display.

    Args:
        position: PositionInfo dataclass

    Returns:
        Formatted string for CLI output
    """
    pl_emoji = get_pl_emoji(position.unrealized_pl)
    return MSG.POSITION_ITEM(
        emoji=pl_emoji,
        symbol=position.symbol,
        qty=position.qty,
        entry=position.avg_entry,
        current=position.current_price,
        value=position.market_value,
        pl=position.unrealized_pl,
        pl_pct=position.unrealized_pl_pct,
    )


def format_position_details(
    position: PositionInfo,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.08,
) -> str:
    """
    Format detailed position info including stop/target levels.

    Args:
        position: PositionInfo dataclass with stop/target
        stop_loss_pct: Configured stop loss percentage (default 5%)
        take_profit_pct: Configured take profit percentage (default 8%)

    Returns:
        Formatted multi-line string for CLI output
    """
    lines = []

    pl_emoji = get_pl_emoji(position.unrealized_pl)
    lines.append(MSG.POSITION_DETAILS_HEADER(emoji=pl_emoji, symbol=position.symbol))
    lines.append(
        MSG.POSITION_DETAILS(
            qty=position.qty,
            entry=position.avg_entry,
            current=position.current_price,
            pl=position.unrealized_pl,
            pl_pct=position.unrealized_pl_pct,
        )
    )

    # Add exit levels if available
    lines.append(f"\n{get_symbol('TARGET')} Exit Levels:")

    if position.stop_price:
        distance = ((position.current_price - position.stop_price) / position.current_price) * 100
        lines.append(
            f"   {get_symbol('RED')} Stop Loss: ${position.stop_price:.2f} "
            f"(-{stop_loss_pct * 100:.0f}% from entry, {distance:+.1f}% away)"
        )
    else:
        lines.append(f"   {get_symbol('RED')} Stop Loss: Not set")

    if position.target_price:
        distance = ((position.target_price - position.current_price) / position.current_price) * 100
        lines.append(
            f"   {get_symbol('GREEN')} Take Profit: ${position.target_price:.2f} "
            f"(+{take_profit_pct * 100:.0f}% from entry, {distance:+.1f}% away)"
        )
    else:
        lines.append(f"   {get_symbol('GREEN')} Take Profit: Not set")

    # Add note about Alpaca limitations
    if position.stop_price and not position.target_price:
        lines.append(
            f"\n   {get_symbol('INFO')} Note: Stop calculated from entry (Alpaca hides bracket order legs)"
        )
        lines.append("      Verify stop order exists on Alpaca dashboard")

    return "\n".join(lines)


# =============================================================================
# High-Level Portfolio Display Function
# =============================================================================


def show_portfolio(
    account_monitor: Any,
    trading_cycle: Any = None,
    specific_ticker: Optional[str] = None,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.08,
) -> str:
    """
    Show portfolio status with account and positions.

    This is the main entry point for portfolio display, handling:
    - Full portfolio view (account + all positions)
    - Specific ticker details with stop/target

    Args:
        account_monitor: AlpacaAccountMonitor instance
        trading_cycle: Optional CostEfficientTradeCycle for stop/target
        specific_ticker: Optional ticker for detailed view
        stop_loss_pct: Configured stop loss percentage
        take_profit_pct: Configured take profit percentage

    Returns:
        Formatted portfolio display string
    """
    lines = [MSG.PORTFOLIO_HEADER]

    if not account_monitor:
        lines.append(MSG.PORTFOLIO_NOT_INITIALIZED)
        return "\n".join(lines)

    try:
        if specific_ticker:
            # Show details for specific position
            position = get_position_details(specific_ticker, account_monitor, trading_cycle)

            if position:
                lines.append(format_position_details(position, stop_loss_pct, take_profit_pct))
            else:
                lines.append(MSG.NO_POSITION(ticker=specific_ticker))
        else:
            # Full portfolio view
            account = get_account_status(account_monitor)
            if account:
                lines.append(MSG.ACCOUNT_HEADER)
                lines.append(format_account_display(account))

            positions = get_positions_list(account_monitor)
            if positions:
                lines.append(MSG.POSITIONS_HEADER(count=len(positions)))
                for pos in positions:
                    lines.append(format_position_item(pos))
            else:
                lines.append(MSG.NO_POSITIONS)

    except Exception as e:
        lines.append(MSG.ERROR_CHECKING_PORTFOLIO(error=e))
        logger.error(f"Portfolio display error: {e}", exc_info=True)

    return "\n".join(lines)


# =============================================================================
# FunctionTool Wrappers for AutoGen Integration
# =============================================================================


def _show_portfolio_from_broker(ticker: Optional[str] = None) -> str:
    """Show the portfolio from a fresh paper trade cycle: what an agent has to go on."""
    try:
        from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle

        cycle = CostEfficientTradeCycle()
        return show_portfolio(cycle.account_monitor, cycle, specific_ticker=ticker)
    except Exception as e:
        logger.error(f"Could not show the portfolio: {e}", exc_info=True)
        return f"Could not read the portfolio: {type(e).__name__}"


def _show_portfolio_tool(ticker: Optional[str] = None) -> str:
    """
    FunctionTool wrapper for portfolio display.

    Args:
        ticker: Optional specific ticker to show details for

    Returns:
        Portfolio display string or error message
    """
    return _show_portfolio_from_broker(ticker)


def _get_position_tool(ticker: str) -> str:
    """
    FunctionTool wrapper for position lookup.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Position details or not found message
    """
    return _show_portfolio_from_broker(ticker.upper())


# =============================================================================
# Tool Registration
# =============================================================================


# Create FunctionTool instances for AutoGen integration
show_portfolio_func_tool = FunctionTool(
    func=_show_portfolio_tool,
    name="show_portfolio",
    description="Display current portfolio status including account equity, cash, buying power, and all positions with P/L.",
)

get_position_func_tool = FunctionTool(
    func=_get_position_tool,
    name="get_position",
    description="Get detailed information about a specific position including entry price, current price, P/L, and stop/target levels.",
)

# Export list for CLI tools registry
CLI_PORTFOLIO_TOOLS = [
    show_portfolio_func_tool,
    get_position_func_tool,
]

__all__ = [
    # Data classes
    "PositionInfo",
    "AccountInfo",
    "PortfolioSummary",
    # Core functions
    "get_account_status",
    "get_positions_list",
    "get_position_details",
    # Display functions
    "format_account_display",
    "format_position_item",
    "format_position_details",
    "show_portfolio",
    # FunctionTools
    "CLI_PORTFOLIO_TOOLS",
    "show_portfolio_func_tool",
    "get_position_func_tool",
]
