"""
Partial Exit CLI Tools for partial exit management.

Issue #508: CLI integration for multi-target position exits.

This module wraps the PartialExitManager functions for the CLI. Plans are read from
the saved state (state/partial_exits.json), so these tools see plans registered by
other processes (#524, #584).

"""

import logging
from typing import List

logger = logging.getLogger(__name__)

NO_PLANS = (
    "[INFO] No partial exit plans registered.\n\n"
    "Plans appear here once a position is registered for partial exits."
)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_partial_exit_manager():
    """PartialExitManager over the saved plans (None if they can't be read)."""
    try:
        from src.trading.orders.partial_exit_manager import load_partial_exit_manager

        return load_partial_exit_manager()
    except Exception as e:
        logger.error(f"Failed to get PartialExitManager: {e}")
        return None


def _target_lines(target, state, indent: str = "    ") -> List[str]:
    """Display lines for one exit target of a plan."""
    status = "[FILLED]" if target.filled else "[ACTIVE]"
    lines = [f"{indent}{status} Target {target.target_number}: {target.quantity} shares"]
    if target.exit_type == "limit" and target.exit_price:
        entry = state.entry_price
        move = (target.exit_price - entry) / entry * 100 if entry else 0.0
        gain = -move if str(state.side).lower() == "short" else move  # shorts gain below entry
        live = f", order {target.order_id}" if target.order_id else ""
        lines.append(
            f"{indent}          Limit @ ${target.exit_price:.2f} ({gain:+.1f}% gain{live})"
        )
    elif target.exit_type == "limit":
        lines.append(f"{indent}          Limit (no price set)")
    else:
        lines.append(f"{indent}          Trailing Stop")
    if target.filled_at:
        lines.append(f"{indent}          Filled: {target.filled_at}")
    return lines


def _plan_lines(state) -> List[str]:
    """Display lines for one position's plan."""
    lines = [
        f"[{state.symbol}] {state.total_quantity} shares @ ${state.entry_price:.2f}",
        f"  Registered: {state.registered_at}",
        f"  Remaining: {state.get_remaining_quantity()} shares",
        f"  Stop Price: ${state.stop_price:.2f}",
        "  Targets:",
    ]
    for target in state.targets:
        lines.extend(_target_lines(target, state))
    return lines


def _error(action: str, error: Exception) -> str:
    """An [ERROR] reply for a view that failed (e.g. a malformed saved plan)."""
    logger.error(f"Failed to {action}: {error}")
    return f"[ERROR] Failed to {action}: {error}"


def show_all_partial_exits() -> str:
    """
    Show all registered partial exit plans.

    Returns:
        Formatted string with all partial exit positions

    Example:
        >>> show_all_partial_exits()
        'Partial Exit Plans
        [AAPL] 100 shares @ $150.00
          ...'
    """
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"
    if not manager.exit_states:
        return NO_PLANS

    try:
        lines = ["Partial Exit Plans", "=" * 60, ""]
        for state in manager.exit_states.values():
            lines.extend(_plan_lines(state))
            lines.extend(["-" * 60, ""])
        return "\n".join(lines)
    except Exception as e:
        return _error("retrieve partial exits", e)


def show_partial_exit_plan(symbol: str) -> str:
    """
    Show partial exit plan for a specific symbol.

    Args:
        symbol: Trading symbol (e.g., 'AAPL')

    Returns:
        Formatted string with exit plan details
    """
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"

    symbol = symbol.upper()
    state = manager.exit_states.get(symbol)
    if state is None:
        return f"[INFO] No partial exit plan for {symbol}"
    try:
        return "\n".join([f"Partial Exit Plan: {symbol}", "=" * 50, *_plan_lines(state)])
    except Exception as e:
        return _error(f"retrieve exit plan for {symbol}", e)


def show_exit_targets(symbol: str) -> str:
    """
    Show exit target levels for a position.

    Args:
        symbol: Trading symbol

    Returns:
        Formatted string with target levels
    """
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"

    symbol = symbol.upper()
    state = manager.exit_states.get(symbol)
    if state is None:
        return f"[INFO] No targets configured for {symbol}"

    try:
        lines = [f"Exit Targets: {symbol}", "=" * 50]
        for target in state.targets:
            lines.extend(_target_lines(target, state, indent=""))
        return "\n".join(lines)
    except Exception as e:
        return _error(f"retrieve targets for {symbol}", e)


def list_active_exits() -> str:
    """
    List only active (unfilled) exit targets.

    Returns:
        Formatted string with active targets
    """
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"

    try:
        blocks = []
        for symbol, state in manager.exit_states.items():
            active = state.get_active_targets()
            if not active:
                continue
            blocks.append(
                f"[{symbol}] {len(active)} target(s), "
                f"{state.get_remaining_quantity()} shares remaining"
            )
            for target in active:
                blocks.extend(_target_lines(target, state, indent="  "))
            blocks.append("")
    except Exception as e:
        return _error("list active exits", e)

    if not blocks:
        return "[INFO] No active exit targets.\n\nAll targets have been filled or no plans exist."
    return "\n".join(["Active Exit Targets", "=" * 60, "", *blocks])


def get_exit_summary() -> str:
    """
    Get summary of all partial exit positions.

    Returns:
        Summary statistics
    """
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"
    if not manager.exit_states:
        return "[INFO] No partial exit positions registered."

    states = manager.exit_states.values()
    try:
        return "\n".join(
            [
                "Partial Exit Summary",
                "=" * 50,
                f"Total Positions: {len(manager.exit_states)}",
                f"Active Targets: {sum(len(s.get_active_targets()) for s in states)}",
                f"Filled Targets: {sum(len(s.get_filled_targets()) for s in states)}",
                f"Shares Remaining: {sum(s.get_remaining_quantity() for s in states)}",
            ]
        )
    except Exception as e:
        return _error("generate summary", e)


def modify_exit_target(symbol: str, target_number: int, new_price: float) -> str:
    """
    Modify the exit price of a partial exit target.

    Only unfilled limit targets without a live broker order can be changed here; the
    change is saved.

    Args:
        symbol: Stock ticker symbol (e.g., "AAPL")
        target_number: Target number to modify (1, 2, etc.)
        new_price: New exit price

    Returns:
        Success or error message

    Example:
        >>> modify_exit_target("AAPL", 1, 205.50)
        '[OK] Modified AAPL target 1 ...'
    """
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"

    try:
        changed, message = manager.update_target_price(symbol.upper(), target_number, new_price)
    except Exception as e:
        logger.error(f"Error modifying exit target: {e}")
        return f"[ERROR] Failed to modify target: {e}"
    return f"[OK] {message}" if changed else f"[ERROR] {message}"


__all__ = [
    "show_all_partial_exits",
    "show_partial_exit_plan",
    "show_exit_targets",
    "list_active_exits",
    "get_exit_summary",
    "modify_exit_target",
]
