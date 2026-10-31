"""
Suggestion Display Utilities - Display formatting for trade suggestions.

Issue #436: Extracted from cli_session.py for better modularity.

This module provides display formatting functions for:
- Trade suggestions
- Position context
- Execution results
"""

from typing import Optional

from config_defaults.message_loader import CLIMessages as MSG  # noqa: N814
from config_defaults.message_loader import get_pl_emoji, get_signal_emoji

from src.cli.commands.timeframe_commands import get_timeframe_commands
from src.core.models import Signal
from src.trading.instruments.timeframe_tools import get_timeframe_display_name


def calc_pct(base: float, value: float) -> float:
    """
    Calculate percentage change between base and value.

    Args:
        base: Base value (e.g., entry price)
        value: Target value (e.g., stop loss or take profit)

    Returns:
        Percentage change as float

    Example:
        >>> calc_pct(100.0, 105.0)
        5.0
        >>> calc_pct(100.0, 95.0)
        -5.0
    """
    if base == 0:
        return 0.0
    return ((value - base) / base) * 100.0


def get_trade_direction(signal: Signal, has_position: bool = False) -> str:
    """
    Format trade direction for display.

    Args:
        signal: Trading signal (BUY/SELL/HOLD)
        has_position: Whether user currently holds a position in this ticker

    Returns:
        Formatted direction string:
        - BUY → "BUY (LONG)"
        - SELL with position → "SELL (CLOSE)"
        - SELL without position → "SELL (SHORT)"

    Example:
        >>> get_trade_direction(Signal.BUY)
        'BUY (LONG)'
        >>> get_trade_direction(Signal.SELL, has_position=True)
        'SELL (CLOSE)'
    """
    if signal == Signal.BUY:
        return "BUY (LONG)"
    elif signal == Signal.SELL:
        return "SELL (CLOSE)" if has_position else "SELL (SHORT)"
    else:
        return "HOLD"


