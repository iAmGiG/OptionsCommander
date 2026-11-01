"""
StateReconciler - Reconcile local state with broker reality.

Extracted from trading_cycle.py as part of #439 refactoring.
Handles state reconciliation, stop adjustments, and position alerts.
"""

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.utils.date_utils import get_datetime_now, now_iso

logger = logging.getLogger(__name__)

# Alpaca cancels GTC orders (bracket legs included) this many days after creation
GTC_LIFETIME_DAYS = 90
# Renew a GTC stop once it is this old, ahead of that cancellation
STOP_RENEW_AFTER_DAYS = 80

# A trailing stop is kept at least this far below the price: off-hours the price is the
# last trade, and a stop just under it could fire on any small gap at the open
MIN_STOP_GAP = 0.01

# Alpaca can't replace an order in these states (orders placed after the close stay
# "accepted" for a while; ones placed at 22:44 ET were replaceable by 09:20 ET), so
# raising one waits for a later run
NOT_REPLACEABLE = ("accepted", "pending_new", "pending_replace", "pending_cancel")


def _protective_stops(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A position's live sell stops (stop or stop-limit; trailing stops trail themselves)."""
    return [
        order
        for order in orders
        if "sell" in str(order.get("side", "")).lower()
        and order.get("stop_price")
        and "trailing" not in str(order.get("type", "")).lower()
    ]


def _alert_levels(
    broker_pos: Dict[str, Any], local_pos: Optional[Dict[str, Any]]
) -> Optional[Tuple[float, int, float, Optional[float]]]:
    """(entry, quantity, stop, target) for a long position with an entry and a stop, else None."""
    if not local_pos or "short" in str(broker_pos.get("side", "")).lower():
        return None
    quantity = int(broker_pos.get("quantity", 0))
    entry_price = float(local_pos.get("entry_price") or 0)
    stop_price = local_pos.get("stop_price")
    if quantity <= 0 or not entry_price or not stop_price:
        return None
    target_price = local_pos.get("target_price")
    return entry_price, quantity, float(stop_price), float(target_price) if target_price else None


def _stop_reason(entry_price: float, current_price: float, new_stop: float) -> str:
    """Why a stop moves to new_stop, from the level itself (whatever rule or clamp set it)."""
    profit = (current_price - entry_price) / entry_price
    if new_stop <= entry_price + 0.01:
        return f"Move to breakeven ({profit:.1%} profit)"
    locked = (new_stop - entry_price) / (current_price - entry_price)
    return f"Lock {locked:.0%} of gains ({profit:.1%} profit)"


@dataclass
class Discrepancy:
    """Represents a mismatch between local state and broker reality."""

    type: str  # UNKNOWN_POSITION, GHOST_POSITION, ORDER_MISMATCH, QUANTITY_MISMATCH
    symbol: str
    details: Dict[str, Any]
    action: str  # NEEDS_HUMAN_REVIEW, AUTO_FIXED, REMOVED_FROM_JSON
    severity: str = "MEDIUM"  # LOW, MEDIUM, HIGH


@dataclass
class StopAdjustment:
    """Represents a stop order modification needed."""

    symbol: str
    order_id: str
    current_stop: float
    new_stop: float
    reason: str
    profit_percent: float


@dataclass
class StopRepair:
    """A protective stop to place (none is live) or renew (an aging GTC stop)."""

    kind: str  # MISSING_STOP or STOP_EXPIRING
    symbol: str
    quantity: int
    stop_price: float
    reason: str
    replaces_order_id: Optional[str] = None  # the stop being renewed


def _age_days(created_at: Optional[str], now: datetime) -> Optional[float]:
    """Days since an ISO timestamp (naive values are UTC); None if unknown."""
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds() / 86400


@dataclass
class PositionAlertSummary:
    """Summary of position alerts for reporting."""

    symbol: str
    alert_type: str
    severity: str
    message: str
    timestamp: str
    current_price: float
    details: Dict[str, Any]


class StateReconciler:
    """
    Reconciles local JSON state with broker reality.

    Broker is always source of truth. Local JSON is for human reference.
    """

    def __init__(
        self,
        config: Any,
        trailing_stop_manager: Optional[Any] = None,
        position_tracker: Optional[Any] = None,
    ):
        """
        Initialize StateReconciler.

        Args:
            config: TradingConfig for risk parameters
            trailing_stop_manager: TrailingStopManager for stop calculations
            position_tracker: PositionTracker for alert checking
        """
        self.config = config
        self.trailing_stop_manager = trailing_stop_manager
        self.position_tracker = position_tracker

        logger.info("StateReconciler initialized")

    def reconcile(  # noqa: C901
        self,
        broker_state: Dict[str, Any],
        local_state: Dict[str, Any],
    ) -> tuple[List[Discrepancy], Dict[str, Any]]:
        """
        Reconcile local JSON with broker reality.
        JSON is for humans, broker is truth.

        Args:
            broker_state: Current state from broker
            local_state: Current local JSON state

        Returns:
            Tuple of (discrepancies list, updated local_state)
        """
        discrepancies = []
        positions = local_state.get("positions", {})

        # Check for unknown positions (at broker but not in local JSON)
        for symbol, broker_pos in broker_state["positions"].items():
            if symbol not in positions:
                discrepancies.append(
                    Discrepancy(
                        type="UNKNOWN_POSITION",
                        symbol=symbol,
                        details={
                            "broker_quantity": broker_pos["quantity"],
                            "broker_entry": broker_pos["entry_price"],
                            "current_price": broker_pos["current_price"],
                        },
                        action="NEEDS_HUMAN_REVIEW",
                        severity="HIGH",
                    )
                )

                # Extract stop/target prices from broker's open orders
                entry_price = broker_pos["entry_price"]
                stop_price, target_price, _ = self._extract_stop_target_from_orders(
                    symbol, broker_state, entry_price=entry_price, local_state=local_state
                )

                # Auto-add to local state for tracking
                positions[symbol] = {
                    "entry_price": broker_pos["entry_price"],
                    "quantity": broker_pos["quantity"],
                    "entry_time": now_iso(),
                    "source": "AUTO_DISCOVERED",
                    "stop_price": stop_price,
                    "target_price": target_price,
                }

        # Check for ghost positions (in local JSON but not at broker)
        for symbol in list(positions.keys()):
            if symbol not in broker_state["positions"]:
                local_pos = positions[symbol]
                discrepancies.append(
                    Discrepancy(
                        type="GHOST_POSITION",
                        symbol=symbol,
                        details={
                            "local_quantity": local_pos.get("quantity", 0),
                            "local_entry": local_pos.get("entry_price", 0),
                        },
                        action="REMOVED_FROM_JSON",
                        severity="MEDIUM",
                    )
                )

                # Remove ghost position
                del positions[symbol]

        # Check quantity mismatches
        for symbol in broker_state["positions"]:
            if symbol in positions:
                broker_qty = broker_state["positions"][symbol]["quantity"]
                local_qty = positions[symbol].get("quantity", 0)

                if broker_qty != local_qty:
                    discrepancies.append(
                        Discrepancy(
                            type="QUANTITY_MISMATCH",
                            symbol=symbol,
                            details={
                                "broker_quantity": broker_qty,
                                "local_quantity": local_qty,
                                "difference": broker_qty - local_qty,
                            },
                            action="AUTO_FIXED",
                            severity="LOW",
                        )
                    )

                    # Fix quantity
                    positions[symbol]["quantity"] = broker_qty

                # The broker's average entry is the truth: it moves on an add-on, or when
                # a position is sold and bought again (#619)
                broker_entry = broker_state["positions"][symbol].get("entry_price")
                local_entry = positions[symbol].get("entry_price")
                if broker_entry and (not local_entry or abs(broker_entry - local_entry) >= 0.01):
                    discrepancies.append(
                        Discrepancy(
                            type="ENTRY_PRICE_CHANGED",
                            symbol=symbol,
                            details={"broker_entry": broker_entry, "local_entry": local_entry},
                            action="AUTO_FIXED",
                            severity="LOW",
                        )
                    )
                    positions[symbol]["entry_price"] = broker_entry

        # Sync stop/target prices from broker orders for all positions
        for symbol in broker_state["positions"]:
            if symbol in positions:
                entry_price = positions[symbol].get("entry_price")
                stop_price, target_price, _ = self._extract_stop_target_from_orders(
                    symbol, broker_state, entry_price=entry_price, local_state=local_state
                )

                # Update local state with broker's stop/target prices
                local_pos = positions[symbol]
                local_stop = local_pos.get("stop_price")
                local_target = local_pos.get("target_price")

                # Only log discrepancy if there was a change
                if stop_price != local_stop or target_price != local_target:
                    logger.info(
                        f"{symbol}: Syncing stop/target from orders - "
                        f"stop: {local_stop} -> {stop_price}, "
                        f"target: {local_target} -> {target_price}"
                    )
                    local_pos["stop_price"] = stop_price
                    local_pos["target_price"] = target_price

        local_state["positions"] = positions
        logger.info(f"Reconciliation found {len(discrepancies)} discrepancies")
        return discrepancies, local_state

    def _extract_stop_target_from_orders(
        self,
        symbol: str,
        broker_state: Dict[str, Any],
        entry_price: float = None,
        local_state: Dict[str, Any] = None,
    ) -> tuple:
        """
        Extract stop_price and target_price from broker's open orders for a symbol.

        If no live stop order is found (e.g. a GTC stop lapsed after 90 days), the saved
        stop or one calculated from the entry price is returned for tracking only;
        check_protective_stops() re-places the missing order.

        Args:
            symbol: Ticker symbol
            broker_state: Current broker state with orders
            entry_price: Position entry price (for calculating expected stop if not found)
            local_state: Local state for saved stop values

        Returns:
            (stop_price, target_price, stop_verified) tuple
            - stop_verified: True if stop found in API, False if calculated
        """
        stop_price = None
        target_price = None
        stop_verified = False

        if symbol in broker_state.get("orders", {}):
            logger.debug(f"{symbol}: Found {len(broker_state['orders'][symbol])} orders")
            for order in broker_state["orders"][symbol]:
                order_type = order.get("type", "")
                side = order.get("side", "")

                # Convert enums to strings for comparison
                side_str = str(side).lower() if side else ""
                order_type_str = str(order_type).lower() if order_type else ""

                logger.debug(
                    f"  Order: type={order_type_str}, side={side_str}, "
                    f"stop={order.get('stop_price')}, limit={order.get('limit_price')}"
                )

                # Stop-loss order: sell order with stop_price set (for long positions)
                if "sell" in side_str and order.get("stop_price"):
                    stop_price = order["stop_price"]
                    stop_verified = True
                    logger.debug(f"  -> Found stop_price: {stop_price}")

                # Take-profit order: sell order with limit_price set (for long positions)
                elif "sell" in side_str and order.get("limit_price"):
                    target_price = order["limit_price"]
                    logger.debug(f"  -> Found target_price: {target_price}")
        else:
            logger.debug(f"{symbol}: No orders found in broker_state")

        # If stop not found via API, try to use saved value from local state first
        if not stop_price and local_state:
            positions = local_state.get("positions", {})
            if symbol in positions:
                saved_stop = positions[symbol].get("stop_price")
                if saved_stop:
                    stop_price = saved_stop
                    logger.info(f"{symbol}: Using saved stop price from local state: ${stop_price}")

        # Last resort: calculate from entry price if we have no saved value
        if not stop_price and entry_price:
            stop_loss_pct = self.config.get_risk_config("stop_loss")
            calculated_stop = round(entry_price * (1 - stop_loss_pct), 2)
            stop_price = calculated_stop
            logger.warning(
                f"{symbol}: No live stop order and no saved value; tracking a stop "
                f"calculated from entry: ${calculated_stop}"
            )

        logger.info(
            f"{symbol}: Extracted stop=${stop_price} (verified={stop_verified}), "
            f"target=${target_price}"
        )

        return stop_price, target_price, stop_verified

    def calculate_stop_adjustments(
        self,
        broker_state: Dict[str, Any],
        local_state: Dict[str, Any],
    ) -> tuple[List[StopAdjustment], Dict[str, Any]]:
        """
        Calculate which stops need raising, from the stop orders the broker holds.

        The broker's stop price is the truth: a raise recorded locally but refused by
        the broker (e.g. "cannot replace order in accepted status" outside market hours)
        is computed again on the next run. Nothing is recorded here; the caller records
        a new stop once the broker accepts it (CostEfficientTradeCycle.batch_modify_orders).

        Args:
            broker_state: Current broker state
            local_state: Current local state (entry prices)

        Returns:
            Tuple of (adjustments list, local_state unchanged)
        """
        if self.trailing_stop_manager is None:
            logger.warning("No trailing stop manager configured")
            return [], local_state

        adjustments = []
        positions = local_state.get("positions", {})

        for symbol, broker_pos in broker_state["positions"].items():
            if symbol not in positions:
                continue  # Skip unknown positions

            stops = _protective_stops(broker_state.get("orders", {}).get(symbol, []))
            if not stops:
                continue  # no live stop: check_protective_stops() re-places it

            # Every stop on the position is raised to one level, from the lowest
            lowest = min(stops, key=lambda order: float(order["stop_price"]))
            current_price = broker_pos["current_price"]
            new_stop = self._trailing_level(
                symbol,
                positions,
                float(lowest["stop_price"]),
                int(broker_pos.get("quantity", 0)),
                current_price,
                adopt_order_id=lowest["id"],
            )
            if new_stop is None:
                continue

            entry_price = float(positions[symbol].get("entry_price") or 0)
            reason = _stop_reason(entry_price, current_price, new_stop)
            for order in stops:
                current_stop = float(order["stop_price"])
                if new_stop <= current_stop + 0.01:
                    continue
                if str(order.get("status", "")).lower() in NOT_REPLACEABLE:
                    logger.info(
                        f"{symbol}: raise to ${new_stop:.2f} waits - order {order['id']} is "
                        f"{order.get('status')} (not yet replaceable)"
                    )
                    continue
                logger.info(
                    f"{symbol}: Stop adjustment - ${current_stop:.2f} → ${new_stop:.2f} ({reason})"
                )
                adjustments.append(
                    StopAdjustment(
                        symbol=symbol,
                        order_id=order["id"],
                        current_stop=current_stop,
                        new_stop=new_stop,
                        reason=reason,
                        profit_percent=(current_price - entry_price) / entry_price,
                    )
                )

        logger.info(f"Found {len(adjustments)} stop adjustments needed")
        return adjustments, local_state

    def check_protective_stops(
        self,
        broker_state: Dict[str, Any],
        local_state: Dict[str, Any],
        now: Optional[datetime] = None,
        raise_to_trailing: bool = True,
    ) -> tuple[List[Discrepancy], List[StopRepair]]:
        """
        Find long positions with no live stop, and GTC orders near Alpaca's 90-day limit.

        Alpaca cancels GTC orders 90 days after creation, bracket legs included, so a
        position's stop can lapse without notice. A missing stop is re-placed at the
        last known stop (local state, else entry minus the configured stop loss); a GTC
        stop older than STOP_RENEW_AFTER_DAYS is renewed. With raise_to_trailing, either is
        raised to the trailing-stop level when that is higher (and below the price): an
        order placed outside market hours can't be modified in the same run, so the
        routine's later adjustment step couldn't raise it. The morning routine turns this
        off, as it doesn't raise stops (#623), so a repair there keeps the stop's level.

        Reported for a human instead of repaired:
        - no usable stop level below the current price (that stop would trigger at once);
        - shares held by other open sell orders (a new stop would be rejected);
        - an aging trailing stop (its trail can't be renewed by a stop-price replace);
        - an aging GTC take-profit (it lapses on its own clock, even next to a renewed stop).

        Args:
            broker_state: Current broker state (positions, and open orders with legs)
            local_state: Local state, after reconcile()
            now: Current time (for tests); UTC by default

        Returns:
            Tuple of (discrepancies needing human review, stop repairs to make)
        """
        now = now or get_datetime_now(timezone.utc)
        needs_review: List[Discrepancy] = []
        repairs: List[StopRepair] = []
        positions = local_state.get("positions", {})

        for symbol, broker_pos in broker_state["positions"].items():
            quantity = int(broker_pos.get("quantity", 0))
            if quantity <= 0 or "short" in str(broker_pos.get("side", "")).lower():
                continue  # only long positions are protected by sell stops

            sells = [
                order
                for order in broker_state.get("orders", {}).get(symbol, [])
                if "sell" in str(order.get("side", "")).lower()
            ]
            stops = [order for order in sells if order.get("stop_price")]
            needs_review.extend(self._aging_targets(symbol, sells, now))

            current_price = float(broker_pos.get("current_price") or 0)
            if stops:
                renewals, reviews = self._stop_renewals(symbol, stops, quantity, now)
                for renewal in renewals:
                    trailing = (
                        self._trailing_level(
                            symbol, positions, renewal.stop_price, quantity, current_price
                        )
                        if raise_to_trailing
                        else None
                    )
                    if trailing is not None:
                        renewal.reason += (
                            f"; raised to the trailing level from ${renewal.stop_price:.2f}"
                        )
                        renewal.stop_price = trailing
                repairs.extend(renewals)
                needs_review.extend(reviews)
                continue

            stop_price = self._last_known_stop(symbol, positions, broker_pos)
            details = {
                "quantity": quantity,
                "stop_price": stop_price,
                "current_price": current_price,
            }
            if stop_price is None or stop_price >= current_price:
                details["reason"] = "no stop level below the current price to place"
            elif sells:
                details["reason"] = "shares are held by open sell orders, so a stop can't be added"
            else:
                reason = "no live stop order (GTC stops lapse after 90 days)"
                trailing = (
                    self._trailing_level(symbol, positions, stop_price, quantity, current_price)
                    if raise_to_trailing
                    else None
                )
                if trailing is not None:
                    reason += f"; placed at the trailing level (last stop ${stop_price:.2f})"
                    stop_price = trailing
                repairs.append(
                    StopRepair(
                        kind="MISSING_STOP",
                        symbol=symbol,
                        quantity=quantity,
                        stop_price=stop_price,
                        reason=reason,
                    )
                )
                continue

            needs_review.append(
                Discrepancy(
                    type="MISSING_STOP",
                    symbol=symbol,
                    details=details,
                    action="NEEDS_HUMAN_REVIEW",
                    severity="HIGH",
                )
            )

        return needs_review, repairs

    def _stop_renewals(
        self, symbol: str, stops: List[Dict[str, Any]], quantity: int, now: datetime
    ) -> tuple[List[StopRepair], List[Discrepancy]]:
        """Renewals for GTC stops old enough to be cancelled soon (trailing stops: review)."""
        renewals, reviews = [], []
        for order in stops:
            age = _age_days(order.get("created_at"), now)
            if age is None or age < STOP_RENEW_AFTER_DAYS:
                continue
            if "gtc" not in str(order.get("time_in_force", "")).lower():
                continue  # DAY stops end each day; a missing one is re-placed as GTC
            reason = f"GTC stop is {age:.0f} days old (Alpaca cancels at {GTC_LIFETIME_DAYS})"
            if "trailing" in str(order.get("type", "")).lower():
                reviews.append(
                    Discrepancy(
                        type="STOP_EXPIRING",
                        symbol=symbol,
                        details={
                            "order_id": order["id"],
                            "stop_price": order.get("stop_price"),
                            "reason": f"{reason}; re-place this trailing stop",
                        },
                        action="NEEDS_HUMAN_REVIEW",
                        severity="HIGH",
                    )
                )
                continue
            renewals.append(
                StopRepair(
                    kind="STOP_EXPIRING",
                    symbol=symbol,
                    quantity=int(order.get("quantity") or quantity),
                    stop_price=float(order["stop_price"]),
                    reason=reason,
                    replaces_order_id=order["id"],
                )
            )
        return renewals, reviews

    def _aging_targets(
        self, symbol: str, sells: List[Dict[str, Any]], now: datetime
    ) -> List[Discrepancy]:
        """Notices for GTC take-profit orders about to reach Alpaca's 90-day limit."""
        notices = []
        for order in sells:
            if order.get("stop_price") or not order.get("limit_price"):
                continue
            age = _age_days(order.get("created_at"), now)
            if age is None or age < STOP_RENEW_AFTER_DAYS:
                continue
            if "gtc" not in str(order.get("time_in_force", "")).lower():
                continue
            notices.append(
                Discrepancy(
                    type="TARGET_EXPIRING",
                    symbol=symbol,
                    details={
                        "order_id": order["id"],
                        "limit_price": order.get("limit_price"),
                        "reason": (
                            f"GTC take-profit is {age:.0f} days old "
                            f"(Alpaca cancels at {GTC_LIFETIME_DAYS})"
                        ),
                    },
                    action="NEEDS_HUMAN_REVIEW",
                    severity="LOW",
                )
            )
        return notices

    def _trailing_level(
        self,
        symbol: str,
        positions: Dict[str, Any],
        actual_stop: Optional[float],
        quantity: int,
        current_price: float,
        adopt_order_id: Optional[str] = None,
    ) -> Optional[float]:
        """
        Where the trailing-stop rules put a position's stop now, if that is above
        actual_stop; else None. The level is kept MIN_STOP_GAP below the price.

        With adopt_order_id (a live broker stop), the trailing manager adopts that stop
        and order as its own first, so a stop it recorded but the broker never accepted
        can't hide the raise. Without it (a stop about to be placed or renewed), the
        level is worked out on a copy and the manager is left as it was: the routine
        records the stop once the broker accepts it.
        """
        local = positions.get(symbol)
        manager = self.trailing_stop_manager
        if manager is None or not local or actual_stop is None or not current_price:
            return None
        entry_price = float(local.get("entry_price") or 0)
        if not entry_price:
            return None

        original = manager.stop_states.get(symbol)
        if original is None:
            manager.register_position(
                symbol=symbol,
                entry_price=entry_price,
                initial_stop=actual_stop,
                quantity=quantity,
                stop_order_id=adopt_order_id,
            )
        elif adopt_order_id is None:
            manager.stop_states[symbol] = copy.deepcopy(original)
        state = manager.stop_states[symbol]
        state.current_stop = actual_stop
        if adopt_order_id is not None:
            state.stop_order_id = adopt_order_id

        try:
            level = manager.calculate_new_stop(symbol, current_price)
        finally:
            if adopt_order_id is None:  # leave the manager as it was
                if original is None:
                    manager.stop_states.pop(symbol, None)
                else:
                    manager.stop_states[symbol] = original

        if level is None:
            return None
        level = round(min(level, current_price * (1 - MIN_STOP_GAP)), 2)
        return level if level > actual_stop + 0.01 else None

    def _last_known_stop(
        self, symbol: str, positions: Dict[str, Any], broker_pos: Dict[str, Any]
    ) -> Optional[float]:
        """The saved stop for a position, else entry price minus the configured stop loss."""
        saved = positions.get(symbol, {}).get("stop_price")
        if saved:
            return round(float(saved), 2)
        entry_price = float(broker_pos.get("entry_price") or 0)
        if not entry_price:
            return None
        return round(entry_price * (1 - self.config.get_risk_config("stop_loss")), 2)

    def check_position_alerts(
        self,
        broker_state: Dict[str, Any],
        local_state: Dict[str, Any],
    ) -> List[PositionAlertSummary]:
        """
        Alerts for open long positions: near or through the stop, and near or through the
        target where one exists (#599). A position through a level is still open, so its
        order didn't fill; details["live_stop"] says whether a sell stop is open for it.

        The tracker keeps one entry per position checked, synced to the levels in local
        state (after reconcile and any raise, the broker's live stop and target); entries
        for anything else (closed, short, or without a stop this run) are dropped.

        Args:
            broker_state: Current broker state with positions
            local_state: Current local state

        Returns:
            List of position alerts
        """
        if self.position_tracker is None:
            logger.warning("No position tracker configured")
            return []

        alerts = []
        checked = []
        positions = local_state.get("positions", {})
        for symbol, broker_pos in broker_state["positions"].items():
            try:
                levels = _alert_levels(broker_pos, positions.get(symbol))
                if levels is None:
                    continue
                checked.append(symbol)
                alert = self._position_alert(symbol, broker_pos, levels, broker_state)
            except Exception as e:  # one position's error doesn't cost the others' alerts
                logger.warning(f"Position alert check failed for {symbol}: {e}")
                continue
            if alert is not None:
                alerts.append(alert)
        self.position_tracker.prune(checked)

        logger.info(f"Found {len(alerts)} position alerts")
        return alerts

    def _position_alert(
        self,
        symbol: str,
        broker_pos: Dict[str, Any],
        levels: Tuple[float, int, float, Optional[float]],
        broker_state: Dict[str, Any],
    ) -> Optional[PositionAlertSummary]:
        """One position's alert, if any, at its (entry, quantity, stop, target) levels."""
        position = self.position_tracker.sync_position(symbol, *levels)
        current_price = float(broker_pos["current_price"])
        condition = self.position_tracker.check_exit_conditions(position.position_id, current_price)
        if condition is None:
            return None
        details = dict(condition["details"])
        details["live_stop"] = bool(
            _protective_stops(broker_state.get("orders", {}).get(symbol, []))
        )
        message = condition["message"]
        if not details["live_stop"]:
            message += " - no live stop order"
        return PositionAlertSummary(
            symbol=symbol,
            alert_type=condition["alert_type"].value,
            severity=condition["severity"],
            message=message,
            timestamp=now_iso(),
            current_price=current_price,
            details=details,
        )
