#!/usr/bin/env python3
"""
Partial Exit Manager - Multi-Target Position Exits

Issue #248: Implement Partial Position Exits

Manages positions with multiple exit targets:
- Target 1: Partial profit-taking at fixed profit level (e.g., 4-6%), a broker limit order
  or a software target the evening routine sells (#584)
- Target 2: Trailing stop for remaining position (integrates with #414)

Design:
- Default 50/50 split between targets
- Target 1 uses limit order at profit percentage
- Target 2 leverages TrailingStopManager for dynamic stops
- Per-mode configuration via trading_modes.yaml
"""

import json
import logging
import math
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.date_utils import format_for_filename, now_iso
from src.utils.state_paths import get_state_dir

try:
    import fcntl
except ImportError:  # Windows: no cross-process lock, writes are still atomic
    fcntl = None

logger = logging.getLogger(__name__)


@dataclass
class ExitTarget:
    """Represents a single exit target within a partial exit strategy."""

    target_number: int  # 1, 2, 3, etc.
    quantity: int  # Shares for this target
    ratio: float  # Percentage of total position (0.0 - 1.0)
    exit_type: str  # "limit" (a broker order), "software" (sold by the routine) or "trailing"
    exit_price: Optional[float] = None  # For limit and software targets
    order_id: Optional[str] = None  # Broker order ID (a software target: its sale)
    filled: bool = False  # Whether this target has been hit
    filled_at: Optional[str] = None  # ISO timestamp of fill


@dataclass
class PartialExitState:
    """Track partial exit state for a position."""

    symbol: str
    entry_price: float
    total_quantity: int  # Original position size
    targets: List[ExitTarget]
    stop_price: float  # Initial stop price
    side: str = "long"  # "long" or "short"
    stop_order_id: Optional[str] = None  # Stop order for remaining position
    registered_at: str = None  # ISO timestamp
    last_updated: str = None

    def __post_init__(self):
        """Initialize timestamps if not provided."""
        if self.registered_at is None:
            self.registered_at = now_iso()
        if self.last_updated is None:
            self.last_updated = now_iso()

    def get_remaining_quantity(self) -> int:
        """Calculate quantity remaining after filled targets."""
        filled_qty = sum(target.quantity for target in self.targets if target.filled)
        return self.total_quantity - filled_qty

    def get_filled_targets(self) -> List[ExitTarget]:
        """Get list of targets that have been filled."""
        return [target for target in self.targets if target.filled]

    def get_active_targets(self) -> List[ExitTarget]:
        """Get list of targets that are still active."""
        return [target for target in self.targets if not target.filled]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartialExitState":
        """Rebuild a state saved with dataclasses.asdict() (unknown keys are ignored)."""
        target_fields = {f.name for f in fields(ExitTarget)}
        targets = [
            ExitTarget(**{k: v for k, v in target.items() if k in target_fields})
            for target in data.get("targets", [])
        ]
        state_fields = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in {**data, "targets": targets}.items() if k in state_fields})


def default_state_file() -> Path:
    """Where partial exit plans are saved (honours AUTOTRADER_STATE_DIR)."""
    return get_state_dir() / "partial_exits.json"