def display_position_context(ticker: str, position: Optional[dict]) -> None:
    """
    Display current position context before showing suggestion.

    Args:
        ticker: Stock symbol
        position: Position dict if exists, None otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"📊 Position Context: {ticker}")
    print(f"{'=' * 60}")

    if position:
        # Calculate metrics
        qty = int(position.get("qty", 0))
        avg_entry = float(position.get("avg_entry_price", 0))
        market_value = float(position.get("market_value", 0))
        current_price = (market_value / qty) if qty > 0 else 0.0
        unrealized_pl = float(position.get("unrealized_pl", 0))
        unrealized_plpc = float(position.get("unrealized_plpc", 0)) * 100

        # Use fallback for entry price if needed
        if avg_entry == 0.0:
            cost_basis = float(position.get("cost_basis", 0))
            avg_entry = (cost_basis / qty) if qty > 0 else 0.0

        pl_emoji = get_pl_emoji(unrealized_pl)

        print(f"   Current Position: {qty} shares @ ${avg_entry:.2f} (avg entry)")
        print(f"   Current Price: ${current_price:.2f}")
        print(f"   {pl_emoji} Unrealized P/L: ${unrealized_pl:+.2f} ({unrealized_plpc:+.2f}%)")
        print(f"   Market Value: ${market_value:,.2f}")
    else:
        print(f"   ℹ️  No position in {ticker} (0 shares)")

    print(f"{'=' * 60}\n")


def display_suggestion(  # noqa: C901
    suggestion, position: Optional[dict] = None, override_mode: Optional[str] = None
) -> None:
    """
    Display trade suggestion to user.

    Args:
        suggestion: TradeSuggestion object
        position: Optional position dict for additional context
        override_mode: Optional override indicator ("USER_OVERRIDE_LONG", "USER_OVERRIDE_SHORT")

    Raises:
        ValueError: If suggestion has invalid entry price (except for HOLD signals)
    """
    # Handle HOLD signals - they don't have entry prices and that's OK
    is_hold_signal = suggestion.signal.value.upper() == "HOLD"

    # Validate suggestion has required fields (skip for HOLD)
    if not is_hold_signal and (suggestion.entry_price is None or suggestion.entry_price <= 0):
        raise ValueError(
            f"Invalid entry price for {suggestion.ticker}: {suggestion.entry_price}. "
            "Market data may be unavailable."
        )

    # Get current price for display (use entry_price if available, or fetch current)
    display_price = suggestion.entry_price
    if display_price is None:
        # For HOLD signals, try to get current price from reasoning or use 0
        display_price = getattr(suggestion, "current_price", None) or 0.0

    print("\n" + MSG.SUGGESTION_SEPARATOR)
    if display_price > 0:
        print(MSG.SUGGESTION_HEADER.format(ticker=suggestion.ticker, price=display_price))
    else:
        print(f"📊 {suggestion.ticker}")
    print(MSG.SUGGESTION_SEPARATOR)

    # Issue #347: Respect user intent when signals disagree
    # Signal display prioritizes user's explicit request
    signal_emoji = get_signal_emoji(suggestion.signal.value)

    if override_mode == "USER_OVERRIDE_LONG":
        # User wants BUY but signals say something else
        print("👤 ACTION: ⬆️ BUY (as requested)")
        if suggestion.signal.value.upper() != "BUY":
            print(f"   📊 Signals suggest: {signal_emoji} {suggestion.signal.value.upper()}")
    elif override_mode == "USER_OVERRIDE_SHORT":
        # User wants SELL but signals say something else
        print("👤 ACTION: ⬇️ SELL (as requested)")
        if suggestion.signal.value.upper() != "SELL":
            print(f"   📊 Signals suggest: {signal_emoji} {suggestion.signal.value.upper()}")
    else:
        # No explicit user intent - show signal recommendation
        print(MSG.SIGNAL_DISPLAY.format(emoji=signal_emoji, signal=suggestion.signal.value.upper()))

    print(MSG.CONFIDENCE_DISPLAY.format(confidence=suggestion.confidence))

    # Get current timeframe for display (Issue #365)
    try:
        timeframe = get_timeframe_commands().manager.get_current_timeframe()
        timeframe_display = get_timeframe_display_name(timeframe)
    except Exception:
        timeframe_display = "1 day"  # Fallback to default

    # Technical analysis
    print(MSG.ANALYSIS_HEADER.format(timeframe=timeframe_display))
    for reason in suggestion.reasoning:
        print(MSG.ANALYSIS_ITEM.format(reason=reason))

    # For HOLD signals, show recommendation without entry plan
    if is_hold_signal:
        print("\n💡 Recommendation:")
        print("   ⏸️  HOLD - No action recommended at this time")
        print("   📊 Indicators do not show a clear signal")
        if suggestion.warnings:
            print("\n⚠️  Notes:")
            for warning in suggestion.warnings:
                print(f"   • {warning}")
        return  # Skip entry plan and portfolio impact for HOLD

    # Determine trade direction (CLOSE if has position, SHORT if not)
    direction = get_trade_direction(suggestion.signal, has_position=bool(position))

    # Entry plan
    print(MSG.ENTRY_PLAN_HEADER)
    print(
        MSG.ENTRY_PLAN.format(
            direction=direction,
            entry=suggestion.entry_price,
            stop=suggestion.stop_loss,
            stop_pct=calc_pct(suggestion.entry_price, suggestion.stop_loss),
            target=suggestion.take_profit,
            target_pct=calc_pct(suggestion.entry_price, suggestion.take_profit),
            qty=suggestion.recommended_quantity,
            tif=suggestion.time_in_force.value.upper(),
        )
    )

    # Portfolio impact
    print(MSG.PORTFOLIO_IMPACT_HEADER)
    print(
        MSG.PORTFOLIO_IMPACT.format(
            trade_value=suggestion.recommended_quantity * suggestion.entry_price,
            portfolio_pct=suggestion.portfolio_pct,
            max_loss=suggestion.max_loss_usd,
            risk_reward=suggestion.risk_reward_ratio,
        )
    )

    # Warnings
    if suggestion.warnings:
        print(MSG.WARNINGS_HEADER)
        for warning in suggestion.warnings:
            print(MSG.WARNING_ITEM.format(warning=warning))


def display_result(result) -> None:
    """
    Display execution result.

    Args:
        result: OrderResult object with success, message, order IDs, etc.
    """
    print("\n" + MSG.RESULT_SEPARATOR)

    if result.success:
        print(MSG.ORDER_SUCCESS_HEADER)
        print(
            MSG.ORDER_SUCCESS.format(
                qty=result.quantity,
                ticker=result.ticker,
                entry_id=result.entry_order_id,
                # A sell exit has no bracket legs
                stop_id=result.stop_order_id or "none",
                target_id=result.target_order_id or "none",
                message=result.message,
            )
        )
    else:
        print(MSG.ORDER_FAILED_HEADER)
        print(MSG.ORDER_FAILED.format(message=result.message))
        if result.error:
            print(MSG.ORDER_ERROR.format(error=result.error))

    print(MSG.RESULT_SEPARATOR)


__all__ = [
    "calc_pct",
    "get_trade_direction",
    "display_position_context",
    "display_suggestion",
    "display_result",
]
