"""
GTT Trailing Stop Bridge - Multi-day trailing stop persistence.

Issue #340, Phase 3: Bridge TrailingStopManager with GTT persistence.

Enables trailing stops to survive across sessions (days/weeks) for swing trades.

Functions:
- sync_trailing_stop_to_gtt: Save position's trailing stop state to GTT
- restore_trailing_stops_from_gtt: Restore state into TrailingStopManager on startup
- sync_all_trailing_stops: Batch sync all active trailing stops
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional

from src.trading.gtt.gtt_manager import ActionType, ConditionType, GTTTrigger, get_gtt_manager

if TYPE_CHECKING:
    from src.trading.orders.trailing_stop_manager import StopState, TrailingStopManager

logger = logging.getLogger(__name__)


def sync_trailing_stop_to_gtt(
    symbol: str,
    stop_state: "StopState",
    expiration_days: Optional[int] = 90,
) -> Optional[GTTTrigger]:
    """
    Sync a trailing stop position to GTT persistence.

    Creates or updates a GTT TRAILING_STOP trigger with the current state,
    allowing it to persist across sessions.

    Args:
        symbol: Stock symbol
        stop_state: Current StopState from TrailingStopManager
        expiration_days: Days until GTT trigger expires (default: 90)

    Returns:
        GTTTrigger if created/updated, None on failure
    """
    gtt_manager = get_gtt_manager()

    # Check if we already have a GTT trigger for this symbol's trailing stop
    existing = _find_trailing_stop_trigger(symbol)

    # Calculate trail percentage from stop state
    # trail_pct = (highest - current_stop) / highest
    if stop_state.highest_price_seen <= 0:
        logger.warning("Invalid highest_price_seen for %s, skipping sync", symbol)
        return None

    highest = stop_state.highest_price_seen
    trail_pct = (highest - stop_state.current_stop) / highest

    # Build action config with full trailing stop state
    action_config = {
        "entry_price": stop_state.entry_price,
        "highest_price": stop_state.highest_price_seen,
        "current_stop": stop_state.current_stop,
        "quantity": stop_state.quantity,
        "stop_order_id": stop_state.stop_order_id,
        "in_profit_zone": stop_state.in_profit_zone,
        "adjustments_count": stop_state.adjustments_count,
        # Order details for when trailing stop triggers
        "order_type": "market",
        "side": "sell",
        "qty": stop_state.quantity,
    }

    # Include voter signal if active
    if stop_state.voter_signal:
        action_config["voter_signal"] = stop_state.voter_signal
        action_config["voter_confidence"] = stop_state.voter_confidence
        action_config["voter_tightening_active"] = stop_state.voter_tightening_active

    # Include S/R levels if set
    if stop_state.support_level is not None:
        action_config["support_level"] = stop_state.support_level
        action_config["resistance_level"] = stop_state.resistance_level

    if existing:
        # Update existing trigger
        logger.info(
            "Syncing trailing stop to GTT %d: %s highest=$%.2f, stop=$%.2f",
            existing.id,
            symbol,
            highest,
            stop_state.current_stop,
        )

        gtt_manager.update_trigger(existing.id, action_config=action_config)

        # Also update via dedicated method
        gtt_manager.update_trailing_stop(
            existing.id,
            new_highest=highest,
            new_stop=stop_state.current_stop,
        )

        # A trigger synced twice a day never lapses; otherwise it expires on the day it
        # was created plus expiration_days, and restore skips it from then on
        if expiration_days:
            gtt_manager.extend_expiration(existing.id, expiration_days)

        return gtt_manager.get_trigger(existing.id)

    # Create new GTT trailing stop trigger
    trigger = gtt_manager.create_trigger(
        symbol=symbol,
        condition_type=ConditionType.TRAILING_STOP,
        trigger_value=trail_pct,  # Trail percentage
        action_type=ActionType.PLACE_ORDER,
        action_config=action_config,
        expiration_days=expiration_days,
        max_triggers=1,  # Fire once (sells the position)
        notes=f"Multi-day trailing stop: {trail_pct * 100:.1f}% from peak",
    )

    if trigger:
        logger.info(
            "Created GTT trailing stop %d: %s highest=$%.2f, stop=$%.2f",
            trigger.id,
            symbol,
            highest,
            stop_state.current_stop,
        )

    return trigger


def restore_trailing_stops_from_gtt(
    trailing_stop_manager: "TrailingStopManager",
) -> Dict[str, Any]:
    """
    Restore trailing stop state from GTT persistence into TrailingStopManager.

    Called on system startup to restore multi-day trailing stops.

    Args:
        trailing_stop_manager: TrailingStopManager instance to populate

    Returns:
        Dict with restoration summary
    """
    gtt_manager = get_gtt_manager()
    restored = 0
    skipped = 0
    errors = 0

    # Get all active TRAILING_STOP triggers
    all_triggers = gtt_manager.get_triggers(active_only=True)
    trailing_triggers = [
        t for t in all_triggers if t.condition_type == ConditionType.TRAILING_STOP.value
    ]

    logger.info("Restoring %d trailing stops from GTT", len(trailing_triggers))

    for trigger in trailing_triggers:
        try:
            config = trigger.action_config or {}

            # Skip if already registered (position still active in manager)
            if trigger.symbol in trailing_stop_manager.stop_states:
                logger.debug("%s already registered, skipping restore", trigger.symbol)
                skipped += 1
                continue

            # Extract state from GTT config
            entry_price = config.get("entry_price", 0)
            current_stop = config.get("current_stop", 0)
            quantity = config.get("quantity", 0)
            stop_order_id = config.get("stop_order_id")

            if not all([entry_price, current_stop, quantity]):
                logger.warning("Incomplete GTT trailing stop config for %s", trigger.symbol)
                skipped += 1
                continue

            # Register position in TrailingStopManager
            state = trailing_stop_manager.register_position(
                symbol=trigger.symbol,
                entry_price=entry_price,
                initial_stop=current_stop,
                quantity=quantity,
                stop_order_id=stop_order_id,
            )

            # Restore additional state
            state.highest_price_seen = config.get("highest_price", entry_price)
            state.in_profit_zone = config.get("in_profit_zone", False)
            state.adjustments_count = config.get("adjustments_count", 0)

            # Restore voter signal if present
            if "voter_signal" in config:
                state.voter_signal = config["voter_signal"]
                state.voter_confidence = config.get("voter_confidence", 0)
                state.voter_tightening_active = config.get("voter_tightening_active", False)

            # Restore S/R levels if present
            if "support_level" in config:
                state.support_level = config["support_level"]
                state.resistance_level = config.get("resistance_level")

            logger.info(
                "Restored trailing stop for %s: highest=$%.2f, stop=$%.2f",
                trigger.symbol,
                state.highest_price_seen,
                state.current_stop,
            )
            restored += 1

        except Exception as e:
            logger.error("Failed to restore trailing stop for %s: %s", trigger.symbol, e)
            errors += 1

    result = {
        "restored": restored,
        "skipped": skipped,
        "errors": errors,
        "total_processed": len(trailing_triggers),
    }

    logger.info("Trailing stop restoration complete: %s", result)
    return result


def sync_all_trailing_stops(
    trailing_stop_manager: "TrailingStopManager",
    expiration_days: Optional[int] = 90,
) -> Dict[str, Any]:
    """
    Sync all active trailing stops to GTT persistence.

    Called by scheduler to persist state for multi-day survival.

    Args:
        trailing_stop_manager: TrailingStopManager with active stops
        expiration_days: Days until GTT triggers expire

    Returns:
        Dict with sync summary
    """
    synced = 0
    errors = 0

    stop_states = trailing_stop_manager.get_all_states()

    logger.info("Syncing %d trailing stops to GTT", len(stop_states))

    for symbol, state in stop_states.items():
        try:
            trigger = sync_trailing_stop_to_gtt(
                symbol=symbol,
                stop_state=state,
                expiration_days=expiration_days,
            )

            if trigger:
                synced += 1
            else:
                errors += 1

        except Exception as e:
            logger.error("Failed to sync trailing stop for %s: %s", symbol, e)
            errors += 1

    result = {
        "synced": synced,
        "errors": errors,
        "total_positions": len(stop_states),
    }

    logger.info("Trailing stop sync complete: %s", result)
    return result


def cleanup_orphaned_trailing_stops(
    active_symbols: Iterable[str],
) -> Dict[str, Any]:
    """
    Delete the trailing-stop state saved for symbols no longer held (#634), so a later
    purchase of the symbol starts afresh. Called by each routine.

    Trailing triggers a user created (/gtt create ... trailing) hold no saved state and
    stay, as do fired or expired ones for a held symbol, whose row sync reuses.

    Args:
        active_symbols: Symbols currently held

    Returns:
        Dict with how many triggers were removed
    """
    gtt_manager = get_gtt_manager()
    active_set = {s.upper() for s in active_symbols}
    removed = 0

    for trigger in gtt_manager.get_triggers(enabled_only=False, active_only=False):
        if (
            trigger.condition_type == ConditionType.TRAILING_STOP.value
            and trigger.symbol not in active_set
            and "current_stop" in (trigger.action_config or {})
            and gtt_manager.delete_trigger(trigger.id)
        ):
            logger.info("Removed GTT trailing stop %d for %s: not held", trigger.id, trigger.symbol)
            removed += 1

    return {"removed": removed}


def get_trailing_stop_status(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get GTT trailing stop status for a symbol.

    Args:
        symbol: Stock symbol

    Returns:
        Dict with status or None if not found
    """
    trigger = _find_trailing_stop_trigger(symbol)

    if not trigger:
        return None

    config = trigger.action_config or {}

    return {
        "trigger_id": trigger.id,
        "symbol": trigger.symbol,
        "trail_pct": trigger.trigger_value * 100,  # As percentage
        "highest_price": config.get("highest_price", 0),
        "current_stop": config.get("current_stop", 0),
        "entry_price": config.get("entry_price", 0),
        "quantity": config.get("quantity", 0),
        "in_profit_zone": config.get("in_profit_zone", False),
        "adjustments_count": config.get("adjustments_count", 0),
        "is_active": trigger.is_active,
        "expiration_date": trigger.expiration_date,
        "created_at": trigger.created_at,
    }


def _find_trailing_stop_trigger(symbol: str) -> Optional[GTTTrigger]:
    """Find existing TRAILING_STOP trigger for a symbol."""
    gtt_manager = get_gtt_manager()

    triggers = gtt_manager.get_triggers(symbol=symbol.upper(), active_only=False, enabled_only=True)

    for trigger in triggers:
        if trigger.condition_type == ConditionType.TRAILING_STOP.value:
            return trigger

    return None
