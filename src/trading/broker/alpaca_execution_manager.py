"""
AlpacaExecutionManager - Executes trades via Alpaca broker.

Integrates existing OrderManager into the plugin architecture.
Refactored to use extracted components (Issue #441).
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

# Import extracted components
from .api_error_translator import APIErrorTranslator
from .validators.bracket_validator import BracketOrderValidator
from src.core.interfaces import ExecutionManager
from src.core.models import OrderResult, OrderType, TradeDecision, TradeSuggestion
from src.utils.config_loader import ConfigLoader
from src.utils.market_hours import is_market_hours

# Import message loader for user-facing messages
try:
    from config_defaults.message_loader import MessageLoader

    _MSG = MessageLoader()
    MESSAGE_LOADER_AVAILABLE = True
except ImportError:
    MESSAGE_LOADER_AVAILABLE = False
    logging.warning("MessageLoader not available - using fallback messages")

# Import price fetcher for current market prices
try:
    from src.trading.utils.unified_price_fetcher import UnifiedPriceFetcher

    PRICE_FETCHER_AVAILABLE = True
except ImportError:
    try:
        from ..utils.unified_price_fetcher import UnifiedPriceFetcher

        PRICE_FETCHER_AVAILABLE = True
    except ImportError:
        PRICE_FETCHER_AVAILABLE = False
        logging.warning("UnifiedPriceFetcher not available - will use suggestion prices")

DEFAULT_STOP_LOSS_PCT = 0.05
DEFAULT_TAKE_PROFIT_PCT = 0.08

logger = logging.getLogger(__name__)


_FINISHED_ORDER_STATUSES = ("filled", "canceled", "expired", "rejected", "replaced", "done_for_day")
# What the order manager returns when no order went to the broker
_ORDER_NOT_SENT_STATUSES = ("error", "cancelled", "dry_run")
_RELEASE_WAIT_TRIES = 10
_RELEASE_WAIT_SECONDS = 0.5


class AlpacaExecutionManager(ExecutionManager):
    """
    Execute trades via Alpaca broker.

    Integrates with existing OrderManager to place bracket orders
    with entry, stop-loss, and take-profit levels.

    MVP: Simplified to handle basic execution.
    Full order lifecycle management in OrderManager.
    """

    def __init__(
        self,
        order_manager: Optional[object] = None,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
    ):
        """
        Initialize with existing OrderManager.

        Args:
            order_manager: OrderManager instance (from src/trading/order_manager.py)
            stop_loss_pct: Stop loss percentage (overrides config/default)
            take_profit_pct: Take profit percentage (overrides config/default)
        """
        self.order_manager = order_manager

        # Load config for defaults
        self.config = ConfigLoader()

        # Priority: Argument > Config > Constant Default
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        else:
            self.stop_loss_pct = self.config.get(
                "trading.default_stop_loss_pct", DEFAULT_STOP_LOSS_PCT
            )

        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        else:
            self.take_profit_pct = self.config.get(
                "trading.default_take_profit_pct", DEFAULT_TAKE_PROFIT_PCT
            )

        self._price_fetcher = None  # Lazy loaded

        if order_manager:
            msg = (
                _MSG.get("execution.init_with_manager")
                if MESSAGE_LOADER_AVAILABLE
                else "AlpacaExecutionManager initialized with OrderManager"
            )
            logger.info(msg)
        else:
            msg = (
                _MSG.get("execution.init_without_manager")
                if MESSAGE_LOADER_AVAILABLE
                else "AlpacaExecutionManager initialized without OrderManager (stub mode)"
            )
            logger.warning(msg)

        msg = (
            _MSG.get(
                "execution.strategy_config",
                stop_loss_pct=self.stop_loss_pct * 100,
                take_profit_pct=self.take_profit_pct * 100,
            )
            if MESSAGE_LOADER_AVAILABLE
            else (
                f"Using strategy config: stop_loss={self.stop_loss_pct * 100}%, "
                f"take_profit={self.take_profit_pct * 100}%"
            )
        )
        logger.info(msg)

        # Log price fetcher availability
        if PRICE_FETCHER_AVAILABLE:
            logger.info(
                "UnifiedPriceFetcher available - will fetch current prices before execution"
            )
        else:
            logger.warning("UnifiedPriceFetcher NOT available - will use suggestion prices")

    async def execute_trade(
        self, suggestion: TradeSuggestion, decision: Optional[TradeDecision] = None
    ) -> OrderResult:
        """
        Execute trade based on suggestion and user decision.

        High-level orchestration method that delegates to focused helper methods
        for better testability and maintainability.

        Args:
            suggestion: The trade suggestion
            decision: User's decision (may contain modifications)

        Returns:
            OrderResult with order IDs and execution details
        """
        try:
            # Step 1: Prepare trade parameters
            ticker, signal, quantity, entry_price, stop_loss, take_profit = self._prepare_trade(
                suggestion, decision
            )

            # Step 2: Fetch current market price
            current_market_price = await self._fetch_current_price(ticker)

            # Step 3: Recalculate bracket prices if needed
            entry_price, stop_loss, take_profit = self._recalculate_bracket_prices(
                signal,
                suggestion.order_type,
                current_market_price,
                entry_price,
                stop_loss,
                take_profit,
            )

            # Step 4: Validate SELL signal if applicable
            if signal.lower() == "sell":
                validation_result = self._validate_sell_signal(ticker, quantity)
                if not validation_result["success"]:
                    return validation_result["order_result"]

            # Step 5: Place the order
            return await self._place_order(
                ticker, quantity, signal, entry_price, stop_loss, take_profit, suggestion.order_type
            )

        except Exception as e:
            # Log full error details at DEBUG level only
            logger.debug(f"Execution error for {suggestion.ticker}: {e}", exc_info=True)

            # Translate API errors to user-friendly messages
            user_message, user_error = APIErrorTranslator.translate(
                str(e),
                suggestion.ticker,
                suggestion.entry_price,
                suggestion.stop_loss,
                suggestion.take_profit,
            )

            return OrderResult(
                success=False,
                ticker=suggestion.ticker,
                quantity=suggestion.recommended_quantity,
                message=user_message,
                error=user_error,
            )

    def _prepare_trade(
        self, suggestion: TradeSuggestion, decision: Optional[TradeDecision] = None
    ) -> tuple:
        """
        Extract and prepare trade parameters from suggestion and user decision.

        Args:
            suggestion: The trade suggestion
            decision: Optional user modifications

        Returns:
            Tuple of (ticker, signal, quantity, entry_price, stop_loss, take_profit)
        """
        ticker = suggestion.ticker
        signal = suggestion.signal.value

        # Apply modifications if present
        if decision:
            quantity = decision.modified_quantity or suggestion.recommended_quantity
            entry_price = decision.modified_entry or suggestion.entry_price
            stop_loss = decision.modified_stop or suggestion.stop_loss
            take_profit = decision.modified_target or suggestion.take_profit
        else:
            quantity = suggestion.recommended_quantity
            entry_price = suggestion.entry_price
            stop_loss = suggestion.stop_loss
            take_profit = suggestion.take_profit

        logger.info(
            f"Executing: {signal.upper()} {quantity} {ticker} "
            f"@ {entry_price:.2f} (stop: {stop_loss:.2f}, target: {take_profit:.2f})"
        )

        return ticker, signal, quantity, entry_price, stop_loss, take_profit

    async def _fetch_current_price(self, ticker: str) -> Optional[float]:
        """
        Fetch current market price using UnifiedPriceFetcher.

        Args:
            ticker: Stock symbol

        Returns:
            Current market price or None if unavailable
        """
        current_market_price = None

        # Try to get current price from OrderManager's market data client
        if self.order_manager and hasattr(self.order_manager, "client"):
            try:
                from src.market_data.alpaca_market_data import AlpacaMarketData

                market_data = AlpacaMarketData()

                # Try latest trade first
                trade = market_data.get_latest_trade(ticker)
                if trade and "trade" in trade and trade["trade"].get("p"):
                    current_market_price = float(trade["trade"]["p"])
                    logger.info(f"Got latest trade price for {ticker}: ${current_market_price:.2f}")
                else:
                    # Fallback to quote mid-price
                    quote = market_data.get_latest_quote(ticker)
                    if quote and "quote" in quote:
                        bid = quote["quote"].get("bp")
                        ask = quote["quote"].get("ap")
                        if bid and ask and bid > 0 and ask > 0:
                            current_market_price = round((float(bid) + float(ask)) / 2, 2)
                            logger.info(
                                f"Got quote mid-price for {ticker}: ${current_market_price:.2f}"
                            )
            except Exception as e:
                logger.warning(f"Could not fetch current price from Alpaca market data: {e}")

        # Fallback to UnifiedPriceFetcher if Alpaca failed
        if current_market_price is None and PRICE_FETCHER_AVAILABLE:
            try:
                if self._price_fetcher is None:
                    self._price_fetcher = UnifiedPriceFetcher()
                fetched_price = self._price_fetcher.get_current_price(ticker, use_cache=False)
                if fetched_price is not None:  # None when no real price is available
                    current_market_price = fetched_price
                    logger.info(f"Got price from UnifiedPriceFetcher: ${current_market_price:.2f}")
                else:
                    logger.warning(f"UnifiedPriceFetcher has no price for {ticker}")
            except Exception as e:
                logger.error(f"UnifiedPriceFetcher failed with exception: {e}", exc_info=True)

        return current_market_price

    def _recalculate_bracket_prices(
        self,
        signal: str,
        order_type,
        current_market_price: Optional[float],
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> tuple:
        """
        Recalculate bracket prices based on signal and market price.

        For LIMIT orders (pullback/breakout), preserves calculated prices.
        For MARKET orders, recalculates based on current market price.

        Args:
            signal: BUY or SELL
            order_type: OrderType enum value
            current_market_price: Current market price if available
            entry_price: Original entry price
            stop_loss: Original stop loss
            take_profit: Original take profit

        Returns:
            Tuple of (entry_price, stop_loss, take_profit)
        """
        is_limit_order = order_type == OrderType.LIMIT

        # For LIMIT orders (pullback/breakout), preserve the calculated entry price
        # For MARKET orders, recalculate based on current market price
        if is_limit_order:
            # Preserve pullback/breakout entry price - don't recalculate
            logger.info(
                f"LIMIT order: Using pullback/breakout entry price "
                f"${entry_price:.2f} (stop=${stop_loss:.2f}, "
                f"target=${take_profit:.2f})"
            )
        elif current_market_price and current_market_price > 0 and signal.lower() == "buy":
            # MARKET order: recalculate bracket prices using current market price
            entry_price = round(current_market_price, 2)
            stop_loss = round(current_market_price * (1 - self.stop_loss_pct), 2)
            take_profit = round(current_market_price * (1 + self.take_profit_pct), 2)

            sl_pct = self.stop_loss_pct * 100
            tp_pct = self.take_profit_pct * 100
            logger.info(
                f"MARKET order: Recalculated bracket prices using "
                f"${current_market_price:.2f}: entry=${entry_price:.2f}, "
                f"stop=${stop_loss:.2f} (-{sl_pct}%), "
                f"target=${take_profit:.2f} (+{tp_pct}%)"
            )
        else:
            logger.warning(
                "Using suggestion prices "
                "(could not fetch current market price) - "
                "may cause validation errors during off-hours"
            )

        return entry_price, stop_loss, take_profit

    def _validate_sell_signal(self, ticker: str, quantity: int) -> dict:
        """
        Validate SELL signal to prevent short selling.

        Checks if we have a position and sufficient quantity.

        Args:
            ticker: Stock symbol
            quantity: Quantity to sell

        Returns:
            Dict with keys:
            - success: bool
            - order_result: OrderResult if validation failed
        """
        # Check if we have a position in this ticker
        has_position = False
        position_qty = 0

        if self.order_manager and hasattr(self.order_manager, "get_positions"):
            try:
                positions = self.order_manager.get_positions()
                position = next((p for p in positions if p["symbol"] == ticker), None)

                if position and float(position["qty"]) > 0:
                    has_position = True
                    position_qty = int(position["qty"])
                    logger.info(f"Found position: {position_qty} shares of {ticker}")
            except Exception as e:
                logger.warning(f"Could not check positions: {e}")

        # If no position, reject SELL to prevent short selling
        if not has_position:
            log_msg = (
                f"SELL signal rejected for {ticker} - no position held (prevents short selling)"
            )
            logger.warning(log_msg)
            user_msg = (
                _MSG.get("execution.sell_no_position", ticker=ticker)
                if MESSAGE_LOADER_AVAILABLE
                else (
                    f"SELL signal rejected: No position in {ticker}. Short selling not supported."
                )
            )
            return {
                "success": False,
                "order_result": OrderResult(
                    success=False,
                    entry_order_id=None,
                    stop_order_id=None,
                    target_order_id=None,
                    ticker=ticker,
                    quantity=quantity,
                    filled_price=None,
                    message=user_msg,
                    error="No position - short selling not supported",
                ),
            }

        # Verify quantity doesn't exceed position
        if quantity > position_qty:
            log_msg = f"SELL quantity {quantity} exceeds position {position_qty} for {ticker}"
            logger.warning(log_msg)
            user_msg = (
                _MSG.get(
                    "execution.sell_exceeds_position",
                    qty=quantity,
                    position_qty=position_qty,
                )
                if MESSAGE_LOADER_AVAILABLE
                else f"SELL quantity ({quantity}) exceeds position ({position_qty} shares)"
            )
            return {
                "success": False,
                "order_result": OrderResult(
                    success=False,
                    entry_order_id=None,
                    stop_order_id=None,
                    target_order_id=None,
                    ticker=ticker,
                    quantity=quantity,
                    filled_price=None,
                    message=user_msg,
                    error="Insufficient position",
                ),
            }

        logger.info(
            f"SELL signal approved - have {position_qty} shares of {ticker}, selling {quantity}"
        )
        return {"success": True}

    def _open_sell_orders(self, ticker: str) -> List[Dict[str, Any]]:
        """A symbol's open sell orders, which hold shares; bracket legs included."""
        orders = self.order_manager.get_orders(status="open", symbols=[ticker])
        flat = [o for order in orders for o in [order, *(order.get("legs") or [])]]
        return [
            o
            for o in flat
            if "sell" in str(o.get("side", "")).lower()
            and str(o.get("status", "")).lower() not in _FINISHED_ORDER_STATUSES
        ]

    def plan_close(self, ticker: str, quantity: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        What selling a long position would take, without changing anything.

        Args:
            ticker: Stock symbol
            quantity: Shares to sell; all of them if None or more than are held

        Returns:
            None if there's no long position, else a dict with ticker, quantity, held,
            market_open, and orders_to_cancel: the open sell orders that must go first,
            because they hold the shares (Alpaca refuses a sale of held shares)
        """
        if not self.order_manager:
            raise ValueError("No order manager: cannot sell")

        position = next(
            (p for p in self.order_manager.get_positions() if p.get("symbol") == ticker), None
        )
        held = int(float(position.get("qty") or 0)) if position else 0
        if held <= 0 or "short" in str(position.get("side", "")).lower():
            return None

        to_sell = min(quantity, held) if quantity else held
        sell_orders = self._open_sell_orders(ticker)
        available = position.get("qty_available")
        free = (
            int(float(available))
            if available is not None
            else held - int(sum(o.get("qty") or 0 for o in sell_orders))
        )
        return {
            "ticker": ticker,
            "quantity": to_sell,
            "held": held,
            "market_open": is_market_hours(),
            "orders_to_cancel": [] if free >= to_sell else sell_orders,
        }

    async def close_position(self, plan: Dict[str, Any]) -> OrderResult:
        """
        Sell at market, first cancelling the orders that hold the shares.

        A stop is cancelled before the sale, so a failed sale puts it back, and a partial
        sale leaves the remaining shares a new stop.

        Args:
            plan: From plan_close

        Returns:
            OrderResult; on failure, its message says whether a stop is in place
        """
        ticker, quantity, held = plan["ticker"], plan["quantity"], plan["held"]

        def failed(reason: str, cancelled: List[Dict[str, Any]]) -> OrderResult:
            message = f"SELL {quantity} {ticker} not placed: {reason}"
            if self._stop_of(cancelled):
                restored = self._replace_stop(ticker, held, cancelled)
                message += (
                    ". Its stop is back in place"
                    if restored
                    else ". ITS STOP WAS CANCELLED AND COULD NOT BE PUT BACK: the next routine "
                    "re-places it"
                )
            return OrderResult(
                success=False, ticker=ticker, quantity=quantity, message=message, error=reason
            )

        cancelled = []
        for order in plan["orders_to_cancel"]:
            if self.order_manager.cancel_order(order["id"]).get("status") == "cancelled":
                cancelled.append(order)

        still_open = await self._wait_for_release(ticker, plan["orders_to_cancel"])
        if still_open:
            # A stop whose cancel hasn't landed is still working: only replace those gone
            gone = [order for order in cancelled if order["id"] not in still_open]
            return failed("the orders holding its shares are still open", gone)

        try:
            order = self.order_manager.place_market_order(symbol=ticker, qty=quantity, side="sell")
        except Exception as e:  # the stop must still go back
            logger.error(f"SELL {quantity} {ticker} failed: {e}")
            return failed(str(e), cancelled)
        if order.get("status") in _ORDER_NOT_SENT_STATUSES:
            return failed(order.get("message", "the broker refused it"), cancelled)

        message = f"MARKET order placed: SELL {quantity} {ticker}"
        remaining = held - quantity
        if remaining > 0 and self._stop_of(cancelled):
            message += (
                f". New stop placed on the other {remaining} shares"
                if self._replace_stop(ticker, remaining, cancelled)
                else f". THE OTHER {remaining} SHARES HAVE NO STOP until the next routine"
            )
        return OrderResult(
            success=True,
            entry_order_id=order.get("order_id"),
            ticker=ticker,
            quantity=quantity,
            message=message,
        )

    async def _wait_for_release(self, ticker: str, orders: List[Dict[str, Any]]) -> Set[str]:
        """
        Alpaca cancels asynchronously, and refuses a sale until the shares are released.

        Returns:
            The IDs of these orders still open when the wait ended
        """
        still_open = {order["id"] for order in orders}
        for _ in range(_RELEASE_WAIT_TRIES):
            if not still_open:
                break
            still_open &= {order["id"] for order in self._open_sell_orders(ticker)}
            if still_open:
                await asyncio.sleep(_RELEASE_WAIT_SECONDS)
        return still_open

    @staticmethod
    def _stop_of(orders: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The first stop among these orders, if any."""
        return next((o for o in orders if o.get("stop_price")), None)

    def _replace_stop(self, ticker: str, quantity: int, cancelled: List[Dict[str, Any]]) -> bool:
        """Place a GTC stop at a cancelled stop's level; whether the broker accepted it."""
        stop_price = self._stop_of(cancelled)["stop_price"]
        try:
            placed = self.order_manager.place_stop_order(
                symbol=ticker,
                qty=quantity,
                side="sell",
                stop_price=stop_price,
                time_in_force="gtc",
            )
        except Exception as e:
            logger.error(f"Could not place a stop on {ticker} @ {stop_price}: {e}")
            return False
        return placed.get("status") not in _ORDER_NOT_SENT_STATUSES

    async def _place_order(
        self,
        ticker: str,
        quantity: int,
        signal: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        order_type,
    ) -> OrderResult:
        """
        Place the actual order via OrderManager.

        Handles bracket orders with fallback to simple market orders during off-hours.

        Args:
            ticker: Stock symbol
            quantity: Quantity to order
            signal: BUY or SELL
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            order_type: OrderType enum value

        Returns:
            OrderResult with order details
        """
        side = signal.lower()
        is_limit_order = order_type == OrderType.LIMIT

        if not self.order_manager:
            # Stub mode: Return mock order result
            return self._create_stub_result(ticker, quantity, entry_price, signal)

        # Check if we're during market hours (using centralized utility)
        market_open = is_market_hours()

        if not market_open:
            msg = (
                _MSG.get("execution.market_closed_warning")
                if MESSAGE_LOADER_AVAILABLE
                else (
                    "⚠️  Market is CLOSED (weekend/off-hours). "
                    "Bracket orders may fail validation during off-hours."
                )
            )
            logger.warning(msg)

        # Execute via OrderManager
        error_data = None
        try:
            order_data = self._submit_order(
                ticker, quantity, side, is_limit_order, entry_price, stop_loss, take_profit
            )

            # Declined at the live-trading confirmation: nothing was sent
            if order_data.get("status") == "cancelled":
                return OrderResult(
                    success=False,
                    ticker=ticker,
                    quantity=quantity,
                    message=order_data.get("message", "Order cancelled"),
                    error="Cancelled at confirmation; no order was sent",
                )

            # Check for errors
            if order_data.get("status") == "error":
                error_data = {
                    "status": "error",
                    "message": order_data.get("message", "Unknown error"),
                    "error_code": order_data.get("error_code"),
                    "status_code": order_data.get("status_code"),
                }
                raise Exception(order_data.get("message", "Unknown error"))

        except Exception as e:
            if error_data is None:
                error_data = {
                    "status": "error",
                    "message": str(e),
                    "error_code": None,
                    "status_code": None,
                }

            # Use extracted BracketOrderValidator
            is_bracket_error = BracketOrderValidator.is_bracket_validation_error(error_data)

            # Only a bracket entry falls back: the fallback is a plain market BUY, so
            # retrying a failed SELL that way would add to the position instead of exiting
            if side == "buy" and not market_open and is_bracket_error:
                # Fallback to simple market order
                return await self._handle_bracket_fallback(
                    ticker, quantity, stop_loss, take_profit, e, error_data
                )
            else:
                # Re-raise if not an off-hours issue
                raise

        # Extract order IDs: the bracket's real leg IDs (a SELL exit has no legs)
        entry_order_id = order_data.get("order_id")
        stop_order_id = order_data.get("stop_order_id")
        target_order_id = order_data.get("take_profit_order_id")

        # Issue #344: Include order type and entry price in message
        order_type_str = "LIMIT" if is_limit_order else "MARKET"
        order_kind = "bracket order" if side == "buy" else "order"
        entry_info = f"@ ${entry_price:.2f}" if is_limit_order else "(market)"

        result = OrderResult(
            success=True,
            entry_order_id=entry_order_id,
            stop_order_id=stop_order_id,
            target_order_id=target_order_id,
            ticker=ticker,
            quantity=quantity,
            filled_price=None,
            message=(
                f"{order_type_str} {order_kind} placed: {signal} {quantity} {ticker} {entry_info}"
            ),
        )

        logger.info(
            f"✅ Execution complete: {ticker} "
            f"entry_id={entry_order_id}, stop_id={stop_order_id}, "
            f"target_id={target_order_id}"
        )

        return result

    def _submit_order(
        self,
        ticker: str,
        quantity: int,
        side: str,
        is_limit_order: bool,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> dict:
        """Submit a SELL exit as a plain order, or a BUY entry as a bracket order."""
        # Handle SELL (Exit) separately - usually a simple order, not a bracket
        if side == "sell":
            logger.info(f"Placing SELL order for {ticker} (closing position)")
            if is_limit_order:
                return self.order_manager.place_limit_order_gtc(
                    symbol=ticker, qty=quantity, side=side, limit_price=entry_price
                )
            return self.order_manager.place_market_order(symbol=ticker, qty=quantity, side=side)

        # Handle BUY (Entry) - Use Bracket Order
        # Issue #344: Use limit entry for pullback/breakout orders
        if is_limit_order:
            logger.info(
                f"Placing LIMIT bracket order: {ticker} @ ${entry_price:.2f} "
                "(waiting for pullback/breakout price)"
            )
        else:
            logger.info(f"Placing MARKET bracket order: {ticker} (immediate fill)")

        return self.order_manager.place_bracket_order(
            symbol=ticker,
            qty=quantity,
            side=side,
            entry_limit_price=entry_price if is_limit_order else None,
            take_profit_price=take_profit,
            stop_loss_price=stop_loss,
            time_in_force="gtc",
        )

    async def _handle_bracket_fallback(
        self,
        ticker: str,
        quantity: int,
        stop_loss: float,
        take_profit: float,
        original_error: Exception,
        error_data: dict,
    ) -> OrderResult:
        """
        Handle fallback to simple market order when bracket fails off-hours.

        Places a market BUY, so it is only for failed bracket entries, never exits.

        Args:
            ticker: Stock symbol
            quantity: Quantity to order
            stop_loss: Stop loss price (for warning message)
            take_profit: Take profit price (for warning message)
            original_error: The original bracket order error
            error_data: Error data dict

        Returns:
            OrderResult from fallback attempt
        """
        msg = (
            _MSG.get(
                "execution.bracket_validation_failed",
                error=str(original_error),
                error_code=error_data.get("error_code", "N/A"),
                status_code=error_data.get("status_code", "N/A"),
            )
            if MESSAGE_LOADER_AVAILABLE
            else (
                f"❌ Bracket order validation failed (off-hours): {original_error}\n"
                f"Error code: {error_data.get('error_code', 'N/A')}, "
                f"Status: {error_data.get('status_code', 'N/A')}\n"
                "🔄 Attempting fallback: simple market order without brackets..."
            )
        )
        logger.warning(msg)

        try:
            fallback_order = self.order_manager.place_market_order(
                symbol=ticker, qty=quantity, side="buy"
            )

            if fallback_order.get("status") == "error":
                raise Exception(fallback_order.get("message", "Unknown error"))

            fallback_order_id = fallback_order.get("order_id") or fallback_order.get("id")

            user_msg = (
                _MSG.get(
                    "execution.fallback_order_warning",
                    target=take_profit,
                    stop=stop_loss,
                )
                if MESSAGE_LOADER_AVAILABLE
                else (
                    f"⚠️  Market order placed WITHOUT brackets (off-hours fallback). "
                    f"Target: ${take_profit:.2f}, Stop: ${stop_loss:.2f} "
                    "(NOT automatically set). Manual risk management required!"
                )
            )
            return OrderResult(
                success=True,
                entry_order_id=fallback_order_id,
                stop_order_id=None,
                target_order_id=None,
                ticker=ticker,
                quantity=quantity,
                filled_price=None,
                message=user_msg,
                error=None,
            )

        except Exception as fallback_error:
            logger.error(f"❌ Fallback market order also failed: {fallback_error}")
            user_msg = (
                _MSG.get("execution.fallback_order_failed")
                if MESSAGE_LOADER_AVAILABLE
                else "Both bracket and fallback market orders failed during off-hours"
            )
            return OrderResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                ticker=ticker,
                quantity=quantity,
                filled_price=None,
                message=user_msg,
                error=f"Bracket error: {original_error}; Fallback error: {fallback_error}",
            )

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if not self.order_manager:
            msg = (
                _MSG.get("execution.cancel_no_manager")
                if MESSAGE_LOADER_AVAILABLE
                else "Cannot cancel - no OrderManager (stub mode)"
            )
            logger.warning(msg)
            return False

        try:
            # OrderManager should have cancel method
            # (or use broker client directly)
            self.order_manager.broker.cancel_order_by_id(order_id)
            msg = (
                _MSG.get("execution.order_cancelled", order_id=order_id)
                if MESSAGE_LOADER_AVAILABLE
                else f"Order cancelled: {order_id}"
            )
            logger.info(msg)
            return True

        except Exception as e:
            msg = (
                _MSG.get("execution.cancel_failed", order_id=order_id, error=str(e))
                if MESSAGE_LOADER_AVAILABLE
                else f"Failed to cancel order {order_id}: {e}"
            )
            logger.error(msg)
            return False

    async def get_order_status(self, order_id: str) -> dict:
        """
        Get current status of an order.

        Args:
            order_id: Order ID

        Returns:
            Dict with order status details
        """
        if not self.order_manager:
            msg = (
                _MSG.get("execution.status_no_manager")
                if MESSAGE_LOADER_AVAILABLE
                else "Cannot get status - no OrderManager (stub mode)"
            )
            logger.warning(msg)
            return {"status": "unknown", "error": "No OrderManager"}

        try:
            # Query broker for order status
            order = self.order_manager.broker.get_order_by_id(order_id)

            return {
                "id": order.id,
                "status": order.status.value,
                "symbol": order.symbol,
                "qty": float(order.qty),
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0.0,
                "filled_avg_price": (
                    float(order.filled_avg_price) if order.filled_avg_price else None
                ),
            }

        except Exception as e:
            msg = (
                _MSG.get("execution.status_failed", order_id=order_id, error=str(e))
                if MESSAGE_LOADER_AVAILABLE
                else f"Failed to get order status {order_id}: {e}"
            )
            logger.error(msg)
            return {"status": "error", "error": str(e)}

    async def modify_order(
        self, order_id: str, new_quantity: Optional[int] = None, new_price: Optional[float] = None
    ) -> bool:
        """
        Modify an existing order.

        Args:
            order_id: Order ID to modify
            new_quantity: New quantity (if changing)
            new_price: New limit price (if changing)

        Returns:
            True if modified successfully
        """
        if not self.order_manager:
            logger.warning("Cannot modify order - no OrderManager")
            return False

        try:
            # Delegate to OrderManager's modify capability
            result = self.order_manager.modify_order(
                order_id=order_id, qty=new_quantity, limit_price=new_price
            )

            if result.get("status") == "submitted":
                logger.info(f"Order {order_id} modified successfully")
                return True
            else:
                logger.error(f"Failed to modify order {order_id}: {result.get('message')}")
                return False
        except Exception as e:
            logger.error(f"Error modifying order {order_id}: {e}")
            return False

    def _create_stub_result(
        self, ticker: str, quantity: int, price: float, signal: str
    ) -> OrderResult:
        """
        Create stub order result for testing without OrderManager.

        Args:
            ticker: Stock ticker
            quantity: Number of shares
            price: Entry price
            signal: buy or sell

        Returns:
            Mock OrderResult
        """
        # Generate unique stub ID for each call
        stub_id = str(uuid.uuid4())[:8]

        return OrderResult(
            success=True,
            entry_order_id=f"stub_entry_{stub_id}",
            stop_order_id=f"stub_stop_{stub_id}",
            target_order_id=f"stub_target_{stub_id}",
            ticker=ticker,
            quantity=quantity,
            filled_price=price,
            message=f"[STUB] {signal.upper()} {quantity} {ticker} @ ${price:.2f}",
        )
