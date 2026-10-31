"""
Trade Handler - Extracted from cli_session.py.

Issue #509: Refactor cli_session.py into modular handlers
Handles trade request processing, signal conflict resolution, and trade execution.
"""

import logging
from typing import TYPE_CHECKING, Optional

from config_defaults.message_loader import CLIMessages as MSG  # noqa: N814

from src.cli.utils.error_utils import sanitize_error_message
from src.cli.utils.input_parser import detect_user_intent
from src.cli.utils.suggestion_display import (
    calc_pct,
    display_position_context,
    display_result,
    display_suggestion,
)
from src.cli.utils.ui_utils import get_error_prefix
from src.core.models import Signal
from src.utils.date_utils import now_iso

if TYPE_CHECKING:
    from src.core.trading_orchestrator import TradingOrchestrator
    from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle

logger = logging.getLogger(__name__)


class TradeHandler:
    """
    Handles trade request processing for CLI session.

    Extracted from CLISession._handle_trade_request for modularity.
    """

    def __init__(
        self,
        orchestrator: "TradingOrchestrator",
        account_monitor,
        trading_cycle: Optional["CostEfficientTradeCycle"],
        autonomy_mode: str = "confirm",
        user_id: str = "cli_user",
    ):
        """
        Initialize trade handler.

        Args:
            orchestrator: TradingOrchestrator instance
            account_monitor: AlpacaAccountMonitor instance
            trading_cycle: CostEfficientTradeCycle instance
            autonomy_mode: "confirm" or "auto"
            user_id: User ID for orchestrator
        """
        self.orchestrator = orchestrator
        self.account_monitor = account_monitor
        self.trading_cycle = trading_cycle
        self.autonomy_mode = autonomy_mode
        self.user_id = user_id

    def set_autonomy_mode(self, mode: str) -> None:
        """Update autonomy mode."""
        self.autonomy_mode = mode

    async def handle_trade_request(self, user_input: str, request=None) -> None:
        """
        Process user trade request via orchestrator.

        Issue #347: Respects user intent - when user says "buy", we show "BUY (as requested)"
        and treat signals as context, not override.
        Issue #632: The order follows the signal, so a request for the other side places
        nothing, and the input is parsed once, which alone decides whether this is a review.

        Args:
            user_input: User's natural language input
            request: The input already parsed; parsed here if not given
        """
        print(MSG.ANALYZING_TRADE)

        try:
            if request is None:
                request = await self.orchestrator.parser.parse(user_input, self.user_id)
            is_review_only = request.action not in ("buy", "sell")

            # Process request via orchestrator
            decision = await self.orchestrator.process_request(
                user_input, self.user_id, request=request
            )

            # Check position context
            position = self._check_position_for_ticker(decision.suggestion.ticker)
            display_position_context(decision.suggestion.ticker, position)

            # A review only shows the analysis; a trade settles any conflict with the signal
            requested = None
            if not is_review_only:
                requested = detect_user_intent(user_input) or request.action
                decision = await self._handle_signal_conflicts(decision, position, requested)
                if decision is None:
                    return  # User cancelled or request handled

            # Display suggestion; "as requested" only when that's the order on offer
            signal = decision.suggestion.signal.value.upper()
            override_mode = None
            if requested == "buy" and signal == "BUY":
                override_mode = "USER_OVERRIDE_LONG"
            elif requested == "sell" and signal == "SELL":
                override_mode = "USER_OVERRIDE_SHORT"
            display_suggestion(decision.suggestion, position, override_mode)

            # Get confirmation
            decision.approved = self._get_approval(decision, is_review_only)

            # Execute if approved
            if decision.approved:
                result = await self.orchestrator.execute_decision(decision)
                display_result(result)
                self._update_local_state_after_trade(decision, result)

        except (ValueError, OSError, RuntimeError, AttributeError) as e:
            sanitized_msg = sanitize_error_message(e)
            error_prefix = get_error_prefix()
            print(f"\n{error_prefix} {sanitized_msg}")
            logger.debug(f"Request processing error: {e}", exc_info=True)

    async def _handle_signal_conflicts(self, decision, position, requested: str):
        """
        Handle conflicts between what the user asked for and the technical signal.

        Args:
            decision: The trade decision
            position: The open position, if any
            requested: "buy" or "sell"

        Returns:
            Updated decision or None if cancelled
        """
        suggestion = decision.suggestion
        signal = suggestion.signal.value.upper()

        # The order would be the opposite of the request
        if (signal == "BUY" and requested == "sell") or (
            signal == "SELL" and position and requested == "buy"
        ):
            self._show_opposite_refusal(suggestion, requested)
            return None

        # SELL signal with no position
        if signal == "SELL" and not position:
            if requested == "buy":
                return await self._handle_buy_override(decision)
            self._show_cannot_sell_message(suggestion.ticker)
            return None

        # SELL signal with position - show warning
        if signal == "SELL":
            print(f"\n{MSG.EMOJI['warning']} SELL will close your position in {suggestion.ticker}")

        # HOLD signal with explicit user intent
        elif signal == "HOLD":
            return self._handle_hold_with_intent(decision, requested == "buy")

        return decision

    def _show_opposite_refusal(self, suggestion, requested: str) -> None:
        """Say why nothing is placed when the order would oppose the request."""
        signal = suggestion.signal.value.upper()
        print(f"\n{MSG.EMOJI['warning']} NO ORDER PLACED")
        print(f"   → You asked to {requested.upper()} {suggestion.ticker}")
        print(f"   → Technical analysis suggests: {signal}")
        print(f"   → Orders follow the signal, so this one would have been a {signal}")

    async def _handle_buy_override(self, decision):
        """Handle user wanting to BUY despite SELL signal."""
        suggestion = decision.suggestion

        print(f"\n{MSG.EMOJI.get('warning', '⚠️')} SIGNAL CONFLICT DETECTED")
        print("   → You requested: LONG (BUY) position")
        print("   → Technical analysis suggests: SHORT (SELL)")
        print(
            f"\n📊 Technical Indicators (based on {suggestion.reasoning[0] if suggestion.reasoning else 'MACD+RSI'}):"
        )

        display_suggestion(suggestion, None, override_mode="USER_OVERRIDE_LONG")

        print("\n💡 Human-in-Loop Decision:")
        print("   → The system recommends SELL, but you want to go LONG")
        print("   → This could be based on news, fundamentals, or your own analysis")
        print("   → Remember: Technical indicators are backward-looking")

        proceed = (
            input(f"\n   Do you still want to BUY {suggestion.ticker}? [yes/no]: ").strip().lower()
        )

        if proceed in ["yes", "y", "1"]:
            print(f"\n{MSG.EMOJI['info']} ✅ User override confirmed - placing BUY order")
            print("   → Overriding SELL signal from technical analysis")

            # Flip signal to BUY
            decision.suggestion.signal = Signal.BUY
            self._invert_stop_target_for_buy(decision.suggestion)

            self._print_adjusted_trade_plan(decision.suggestion, "BUY")
            return decision
        else:
            print(f"\n{MSG.EMOJI['info']} Order cancelled. You can review alternatives:")
            print(f"   • Type 'review {suggestion.ticker}' for detailed analysis")
            print(f"   • Type 'short {suggestion.ticker}' to follow the SELL signal")
            return None

    def _get_approval(self, decision, is_review_only: bool) -> bool:
        """
        Whether to execute the trade.

        Review-only requests and trades the risk check refused are never approved (the
        orchestrator refuses the latter too). Otherwise ask, or approve in auto mode.
        """
        refused = bool(decision.suggestion.risk_block_reasons)
        if is_review_only:
            print("\n📊 Analysis complete. No trade execution requested.")
        if refused:
            self._show_risk_refusal(decision.suggestion)
        if is_review_only or refused:
            return False

        suggestion = decision.suggestion
        order = (
            f"{suggestion.signal.value.upper()} {suggestion.recommended_quantity} "
            f"{suggestion.ticker}"
        )
        if self.autonomy_mode != "confirm":
            print(f"{MSG.AUTO_EXECUTING} {order}")
            return True
        print(f"\nOrder: {order}")
        if self._get_confirmation():
            return True
        print(MSG.TRADE_CANCELLED)
        return False

    def _show_risk_refusal(self, suggestion) -> None:
        """Show why the risk check refused a trade."""
        print(f"\n{MSG.EMOJI['error']} Trade refused by risk check - it can't be placed:")
        for reason in suggestion.risk_block_reasons:
            print(f"   • {reason}")

    def _show_cannot_sell_message(self, ticker: str) -> None:
        """Show message when user wants to sell but has no position."""
        print(f"\n{MSG.EMOJI['error']} Cannot SELL or CLOSE position in {ticker}")
        print(f"   → You don't currently own any shares of {ticker}")
        print("   → To sell a stock, you must buy it first")
        print(f"\n   💡 Did you mean to SHORT {ticker}? (bet it will go down)")
        print("      Short selling is not currently supported by this system.")

    def _handle_hold_with_intent(self, decision, user_wants_buy: bool):
        """Handle HOLD signal when user explicitly wants to trade."""
        suggestion = decision.suggestion

        print(f"\n{MSG.EMOJI.get('info', 'ℹ️')} SIGNALS INCONCLUSIVE")
        print("   → Technical indicators suggest: HOLD (no clear direction)")
        print(f"   → You requested: {'BUY' if user_wants_buy else 'SELL'}")

        # Asked in AUTO too: this trade comes from the words typed, not from the signal
        side = "BUY" if user_wants_buy else "SELL"
        answer = input(f"\n   Prepare a {side} order anyway? [yes/no]: ").strip().lower()
        if answer not in ("yes", "y"):
            print(f"\n{MSG.EMOJI['info']} No order placed.")
            return None

        current_price = getattr(suggestion, "current_price", None)
        if current_price is None or current_price <= 0:
            indicators = getattr(suggestion, "indicators", {})
            current_price = indicators.get("current_price", 0.0)

        if not current_price or current_price <= 0:
            print(f"\n{MSG.EMOJI['error']} Cannot determine current price for {suggestion.ticker}")
            print("   → Try refreshing market data or check during market hours")
            return None

        # Get mode params
        try:
            from src.core.trading_modes import get_mode_manager

            mode_params = get_mode_manager().get_parameters()
            stop_pct = mode_params.stop_loss
            target_pct = mode_params.take_profit
            position_size_pct = mode_params.position_size
        except Exception:
            stop_pct = 0.05
            target_pct = 0.10
            position_size_pct = 0.05

        entry_price = round(current_price, 2)

        if user_wants_buy:
            decision.suggestion.signal = Signal.BUY
            stop_loss = round(current_price * (1 - stop_pct), 2)
            take_profit = round(current_price * (1 + target_pct), 2)
        else:
            decision.suggestion.signal = Signal.SELL
            stop_loss = round(current_price * (1 + stop_pct), 2)
            take_profit = round(current_price * (1 - target_pct), 2)

        decision.suggestion.entry_price = entry_price
        decision.suggestion.stop_loss = stop_loss
        decision.suggestion.take_profit = take_profit

        # Calculate quantity if needed
        if decision.suggestion.recommended_quantity == 0:
            self._calculate_quantity(
                decision.suggestion, entry_price, stop_loss, take_profit, position_size_pct
            )

        # Clear stale warnings
        decision.suggestion.warnings = [
            w for w in decision.suggestion.warnings if "No entry price" not in w
        ]

        self._print_adjusted_trade_plan(decision.suggestion, "BUY" if user_wants_buy else "SELL")
        return decision

    def _invert_stop_target_for_buy(self, suggestion) -> None:
        """Invert stop/target for BUY when originally calculated for SELL."""
        entry = suggestion.entry_price
        old_stop = suggestion.stop_loss
        old_target = suggestion.take_profit

        stop_distance = abs(old_stop - entry)
        target_distance = abs(old_target - entry)

        suggestion.stop_loss = round(entry - stop_distance, 2)
        suggestion.take_profit = round(entry + target_distance, 2)

    def _print_adjusted_trade_plan(self, suggestion, direction: str) -> None:
        """Print adjusted trade plan after signal override."""
        print(f"\n   📊 Adjusted for {direction}:")
        print(f"      Entry:  ${suggestion.entry_price:.2f}")
        print(
            f"      Stop:   ${suggestion.stop_loss:.2f} ({calc_pct(suggestion.entry_price, suggestion.stop_loss):.1f}%)"
        )
        print(
            f"      Target: ${suggestion.take_profit:.2f} ({calc_pct(suggestion.entry_price, suggestion.take_profit):.1f}%)"
        )
        print(f"      Quantity: {suggestion.recommended_quantity} shares")

    def _calculate_quantity(
        self, suggestion, entry_price, stop_loss, take_profit, position_size_pct
    ) -> None:
        """Calculate quantity based on buying power and position size."""
        try:
            if self.account_monitor:
                account = self.account_monitor.get_account_info()
                buying_power = float(account.get("buying_power", 0))
                trade_value = buying_power * position_size_pct
                quantity = int(trade_value / entry_price)
                if quantity > 0:
                    suggestion.recommended_quantity = quantity
                    max_loss = quantity * abs(entry_price - stop_loss)
                    suggestion.max_loss_usd = round(max_loss, 2)
                    potential_gain = quantity * abs(take_profit - entry_price)
                    if max_loss > 0:
                        suggestion.risk_reward_ratio = round(potential_gain / max_loss, 2)
        except Exception as e:
            logger.debug(f"Could not calculate quantity: {e}")

    def _check_position_for_ticker(self, ticker: str) -> Optional[dict]:
        """
        Check if user currently holds a position in the ticker.

        Args:
            ticker: Stock symbol to check

        Returns:
            Position dict if found, None otherwise
        """
        try:
            if not self.account_monitor:
                return None

            positions = self.account_monitor.get_positions()
            return next((p for p in positions if p.get("symbol") == ticker), None)
        except (AttributeError, ValueError, OSError) as e:
            logger.warning(f"Failed to check position for {ticker}: {e}")
            return None

    def _get_confirmation(self) -> bool:
        """
        Get user confirmation.

        Returns:
            True if user approves, False otherwise
        """
        while True:
            response = input(MSG.CONFIRM_PROMPT).strip().lower()

            if response in ["yes", "y"]:
                return True
            elif response in ["no", "n"]:
                return False
            else:
                print(MSG.CONFIRM_INVALID)

    def _update_local_state_after_trade(self, decision, result) -> None:
        """
        Update local state after successful trade.

        Issue #385: Bracket Order Stop-Loss Not Logged to Local State on Placement

        Args:
            decision: TradeDecision with suggestion
            result: OrderResult from execution
        """
        if not result.success:
            return

        if not self.trading_cycle:
            logger.warning("No trading_cycle available - cannot update local state")
            return

        try:
            suggestion = decision.suggestion
            symbol = suggestion.ticker
            quantity = result.quantity or suggestion.recommended_quantity

            # A routine may have saved since this session loaded the state: start from the
            # file, or its stops and alert history would be overwritten (#632)
            self.trading_cycle.load_local_state()
            positions = self.trading_cycle.local_state.setdefault("positions", {})

            if suggestion.signal.value.upper() == "SELL":
                position = positions.get(symbol)
                if position:
                    remaining = position.get("quantity", 0) - quantity
                    if remaining > 0:
                        position["quantity"] = remaining
                    else:
                        del positions[symbol]
                self.trading_cycle.save_local_state()
                logger.info(f"Updated local state for {symbol} after selling {quantity}")
                return

            positions[symbol] = {
                "entry_price": suggestion.entry_price,
                "quantity": quantity,
                "entry_time": now_iso(),
                "source": "CLI_TRADE",
                "stop_price": suggestion.stop_loss,
                "target_price": suggestion.take_profit,
                "order_id": result.entry_order_id,
            }

            self.trading_cycle.save_local_state()

            logger.info(
                f"Updated local state for {symbol}: "
                f"stop=${suggestion.stop_loss:.2f}, target=${suggestion.take_profit:.2f}"
            )

        except (AttributeError, TypeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to update local state after trade: {e}")
