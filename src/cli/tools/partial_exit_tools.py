"""
Partial Exit CLI Tools for partial exit management.

Issue #508: CLI integration for multi-target position exits.

This module wraps the PartialExitManager functions for the CLI. Plans are read from
the saved state (state/partial_exits.json), so these tools see plans registered by
other processes (#524, #584).

A plan made with `/partial set` has a software target: no broker order, the evening
routine sells it once the price is at the target (#584).
"""

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

NO_PLANS = (
    "[INFO] No partial exit plans registered.\n\n"
    "Plans appear here once a position is registered for partial exits "
    "(/partial set SYMBOL)."
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
    kind = "Software target" if target.exit_type == "software" else "Limit"
    if target.exit_type in ("limit", "software") and target.exit_price:
        entry = state.entry_price
        move = (target.exit_price - entry) / entry * 100 if entry else 0.0
        gain = -move if str(state.side).lower() == "short" else move  # shorts gain below entry
        if target.exit_type == "software":
            live = f", sold: order {target.order_id}" if target.order_id else ""
        else:
            live = f", order {target.order_id}" if target.order_id else ""
        lines.append(
            f"{indent}          {kind} @ ${target.exit_price:.2f} ({gain:+.1f}% gain{live})"
        )
        if target.exit_type == "software" and not target.filled and not target.order_id:
            lines.append(f"{indent}          Sold by the evening routine once the price is there")
    elif target.exit_type in ("limit", "software"):
        lines.append(f"{indent}          {kind} (no price set)")
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
        *([f"  Stop Price: ${state.stop_price:.2f}"] if state.stop_price != 0 else []),
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

    Only unfilled limit or software targets without an order can be changed here; the
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


def _whole_long_shares(symbol: str, positions: Optional[List[Dict[str, Any]]]):
    """
    (position, shares, average entry) of a held long position that can have a plan, or the
    [ERROR] reply saying why not.
    """
    if positions is None:
        return "[ERROR] Could not read the positions from the broker: no plan registered"
    position = next((p for p in positions if str(p.get("symbol", "")).upper() == symbol), None)
    if position is None:
        return f"[ERROR] {symbol} is not held: a partial exit plan needs an open long position"
    qty = float(position.get("qty") or 0)
    if qty < 0 or "short" in str(position.get("side", "")).lower():
        return f"[ERROR] {symbol} is a short position: partial exit plans are for longs only"
    if qty != int(qty):
        return f"[ERROR] {symbol} has {qty} shares: a plan needs a whole number of shares"
    if int(qty) < 2:
        return f"[ERROR] {symbol} has {int(qty)} share: a partial exit needs at least 2"
    entry = float(position.get("avg_entry_price") or 0)
    if entry <= 0:
        return f"[ERROR] {symbol} has no average entry price from the broker"
    return position, int(qty), entry


def set_partial_plan(
    symbol: str,
    positions: Optional[List[Dict[str, Any]]],
    confirm: Callable[[str], bool],
) -> str:
    """
    Register a held long position for a software partial exit (#584).

    Target 1 is half the shares (rounded down) at the average entry plus the trading
    mode's target_1_pct; the rest stays on the trailing stop. Nothing is sent to the
    broker: the evening routine sells the shares once the price is at the target.

    Args:
        symbol: Trading symbol
        positions: The broker's positions (AlpacaAccountMonitor.get_positions()), or None
            if they could not be read
        confirm: Asks a question, True if the user typed yes (used when the price is
            already at the target)

    Returns:
        What was registered, or why not
    """
    symbol = symbol.upper()
    held = _whole_long_shares(symbol, positions)
    if isinstance(held, str):
        return held
    position, shares, entry = held

    try:
        from src.core.trading_modes import get_mode_manager

        config = get_mode_manager().get_partial_exit_config_dict()
    except Exception as e:
        return _error("read the trading mode's partial exit settings", e)
    if not config.get("enabled"):
        return "[ERROR] Partial exits are switched off in the current trading mode"
    target_pct = float(config["target_1_pct"])

    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"
    if symbol in manager.exit_states:
        return f"[ERROR] {symbol} already has a plan: /partial remove {symbol} first"

    target_price = entry * (1 + target_pct)
    market_value = float(position.get("market_value") or 0)
    price = market_value / shares if market_value else 0.0
    if price and price >= target_price:
        question = (
            f"{symbol} is at about ${price:.2f}, already at or past its ${target_price:.2f} "
            f"target: the next evening routine would sell {shares // 2} share(s)."
        )
        if not confirm(question):
            return f"[INFO] No plan registered for {symbol}"

    try:
        state = manager.register_software_plan(symbol, entry, shares, target_1_pct=target_pct)
    except Exception as e:
        return _error(f"register a plan for {symbol}", e)
    target = state.targets[0]
    return "\n".join(
        [
            f"[OK] Partial exit plan registered for {symbol} ({shares} shares @ ${entry:.2f})",
            f"  Target 1: sell {target.quantity} at ${target.exit_price:.2f} "
            f"({target_pct * 100:.1f}% above entry)",
            f"  The other {shares - target.quantity} stay on the trailing stop",
            "  No order was sent: the evening routine sells target 1 at market once the price "
            "is there, while the market is open",
        ]
    )


def remove_partial_plan(symbol: str, confirm: Callable[[str], bool]) -> str:
    """Drop a symbol's saved plan after the user confirms. No order is changed."""
    symbol = symbol.upper()
    manager = _get_partial_exit_manager()
    if not manager:
        return "[ERROR] Could not access PartialExitManager"
    if symbol not in manager.exit_states:
        return f"[INFO] No partial exit plan for {symbol}"
    if not confirm(f"Remove the partial exit plan for {symbol}? Orders at the broker stay."):
        return f"[INFO] Plan for {symbol} kept"
    manager.remove_position(symbol)
    return f"[OK] Removed the partial exit plan for {symbol}"


__all__ = [
    "show_all_partial_exits",
    "show_partial_exit_plan",
    "show_exit_targets",
    "list_active_exits",
    "get_exit_summary",
    "modify_exit_target",
    "set_partial_plan",
    "remove_partial_plan",
]