class PartialExitManager:
    """
    Manages multi-target position exits.

    Issue #248: Implement Partial Position Exits

    Coordinates with TrailingStopManager to provide:
    - Partial profit-taking at fixed levels
    - Trailing stops for remaining position
    - Position state tracking across partial fills
    """

    def __init__(
        self,
        order_manager,
        trailing_stop_manager=None,
        partial_exit_config: Optional[Dict[str, Any]] = None,
        state_file: Optional[Path] = None,
    ):
        """
        Initialize partial exit manager.

        Args:
            order_manager: OrderManager instance for broker operations
            trailing_stop_manager: TrailingStopManager for dynamic stops
            partial_exit_config: Configuration dict for partial exits
            state_file: JSON file plans are loaded from and saved to (None: memory only)
        """
        self.order_manager = order_manager
        self.trailing_stop_manager = trailing_stop_manager

        # Default configuration if not provided
        self.config = partial_exit_config or {
            "enabled": True,
            "targets": 2,
            "split": [0.5, 0.5],
            "target_1_pct": 0.04,  # 4% profit
            "target_2": "trailing",
        }

        # Track exit states for each position (saved so other processes, like the CLI,
        # see the same plans)
        self.state_file = Path(state_file) if state_file else None
        self.exit_states: Dict[str, PartialExitState] = self._load()

        logger.info(
            f"PartialExitManager initialized: enabled={self.config.get('enabled')}, "
            f"targets={self.config.get('targets')}, split={self.config.get('split')}"
        )

    def register_position(
        self,
        symbol: str,
        entry_price: float,
        total_quantity: int,
        stop_price: float,
        stop_order_id: Optional[str] = None,
        side: str = "long",
    ) -> Optional[PartialExitState]:
        """
        Register a new position for partial exit management.

        Args:
            symbol: Ticker symbol
            entry_price: Position entry price
            total_quantity: Total shares/quantity
            stop_price: Initial stop loss price
            stop_order_id: ID of initial stop order (will be replaced)
            side: Position side ("long" or "short")

        Returns:
            PartialExitState if registered, None if disabled/error
        """
        if not self.config.get("enabled", False):
            logger.debug(f"Partial exits disabled, skipping registration for {symbol}")
            return None

        if total_quantity < 2:
            logger.warning(
                f"Position {symbol} too small for partial exits (qty={total_quantity}), "
                "requires minimum 2 shares"
            )
            return None

        # Calculate exit targets
        targets = self._calculate_targets(symbol, entry_price, total_quantity, stop_price, side)

        # Create exit state
        state = PartialExitState(
            symbol=symbol,
            entry_price=entry_price,
            total_quantity=total_quantity,
            side=side,
            targets=targets,
            stop_price=stop_price,
            stop_order_id=stop_order_id,
        )

        self.exit_states[symbol] = state

        # Place orders for targets
        self._place_target_orders(state)
        self.save(symbol)

        logger.info(
            f"Registered {symbol} for partial exits: "
            f"{len(targets)} targets, total_qty={total_quantity}"
        )

        return state

    def register_software_plan(
        self,
        symbol: str,
        entry_price: float,
        total_quantity: int,
        stop_price: float = 0.0,
        target_1_pct: Optional[float] = None,
    ) -> PartialExitState:
        """
        Save a plan for a long position whose target 1 the evening routine sells (#584).

        Target 1 is a "software" target: no order is placed, and the trailing stop is left
        alone (register_position does both, and is not used here). The routine sells it at
        market once the price is at the target.

        Args:
            symbol: Ticker symbol
            entry_price: The position's average entry
            total_quantity: Shares held (at least 2)
            stop_price: The live stop's level, for display (0 if unknown)
            target_1_pct: Profit for target 1 (default: the configured one)

        Returns:
            The saved plan

        Raises:
            ValueError: Fewer than 2 shares, or no usable entry price
        """
        if total_quantity < 2:
            raise ValueError(
                f"{symbol} has {total_quantity} share(s): a partial exit needs at least 2"
            )
        if not math.isfinite(entry_price) or entry_price <= 0:
            raise ValueError(f"{symbol} has no usable entry price ({entry_price})")

        targets = self._calculate_targets(
            symbol, entry_price, total_quantity, stop_price, "long", target_1_pct
        )
        targets[0].exit_type = "software"
        state = PartialExitState(
            symbol=symbol,
            entry_price=entry_price,
            total_quantity=total_quantity,
            side="long",
            targets=targets,
            stop_price=stop_price,
        )
        self.exit_states[symbol] = state
        self.save(symbol)
        logger.info(
            f"Registered {symbol} for a software partial exit: {targets[0].quantity} of "
            f"{total_quantity} shares at ${targets[0].exit_price:.2f}"
        )
        return state

    def _calculate_targets(
        self,
        symbol: str,
        entry_price: float,
        total_quantity: int,
        stop_price: float,
        side: str = "long",
        target_1_pct: Optional[float] = None,
    ) -> List[ExitTarget]:
        """
        Calculate exit targets based on configuration.

        Args:
            symbol: Ticker symbol
            entry_price: Position entry price
            total_quantity: Total shares
            stop_price: Initial stop price
            target_1_pct: Profit for target 1 (default: the configured one)

        Returns:
            List of ExitTarget objects
        """
        targets = []
        split_ratios = self.config.get("split", [0.5, 0.5])

        # Calculate quantities for each target
        remaining_qty = total_quantity
        for i, ratio in enumerate(split_ratios, start=1):
            # Last target gets all remaining shares (handles rounding)
            if i == len(split_ratios):
                qty = remaining_qty
            else:
                qty = int(total_quantity * ratio)
                remaining_qty -= qty

            # Determine exit type and price
            if i == 1:
                # Target 1: Limit order at profit percentage
                target_pct = (
                    target_1_pct
                    if target_1_pct is not None
                    else self.config.get("target_1_pct", 0.04)
                )
                if side.lower() == "short":
                    # For shorts, profit is below entry
                    exit_price = entry_price * (1 - target_pct)
                else:
                    exit_price = entry_price * (1 + target_pct)
                exit_type = "limit"
            else:
                # Target 2+: Trailing stop (no fixed price)
                exit_price = None
                exit_type = "trailing"

            target = ExitTarget(
                target_number=i,
                quantity=qty,
                ratio=ratio,
                exit_type=exit_type,
                exit_price=exit_price,
            )

            targets.append(target)

        logger.debug(
            f"Calculated targets for {symbol}: "
            f"{[(t.target_number, t.quantity, t.exit_type) for t in targets]}"
        )

        return targets

    def _place_target_orders(self, state: PartialExitState) -> None:
        """
        Place orders for all exit targets.

        Args:
            state: PartialExitState for the position
        """
        for target in state.targets:
            if target.exit_type == "limit":
                # Place limit order for partial profit-taking
                self._place_limit_exit(state, target)
            elif target.exit_type == "trailing":
                # Register with TrailingStopManager
                self._register_trailing_target(state, target)

    def _place_limit_exit(self, state: PartialExitState, target: ExitTarget) -> None:
        """
        Place limit order for a profit target.

        Args:
            state: PartialExitState for the position
            target: ExitTarget to place order for
        """
        try:
            # Use order_manager to place limit sell order
            # Exit side is opposite of position side
            exit_side = "buy" if state.side.lower() == "short" else "sell"

            result = self.order_manager.place_limit_order(
                symbol=state.symbol,
                qty=target.quantity,
                side=exit_side,
                limit_price=target.exit_price,
            )

            if "error" not in result:
                target.order_id = result.get("id")
                logger.info(
                    f"Placed limit exit for {state.symbol} target {target.target_number}: "
                    f"{target.quantity} @ ${target.exit_price:.2f} (ID: {target.order_id})"
                )
            else:
                logger.error(
                    f"Failed to place limit exit for {state.symbol} "
                    f"target {target.target_number}: {result['error']}"
                )

        except Exception as e:
            logger.error(
                f"Error placing limit exit for {state.symbol} target {target.target_number}: {e}"
            )

    def _register_trailing_target(self, state: PartialExitState, target: ExitTarget) -> None:
        """
        Register trailing stop target with TrailingStopManager.

        Args:
            state: PartialExitState for the position
            target: ExitTarget for trailing stop
        """
        if self.trailing_stop_manager is None:
            logger.warning(
                f"TrailingStopManager not available for {state.symbol} "
                f"target {target.target_number}, using fixed stop"
            )
            return

        try:
            # Register with trailing stop manager
            self.trailing_stop_manager.register_position(
                symbol=state.symbol,
                entry_price=state.entry_price,
                initial_stop=state.stop_price,
                quantity=target.quantity,
                stop_order_id=state.stop_order_id,  # Will replace with partial qty
            )

            logger.info(
                f"Registered trailing target for {state.symbol} target {target.target_number}: "
                f"{target.quantity} shares with dynamic stops"
            )

        except Exception as e:
            logger.error(
                f"Error registering trailing target for {state.symbol} "
                f"target {target.target_number}: {e}"
            )

    def update_position_fills(self, symbol: str) -> bool:
        """
        Check if any targets have been filled and update state.

        Args:
            symbol: Ticker symbol to check

        Returns:
            True if any targets were filled, False otherwise
        """
        self._refresh(symbol)  # pick up changes another process saved
        if symbol not in self.exit_states:
            return False

        state = self.exit_states[symbol]
        updated = False

        for target in state.get_active_targets():
            if target.order_id is None:
                continue

            # Check order status from broker
            order_status = self._check_order_status(target.order_id)

            if order_status == "filled":
                target.filled = True
                target.filled_at = now_iso()
                updated = True

                logger.info(
                    f"Target {target.target_number} filled for {symbol}: "
                    f"{target.quantity} @ ${target.exit_price:.2f}"
                )

        if updated:
            state.last_updated = now_iso()
            self.save(symbol)

        return updated

    def _check_order_status(self, order_id: str) -> str:
        """
        Check order status from broker.

        Args:
            order_id: Order ID to check

        Returns:
            Order status string ("filled", "open", "cancelled", etc.)
        """
        try:
            # Use position_manager to get order details
            order = self.order_manager.position_manager.get_order(order_id)
            if order:
                return order.get("status", "unknown")
            return "unknown"
        except Exception as e:
            logger.error(f"Error checking order status for {order_id}: {e}")
            return "error"

    def get_position_summary(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get summary of partial exit state for a position.

        Args:
            symbol: Ticker symbol

        Returns:
            Dict with exit state summary, None if not found
        """
        if symbol not in self.exit_states:
            return None

        state = self.exit_states[symbol]

        return {
            "symbol": symbol,
            "entry_price": state.entry_price,
            "total_quantity": state.total_quantity,
            "remaining_quantity": state.get_remaining_quantity(),
            "targets": [
                {
                    "number": t.target_number,
                    "quantity": t.quantity,
                    "type": t.exit_type,
                    "price": t.exit_price,
                    "filled": t.filled,
                    "filled_at": t.filled_at,
                }
                for t in state.targets
            ],
            "filled_targets": len(state.get_filled_targets()),
            "active_targets": len(state.get_active_targets()),
            "registered_at": state.registered_at,
            "last_updated": state.last_updated,
        }

    def remove_position(self, symbol: str) -> bool:
        """
        Remove position from partial exit tracking (fully closed).

        Args:
            symbol: Ticker symbol

        Returns:
            True if removed, False if not found
        """
        if symbol in self.exit_states:
            del self.exit_states[symbol]
            self.save(symbol)
            logger.info(f"Removed {symbol} from partial exit tracking")
            return True
        return False

    def update_target_price(
        self, symbol: str, target_number: int, new_price: float
    ) -> Tuple[bool, str]:
        """
        Change the exit price of an unfilled limit or software target in a saved plan.

        A target with an order is refused: changing only the stored price would leave the
        broker order at the old one (a software target's order is its sale).

        Returns:
            Tuple of (changed, message)
        """
        self._refresh(symbol)  # edit the latest saved plan
        state = self.exit_states.get(symbol)
        if state is None:
            return False, f"No partial exit plan found for {symbol}"
        target = next((t for t in state.targets if t.target_number == target_number), None)
        if target is None:
            return False, f"Target {target_number} not found for {symbol}"
        if target.filled:
            return False, f"Target {target_number} for {symbol} has already been filled"
        if target.exit_type not in ("limit", "software"):
            return False, f"Target {target_number} is a {target.exit_type} target, not a limit"
        if not math.isfinite(new_price) or new_price <= 0:
            return False, f"Exit price must be a positive number, got {new_price}"
        if target.order_id and target.exit_type == "software":
            return False, (
                f"Target {target_number} for {symbol} has already been sold "
                f"(order {target.order_id})"
            )
        if target.order_id:
            return False, (
                f"Target {target_number} for {symbol} has a live order ({target.order_id}); "
                "change it at the broker, not here"
            )

        old_price = target.exit_price
        target.exit_price = new_price
        state.last_updated = now_iso()
        self.save(symbol)
        previous = f"${old_price:.2f}" if old_price else "none"
        saved = (
            "the evening routine sells it at this price"
            if target.exit_type == "software"
            else "this target has no broker order, so nothing was sent"
        )
        return True, (
            f"Modified {symbol} target {target_number}\n"
            f"  Previous price: {previous}\n"
            f"  New price: ${new_price:.2f}\n"
            f"  Saved in the plan only: {saved}"
        )

    def save(self, symbol: Optional[str] = None) -> None:
        """
        Write plans to the state file, if one is configured.

        With a symbol, only that plan is written (or removed) on top of what is on disk,
        so plans other processes saved are kept. The file is locked while it is read and
        rewritten, and replaced atomically. An unreadable file is never overwritten.
        """
        if self.state_file is None:
            return
        try:
            with self._file_lock():
                if symbol is None:
                    plans = {sym: asdict(state) for sym, state in self.exit_states.items()}
                else:
                    plans = self._read_plans()
                    if symbol in self.exit_states:
                        plans[symbol] = asdict(self.exit_states[symbol])
                    else:
                        plans.pop(symbol, None)
                self._write_plans(plans)
        except Exception as e:
            logger.error(f"Failed to save partial exit plans to {self.state_file}: {e}")

    def _refresh(self, symbol: str) -> None:
        """Replace a plan in memory with its saved version, if the file has one."""
        if self.state_file is None:
            return
        try:
            saved = self._read_plans().get(symbol)
        except Exception as e:
            logger.warning(f"Could not re-read partial exit plans: {e}")
            return
        if saved is not None:
            self.exit_states[symbol] = PartialExitState.from_dict(saved)

    def refresh_plan(self, symbol: str) -> Optional[PartialExitState]:
        """
        A symbol's plan as saved now, or None if another process removed it. Unlike
        _refresh, a plan gone from the file is dropped from memory too. Without a state
        file, the plan in memory.
        """
        if self.state_file is None:
            return self.exit_states.get(symbol)
        try:
            saved = self._read_plans().get(symbol)
        except Exception as e:
            logger.warning(f"Could not re-read partial exit plans: {e}")
            return self.exit_states.get(symbol)
        if saved is None:
            self.exit_states.pop(symbol, None)
            return None
        self.exit_states[symbol] = PartialExitState.from_dict(saved)
        return self.exit_states[symbol]

    def _read_plans(self) -> Dict[str, Dict[str, Any]]:
        """Raw saved plans by symbol ({} if no file); raises if the file is unreadable."""
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text()).get("positions", {})

    def _write_plans(self, plans: Dict[str, Dict[str, Any]]) -> None:
        """Atomically replace the state file (unique temp file, then rename)."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.state_file.parent, prefix=self.state_file.name)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({"positions": plans, "saved_at": now_iso()}, handle, indent=2)
            os.replace(tmp, self.state_file)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @contextmanager
    def _file_lock(self):
        """Exclusive lock (POSIX) around a read-modify-write of the state file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file.with_name(self.state_file.name + ".lock"), "a") as handle:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, PartialExitState]:
        """
        Plans saved in the state file ({} if none).

        An unreadable file is moved aside (``.unreadable-<time>``) rather than left to
        be overwritten by the next save, so its plans can still be recovered.
        """
        if self.state_file is None or not self.state_file.exists():
            return {}
        try:
            return {
                symbol: PartialExitState.from_dict(state)
                for symbol, state in self._read_plans().items()
            }
        except Exception as e:
            aside = self.state_file.with_name(
                f"{self.state_file.name}.unreadable-{format_for_filename()}"
            )
            os.replace(self.state_file, aside)
            logger.error(f"Could not read partial exit plans ({e}); moved the file to {aside}")
            return {}

    @classmethod
    def from_mode_manager(
        cls,
        order_manager,
        trailing_stop_manager,
        mode_manager,
        mode=None,
        state_file: Optional[Path] = None,
    ) -> "PartialExitManager":
        """
        Create PartialExitManager from TradingModeManager configuration.

        Args:
            order_manager: OrderManager instance
            trailing_stop_manager: TrailingStopManager instance
            mode_manager: TradingModeManager instance
            mode: TradingMode to use (default: current mode)
            state_file: Where plans are saved (default: state/partial_exits.json, so
                the CLI sees them)

        Returns:
            PartialExitManager instance configured for the mode
        """
        params = mode_manager.get_parameters(mode)
        partial_exit_config = mode_manager.get_partial_exit_config_dict(mode)

        logger.info(
            f"Creating PartialExitManager from mode: {params.mode.value}, "
            f"config={partial_exit_config}"
        )

        return cls(
            order_manager=order_manager,
            trailing_stop_manager=trailing_stop_manager,
            partial_exit_config=partial_exit_config,
            state_file=state_file or default_state_file(),
        )


def load_partial_exit_manager(order_manager=None) -> PartialExitManager:
    """
    A PartialExitManager over the saved plans in the state dir.

    Each call reads the file, so it sees plans saved by other processes. Without an
    order manager it can show and edit saved plans but not place or check orders.
    """
    return PartialExitManager(order_manager=order_manager, state_file=default_state_file())
