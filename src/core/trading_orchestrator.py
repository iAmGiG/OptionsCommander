"""
TradingOrchestrator - Central coordinator for the trading system.

This is the main business logic layer that all presentation layers (CLI, GUI, API) use.
It coordinates the workflow: parse → analyze → risk check → suggest → execute.
"""

import logging
import uuid
from typing import Optional

from .interfaces import ExecutionManager, InputParser, RiskManager, StrategyAnalyzer
from .models import (
    AnalysisResult,
    OrderResult,
    OrderType,
    RiskAssessment,
    TimeInForce,
    TradeDecision,
    TradeRequest,
    TradeSuggestion,
)
from src.utils.date_utils import get_datetime_now

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """
    Central coordinator for all trading operations.

    All UIs (CLI, GUI, Web API) use this orchestrator to process trades.
    This ensures consistent business logic regardless of presentation layer.

    Architecture:
        User Input → InputParser → StrategyAnalyzer → RiskManager → User Confirmation → ExecutionManager

    The orchestrator:
    1. Parses user input into TradeRequest
    2. Routes to appropriate strategy analyzer
    3. Assesses risk and position sizing
    4. Creates suggestion for user
    5. Executes trade on confirmation
    6. Tracks session state
    """

    def __init__(
        self,
        input_parser: InputParser,
        strategy_analyzer: StrategyAnalyzer,
        risk_manager: RiskManager,
        execution_manager: ExecutionManager,
        session_store: Optional[object] = None,  # SessionStore interface (to be created)
    ):
        """
        Initialize the orchestrator with pluggable components.

        Args:
            input_parser: Component to parse user input
            strategy_analyzer: Component to analyze trades
            risk_manager: Component to assess risk
            execution_manager: Component to execute orders
            session_store: Optional session persistence (SQLite)
        """
        self.parser = input_parser
        self.analyzer = strategy_analyzer
        self.risk = risk_manager
        self.executor = execution_manager
        self.session_store = session_store

        logger.info(
            f"TradingOrchestrator initialized with "
            f"analyzer={strategy_analyzer.name}, "
            f"parser={type(input_parser).__name__}"
        )

    async def process_request(
        self,
        user_input: str,
        user_id: str = "default",
        request: Optional[TradeRequest] = None,
    ) -> TradeDecision:
        """
        Main workflow: Process user request and generate trade suggestion.

        This is the primary entry point for all UIs.

        Args:
            user_input: Raw user input (e.g., "is SPY at 600 good?")
            user_id: User ID for portfolio lookups
            request: The input already parsed; parsed here if not given

        Returns:
            TradeDecision with suggestion (awaiting user confirmation)

        Raises:
            ValueError: If input cannot be parsed or analyzed
        """
        logger.info(f"Processing request from user {user_id}: {user_input}")

        try:
            # Step 1: Parse input
            if request is None:
                request = await self.parser.parse(user_input, user_id)
            logger.debug(f"Parsed request: {request}")

            # Step 2: Validate request
            is_valid = await self.parser.validate(request)
            if not is_valid:
                # Check if this looks like a portfolio query (no ticker)
                if not request.ticker or request.ticker.strip() == "":
                    portfolio_keywords = [
                        "position",
                        "portfolio",
                        "holding",
                        "open",
                        "what do i have",
                    ]
                    if any(keyword in user_input.lower() for keyword in portfolio_keywords):
                        raise ValueError(
                            "Portfolio queries not yet supported. "
                            "Please ask about a specific ticker (e.g., 'any positions in AAPL?')"
                        )

                raise ValueError(f"Invalid request: {request}")

            # Nothing to analyze: don't send an empty symbol to the data sources (#636)
            if not request.ticker:
                raise ValueError("No symbol given")

            # Step 3: Strategy analysis
            analysis = await self.analyzer.analyze(request)
            logger.debug(
                f"Analysis complete: {analysis.signal.value} "
                f"with {analysis.confidence:.1%} confidence"
            )

            # Step 4: Risk assessment
            risk_assessment = await self.risk.assess(request, analysis, user_id)
            logger.debug(
                f"Risk assessment: {risk_assessment.recommended_quantity} shares, "
                f"{risk_assessment.portfolio_pct:.1f}% of portfolio"
            )

            # Step 5: Create suggestion
            suggestion = self._create_suggestion(request, analysis, risk_assessment)

            # Step 6: Save to session (if session store available)
            if self.session_store:
                await self.session_store.save_suggestion(user_id, suggestion)

            # Step 7: Return decision (awaiting user confirmation)
            decision = TradeDecision(
                suggestion=suggestion,
                approved=False,
                session_id=suggestion.suggestion_id,
                user_id=user_id,
            )

            logger.info(
                f"Suggestion created: {suggestion.signal.value} "
                f"{suggestion.recommended_quantity} shares {suggestion.ticker}"
            )

            return decision

        except Exception as e:
            # Log error at DEBUG level only (not shown to users)
            logger.debug(f"Error processing request: {e}", exc_info=True)
            raise

    async def execute_decision(
        self, decision: TradeDecision, modifications: Optional[dict] = None
    ) -> OrderResult:
        """
        Execute an approved trade decision.

        Args:
            decision: The trade decision (with approved=True)
            modifications: Optional modifications to quantity/price

        Returns:
            OrderResult with order IDs and execution details. success=False with
            message "Trade refused by risk check" when the risk check blocks the trade.

        Raises:
            ValueError: If decision not approved or invalid modifications
        """
        if not decision.approved:
            raise ValueError("Cannot execute unapproved decision")

        logger.info(
            f"Executing decision for {decision.suggestion.ticker}: "
            f"{decision.suggestion.recommended_quantity} shares"
        )

        try:
            # Apply modifications if provided
            if modifications:
                decision = self._apply_modifications(decision, modifications)

            refusal = await self._risk_refusal(decision)
            if refusal:
                logger.warning(
                    f"Trade refused by risk check for {decision.suggestion.ticker}: {refusal}"
                )
                return OrderResult(
                    success=False,
                    ticker=decision.suggestion.ticker,
                    quantity=decision.suggestion.recommended_quantity,
                    message="Trade refused by risk check",
                    error=refusal,
                )

            # Execute via execution manager
            result = await self.executor.execute_trade(decision.suggestion, decision)

            # Save to session (if available)
            if self.session_store:
                await self.session_store.save_execution(decision.user_id, decision, result)

            logger.info(
                f"Execution complete: {result.success}, entry_order={result.entry_order_id}"
            )

            return result

        except Exception as e:
            # Log the full error with traceback (for debugging)
            logger.error(f"Error executing trade: {e}", exc_info=True)

            # Return error result instead of re-raising (prevents traceback showing to user)
            return OrderResult(
                success=False,
                ticker=decision.suggestion.ticker,
                quantity=decision.suggestion.recommended_quantity,
                message="Trade execution failed",
                error=str(e),
            )

    def _create_suggestion(
        self, request: TradeRequest, analysis: AnalysisResult, risk_assessment: RiskAssessment
    ) -> TradeSuggestion:
        """
        Merge analysis and risk assessment into actionable suggestion.

        Args:
            request: Original trade request
            analysis: Strategy analysis result
            risk_assessment: Risk assessment result

        Returns:
            TradeSuggestion ready for user confirmation
        """
        # Generate unique suggestion ID
        suggestion_id = str(uuid.uuid4())[:8]

        # Use modified quantity if user specified, otherwise use risk manager's recommendation
        quantity = request.quantity if request.quantity else risk_assessment.recommended_quantity

        # Issue #344: Adjust entry price based on timing context
        entry_price = analysis.entry_price
        stop_loss = analysis.stop_loss
        take_profit = analysis.take_profit
        reasoning = list(analysis.reasoning)  # Copy to avoid modifying original

        # Analysis timeframe context for user display
        # Currently using daily (1D) candles for MACD+RSI analysis
        # (Validated VoterAgent uses 1D - configurable in future iterations)
        analysis_timeframe = "1D"

        # Issue #474/#475: Handle HOLD signals with timing overrides
        # If entry_price is None (HOLD signal), get current_price from indicators
        base_price = entry_price
        if base_price is None:
            base_price = analysis.indicators.get("current_price", 0.0)
            if base_price and base_price > 0:
                entry_price = round(base_price, 2)
                # Generate default stop/target for user override
                from src.core.trading_modes import get_mode_manager

                mode_params = get_mode_manager().get_parameters()
                stop_loss = round(base_price * (1 - mode_params.stop_loss), 2)
                take_profit = round(base_price * (1 + mode_params.take_profit), 2)

        if request.timing in ("pullback", "dip") and base_price and base_price > 0:
            # Suggest entry 2.5% below current price for pullback/dip timing
            pullback_pct = 0.025
            entry_price = round(base_price * (1 - pullback_pct), 2)
            # Adjust stop loss proportionally (maintain same % distance)
            if analysis.stop_loss and analysis.entry_price:
                stop_distance_pct = (
                    analysis.entry_price - analysis.stop_loss
                ) / analysis.entry_price
            else:
                stop_distance_pct = 0.08  # Default 8% stop
            stop_loss = round(entry_price * (1 - stop_distance_pct), 2)
            reasoning.insert(
                0,
                f"⏳ Pullback entry ({analysis_timeframe} analysis): "
                f"limit @ ${entry_price} (2.5% below current, GTC)",
            )
            logger.info(f"Timing=pullback: adjusted entry from ${base_price} to ${entry_price}")
        elif request.timing == "breakout" and base_price and base_price > 0:
            # Suggest entry 1.5% above current price for breakout timing
            breakout_pct = 0.015
            entry_price = round(base_price * (1 + breakout_pct), 2)
            # Adjust take profit proportionally
            if analysis.take_profit and analysis.entry_price:
                target_distance_pct = (
                    analysis.take_profit - analysis.entry_price
                ) / analysis.entry_price
            else:
                target_distance_pct = 0.20  # Default 20% target
            take_profit = round(entry_price * (1 + target_distance_pct), 2)
            reasoning.insert(
                0,
                f"🚀 Breakout entry ({analysis_timeframe} analysis): "
                f"limit @ ${entry_price} (1.5% above current, GTC)",
            )
            logger.info(f"Timing=breakout: adjusted entry from ${base_price} to ${entry_price}")
        elif request.price:
            # User specified exact price - use it
            entry_price = request.price
            reasoning.insert(0, f"📍 Using your specified entry price: ${entry_price}")

        # Determine order type based on timing context (Issue #344 fix)
        # LIMIT orders for pullback/dip/breakout wait for the price to reach entry_price
        # MARKET orders execute immediately at current price
        if request.timing in ("pullback", "dip", "breakout", "limit"):
            order_type = OrderType.LIMIT
            logger.info(f"Timing={request.timing}: Using LIMIT order at ${entry_price} (GTC)")
        else:
            order_type = OrderType.MARKET
            logger.info(f"Timing={request.timing or 'now'}: Using MARKET order")

        # Create suggestion
        suggestion = TradeSuggestion(
            # From analysis
            signal=analysis.signal,
            confidence=analysis.confidence,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=reasoning,
            # From risk assessment
            recommended_quantity=quantity,
            portfolio_pct=risk_assessment.portfolio_pct,
            max_loss_usd=risk_assessment.max_loss_usd,
            risk_reward_ratio=risk_assessment.risk_reward_ratio,
            warnings=risk_assessment.warnings,
            # Order details
            ticker=request.ticker,
            order_type=order_type,  # LIMIT for pullback/breakout, MARKET otherwise
            time_in_force=TimeInForce.GTC,  # Always GTC per requirements
            # Metadata
            suggestion_id=suggestion_id,
            timestamp=get_datetime_now(),
            # Issue #474: Pass indicators for HOLD signal overrides
            indicators=analysis.indicators,
            risk_block_reasons=list(risk_assessment.block_reasons),
        )

        return suggestion

    async def _risk_refusal(self, decision: TradeDecision) -> Optional[str]:
        """
        Why the risk check refuses this decision's trade, or None if it may go ahead.

        A suggestion refused when it was made stays refused. Otherwise the trade is
        assessed again as it will be placed, since overrides and edits made after the
        suggestion (e.g. a HOLD turned into a BUY) change what the first check covered.
        """
        suggestion = decision.suggestion
        if suggestion.risk_block_reasons:
            return "; ".join(suggestion.risk_block_reasons)

        entry_price = decision.modified_entry or suggestion.entry_price
        request = TradeRequest(
            ticker=suggestion.ticker,
            action=suggestion.signal.value,
            quantity=decision.modified_quantity or suggestion.recommended_quantity,
            price=entry_price,
        )
        analysis = AnalysisResult(
            signal=suggestion.signal,
            confidence=suggestion.confidence,
            entry_price=entry_price,
            stop_loss=decision.modified_stop or suggestion.stop_loss,
            take_profit=decision.modified_target or suggestion.take_profit,
        )
        assessment = await self.risk.assess(request, analysis, decision.user_id)
        if assessment.approved:
            return None
        return "; ".join(assessment.block_reasons or assessment.warnings) or "not approved"

    def _apply_modifications(self, decision: TradeDecision, modifications: dict) -> TradeDecision:
        """
        Apply user modifications to a decision.

        Args:
            decision: Original decision
            modifications: Dict with keys: quantity, entry, stop, target

        Returns:
            Modified TradeDecision
        """
        if "quantity" in modifications:
            decision.modified_quantity = modifications["quantity"]

        if "entry" in modifications:
            decision.modified_entry = modifications["entry"]

        if "stop" in modifications:
            decision.modified_stop = modifications["stop"]

        if "target" in modifications:
            decision.modified_target = modifications["target"]

        decision.decision_timestamp = get_datetime_now()

        return decision

    async def get_session_history(self, user_id: str, limit: int = 10) -> list:
        """
        Get recent suggestions for a user.

        Args:
            user_id: User ID
            limit: Number of recent suggestions to retrieve

        Returns:
            List of recent TradeSuggestion objects
        """
        if not self.session_store:
            return []

        return await self.session_store.get_recent_suggestions(user_id, limit)

    def plan_close(self, ticker: str, quantity: Optional[int] = None) -> Optional[dict]:
        """
        What selling a long position would take, without changing anything.

        Args:
            ticker: Stock symbol
            quantity: Shares to sell; all of them if None

        Returns:
            The plan to pass to close_position, or None if there's no long position
        """
        return self.executor.plan_close(ticker, quantity)

    async def close_position(self, plan: dict) -> OrderResult:
        """
        Sell a position at market. This is the user's own exit, so it isn't subject to the
        signal or the risk check, which limit new exposure.

        Args:
            plan: From plan_close

        Returns:
            OrderResult with the outcome
        """
        return await self.executor.close_position(plan)

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        return await self.executor.cancel_order(order_id)

    async def get_order_status(self, order_id: str) -> dict:
        """
        Get status of an order.

        Args:
            order_id: Order ID

        Returns:
            Dict with order status details
        """
        return await self.executor.get_order_status(order_id)
