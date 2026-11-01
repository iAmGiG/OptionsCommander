"""
GTT Action Executor - Execute trigger actions.

Issue #340: Handle actions when GTT triggers fire.

Phase 1: Alert actions (complete)
Phase 2: Order placement and cancellation with AlpacaOrderManager integration

Supports:
- ALERT: Send notification to user
- PLACE_ORDER: Place market/limit order via AlpacaOrderManager
- CANCEL_ORDER: Cancel existing order via AlpacaOrderManager
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.trading.gtt.gtt_manager import ActionType, GTTTrigger, get_gtt_manager
from src.utils.date_utils import now_iso

logger = logging.getLogger(__name__)


def _get_order_manager(mode: str = "paper"):
    """
    Lazy load AlpacaOrderManager to avoid circular imports.

    Args:
        mode: Trading mode ("paper" or "live")

    Returns:
        AlpacaOrderManager instance or None if unavailable
    """
    try:
        from src.trading.broker.alpaca_trading_client import AlpacaOrderManager

        return AlpacaOrderManager(mode=mode)
    except ImportError as e:
        logger.warning(f"AlpacaOrderManager not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize AlpacaOrderManager: {e}")
        return None


@dataclass
class ActionResult:
    """Result of executing a trigger action."""

    trigger_id: int
    success: bool
    action_type: str
    message: str
    details: Dict[str, Any] = None
    timestamp: str = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}
        if self.timestamp is None:
            self.timestamp = now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "success": self.success,
            "action_type": self.action_type,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class ActionExecutor:
    """
    Executes actions when GTT triggers fire.

    Handles alerts, order placement, and OCO group management.

    Phase 2: Integrates with AlpacaOrderManager for actual order execution.
    """

    def __init__(self, trading_mode: str = "paper"):
        """
        Initialize action executor.

        Args:
            trading_mode: "paper" or "live" for order execution
        """
        self.gtt_manager = get_gtt_manager()
        self._alert_history: List[ActionResult] = []
        self._trading_mode = trading_mode
        self._order_manager = None  # Lazy loaded

    def execute_trigger(
        self,
        trigger: GTTTrigger,
        current_price: float,
    ) -> ActionResult:
        """
        Execute action for a fired trigger.

        Args:
            trigger: GTT trigger that fired
            current_price: Current market price when triggered

        Returns:
            ActionResult with execution details
        """
        action_type = trigger.action_type

        # Execute the action
        if action_type == ActionType.ALERT.value:
            result = self._execute_alert(trigger, current_price)

        elif action_type == ActionType.PLACE_ORDER.value:
            result = self._execute_place_order(trigger, current_price)

        elif action_type == ActionType.CANCEL_ORDER.value:
            result = self._execute_cancel_order(trigger)

        else:
            result = ActionResult(
                trigger_id=trigger.id,
                success=False,
                action_type=action_type,
                message=f"Unknown action type: {action_type}",
            )

        # Record the trigger fire
        if result.success:
            self.gtt_manager.record_trigger_fire(trigger.id)

            # Handle OCO - disable partner triggers
            if trigger.oco_group_id:
                disabled = self.gtt_manager.disable_oco_group(
                    trigger.oco_group_id, except_trigger_id=trigger.id
                )
                result.details["oco_disabled"] = disabled
                logger.info(f"OCO: Disabled {disabled} partner triggers")

        # Store in history
        self._alert_history.append(result)

        return result

    def execute_triggers_batch(
        self,
        triggers: List[GTTTrigger],
        prices: Dict[str, float],
    ) -> List[ActionResult]:
        """
        Execute actions for multiple triggered GTTs.

        Args:
            triggers: List of triggers that fired
            prices: Current prices by symbol

        Returns:
            List of ActionResults
        """
        results = []

        for trigger in triggers:
            current_price = prices.get(trigger.symbol, 0)
            result = self.execute_trigger(trigger, current_price)
            results.append(result)

        return results

    # =========================================================================
    # Action Handlers
    # =========================================================================

    def _execute_alert(self, trigger: GTTTrigger, current_price: float) -> ActionResult:
        """Execute ALERT action - send notification."""
        config = trigger.action_config or {}
        custom_message = config.get("message", "")

        # Build alert message
        condition_desc = self._describe_condition(trigger)
        message = (
            f"GTT Alert: {trigger.symbol}\n"
            f"Condition: {condition_desc}\n"
            f"Current price: ${current_price:.2f}"
        )

        if custom_message:
            message += f"\nNote: {custom_message}"

        if trigger.notes:
            message += f"\nNotes: {trigger.notes}"

        # Log the alert (in production, this would send to notification system)
        logger.info(f"GTT ALERT: {message}")

        return ActionResult(
            trigger_id=trigger.id,
            success=True,
            action_type=ActionType.ALERT.value,
            message=message,
            details={
                "symbol": trigger.symbol,
                "condition": trigger.condition_type,
                "trigger_value": trigger.trigger_value,
                "current_price": current_price,
            },
        )

    def _get_order_manager_instance(self):
        """Get or create order manager instance."""
        if self._order_manager is None:
            self._order_manager = _get_order_manager(self._trading_mode)
        return self._order_manager

    def _execute_place_order(self, trigger: GTTTrigger, current_price: float) -> ActionResult:
        """
        Execute PLACE_ORDER action - place trade order via AlpacaOrderManager.

        Phase 2: Actually places orders through the broker.

        action_config expected keys:
            - order_type: "market" or "limit" (default: "market")
            - side: "buy" or "sell" (required)
            - qty: int quantity (required)
            - limit_price: float for limit orders
            - simulate_only: bool to force simulation mode (default: False)
        """
        config = trigger.action_config or {}

        order_type = config.get("order_type", "market")
        side = config.get("side")
        qty = config.get("qty")
        limit_price = config.get("limit_price")

        # Never guess an order: this used to default to buying 1 share (#635)
        if side not in ("buy", "sell") or not isinstance(qty, (int, float)) or qty <= 0:
            return ActionResult(
                trigger_id=trigger.id,
                success=False,
                action_type=ActionType.PLACE_ORDER.value,
                message=f"{trigger.symbol}: no order placed, its trigger has no side or quantity",
                details={"symbol": trigger.symbol},
            )
        simulate_only = config.get("simulate_only", False)

        # Build order description
        order_desc = f"{side.upper()} {qty} {trigger.symbol}"
        if order_type == "limit" and limit_price:
            order_desc += f" @ ${limit_price:.2f}"
        else:
            order_desc += f" @ MARKET (~${current_price:.2f})"

        # Check if we should simulate or execute
        order_manager = self._get_order_manager_instance()

        if simulate_only or order_manager is None:
            # Simulation mode
            logger.info(f"GTT ORDER (simulated): {order_desc}")
            return ActionResult(
                trigger_id=trigger.id,
                success=True,
                action_type=ActionType.PLACE_ORDER.value,
                message=f"Order simulated: {order_desc}",
                details={
                    "symbol": trigger.symbol,
                    "order_type": order_type,
                    "side": side,
                    "qty": qty,
                    "limit_price": limit_price,
                    "current_price": current_price,
                    "simulated": True,
                },
            )

        # Execute real order via AlpacaOrderManager
        try:
            logger.info(f"GTT ORDER (executing): {order_desc} [{self._trading_mode}]")

            if order_type == "limit" and limit_price:
                # Place limit order (GTC)
                order_result = order_manager.place_limit_order_gtc(
                    symbol=trigger.symbol,
                    qty=qty,
                    side=side,
                    limit_price=limit_price,
                )
            else:
                # Place market order
                order_result = order_manager.place_market_order(
                    symbol=trigger.symbol,
                    qty=qty,
                    side=side,
                )

            # Check result
            if order_result.get("status") == "error":
                logger.error(f"GTT ORDER FAILED: {order_result.get('message')}")
                return ActionResult(
                    trigger_id=trigger.id,
                    success=False,
                    action_type=ActionType.PLACE_ORDER.value,
                    message=f"Order failed: {order_result.get('message')}",
                    details={
                        "symbol": trigger.symbol,
                        "order_type": order_type,
                        "side": side,
                        "qty": qty,
                        "error": order_result.get("message"),
                        "simulated": False,
                    },
                )

            # Success
            order_id = order_result.get("order_id")
            logger.info(f"GTT ORDER SUCCESS: {order_desc} -> Order ID: {order_id}")

            return ActionResult(
                trigger_id=trigger.id,
                success=True,
                action_type=ActionType.PLACE_ORDER.value,
                message=f"Order placed: {order_desc}",
                details={
                    "symbol": trigger.symbol,
                    "order_type": order_type,
                    "side": side,
                    "qty": qty,
                    "limit_price": limit_price,
                    "current_price": current_price,
                    "order_id": order_id,
                    "order_status": order_result.get("status_detail"),
                    "trading_mode": self._trading_mode,
                    "simulated": False,
                },
            )

        except Exception as e:
            logger.error(f"GTT ORDER EXCEPTION: {e}", exc_info=True)
            return ActionResult(
                trigger_id=trigger.id,
                success=False,
                action_type=ActionType.PLACE_ORDER.value,
                message=f"Order exception: {str(e)}",
                details={
                    "symbol": trigger.symbol,
                    "order_type": order_type,
                    "side": side,
                    "qty": qty,
                    "error": str(e),
                    "simulated": False,
                },
            )

    def _execute_cancel_order(self, trigger: GTTTrigger) -> ActionResult:
        """
        Execute CANCEL_ORDER action - cancel existing order via AlpacaOrderManager.

        Phase 2: Actually cancels orders through the broker.

        action_config expected keys:
            - order_id: str order ID to cancel (required)
            - simulate_only: bool to force simulation mode (default: False)
        """
        config = trigger.action_config or {}
        order_id = config.get("order_id")
        simulate_only = config.get("simulate_only", False)

        if not order_id:
            return ActionResult(
                trigger_id=trigger.id,
                success=False,
                action_type=ActionType.CANCEL_ORDER.value,
                message="No order_id specified in action_config",
            )

        # Check if we should simulate or execute
        order_manager = self._get_order_manager_instance()

        if simulate_only or order_manager is None:
            # Simulation mode
            logger.info(f"GTT CANCEL (simulated): Order {order_id}")
            return ActionResult(
                trigger_id=trigger.id,
                success=True,
                action_type=ActionType.CANCEL_ORDER.value,
                message=f"Cancel simulated: Order {order_id}",
                details={
                    "order_id": order_id,
                    "simulated": True,
                },
            )

        # Execute real cancellation via AlpacaOrderManager
        try:
            logger.info(f"GTT CANCEL (executing): Order {order_id} [{self._trading_mode}]")

            cancel_result = order_manager.cancel_order(order_id)

            # Check result
            if cancel_result.get("status") == "error":
                logger.error(f"GTT CANCEL FAILED: {cancel_result.get('message')}")
                return ActionResult(
                    trigger_id=trigger.id,
                    success=False,
                    action_type=ActionType.CANCEL_ORDER.value,
                    message=f"Cancel failed: {cancel_result.get('message')}",
                    details={
                        "order_id": order_id,
                        "error": cancel_result.get("message"),
                        "simulated": False,
                    },
                )

            # Success
            logger.info(f"GTT CANCEL SUCCESS: Order {order_id}")
            return ActionResult(
                trigger_id=trigger.id,
                success=True,
                action_type=ActionType.CANCEL_ORDER.value,
                message=f"Order cancelled: {order_id}",
                details={
                    "order_id": order_id,
                    "cancel_status": cancel_result.get("status"),
                    "trading_mode": self._trading_mode,
                    "simulated": False,
                },
            )

        except Exception as e:
            logger.error(f"GTT CANCEL EXCEPTION: {e}", exc_info=True)
            return ActionResult(
                trigger_id=trigger.id,
                success=False,
                action_type=ActionType.CANCEL_ORDER.value,
                message=f"Cancel exception: {str(e)}",
                details={
                    "order_id": order_id,
                    "error": str(e),
                    "simulated": False,
                },
            )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _describe_condition(self, trigger: GTTTrigger) -> str:
        """Generate human-readable condition description."""
        from src.trading.gtt.trigger_evaluator import get_trigger_evaluator

        evaluator = get_trigger_evaluator()
        return evaluator.describe_trigger_condition(trigger)

    def get_alert_history(self, limit: int = 20) -> List[ActionResult]:
        """Get recent alert history."""
        return self._alert_history[-limit:]

    def clear_alert_history(self) -> None:
        """Clear alert history."""
        self._alert_history.clear()

    def format_result_for_display(self, result: ActionResult) -> str:
        """Format ActionResult for CLI display."""
        lines = []

        if result.success:
            lines.append(f"✓ Trigger {result.trigger_id} - {result.action_type}")
        else:
            lines.append(f"✗ Trigger {result.trigger_id} - FAILED")

        lines.append(f"  {result.message}")

        details = result.details or {}
        if details.get("symbol"):
            lines.append(f"  Symbol: {details['symbol']}")
        if details.get("current_price"):
            lines.append(f"  Price: ${details['current_price']:.2f}")
        if details.get("oco_disabled"):
            lines.append(f"  OCO: Disabled {details['oco_disabled']} partner(s)")

        return "\n".join(lines)


# Module-level instance
_executor: Optional[ActionExecutor] = None


def get_action_executor() -> ActionExecutor:
    """Get global action executor instance."""
    global _executor
    if _executor is None:
        _executor = ActionExecutor()
    return _executor
