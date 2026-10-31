"""
Natural language parser for trade requests, on Microsoft Agent Framework (#606).

One LLM call offers the parse_trade_request tool, and the arguments of the model's
tool call are the parsed request. The raw chat client returns tool calls without
running them (the standard client would run the tool and ask the model again).

Issue #406: Consolidate LLM services (this was the AutoGen-based parser)
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional

from agent_framework import FunctionTool, Message
from agent_framework.openai import RawOpenAIChatCompletionClient

from core.interfaces import InputParser
from core.models import AssetType, TradeRequest

from src.utils.config_loader import ConfigLoader

logger = logging.getLogger(__name__)

# Load configuration (shared with base_agent.py pattern)
config_loader = ConfigLoader()

# Get API key and model from environment or config
_api_key = os.getenv("OPEN_AI_KEY", config_loader.get("OPEN_AI_KEY"))
_tool_model = os.getenv(
    "OPENAI_TOOL_MODEL",
    config_loader.get("OPENAI_TOOL_MODEL", "gpt-4o-mini"),
)


class RequestType(str, Enum):
    """Type of user request."""

    TRADE = "trade"
    STATUS_QUERY = "status_query"


class ActionType(str, Enum):
    """User's intended action."""

    REVIEW = "review"
    BUY = "buy"
    SELL = "sell"


@dataclass
class ParsedTradeRequest:
    """
    Structured output from parsing user input.

    This dataclass defines the schema that the LLM will extract.
    FunctionTool generates the JSON schema from these type hints.
    """

    request_type: Literal["trade", "status_query"]
    ticker: str
    action: Literal["review", "buy", "sell"]
    quantity: Optional[int] = None
    price: Optional[float] = None
    asset_type: Literal["stock", "option"] = "stock"
    # Issue #475: Entry timing context
    timing: Optional[Literal["now", "pullback", "dip", "breakout", "limit"]] = None


def _parse_trade_request(
    request_type: Literal["trade", "status_query"],
    ticker: str,
    action: Literal["review", "buy", "sell"],
    quantity: Optional[int] = None,
    price: Optional[float] = None,
    asset_type: Literal["stock", "option"] = "stock",
    timing: Optional[Literal["now", "pullback", "dip", "breakout", "limit"]] = None,
) -> dict:
    """
    Parse user's trade request into structured format.

    This function is wrapped in a FunctionTool to create a tool
    that the LLM can call. The type hints define the JSON schema.

    Args:
        request_type: "trade" for buy/sell/analyze, "status_query" for account status
        ticker: Stock ticker symbol (e.g., SPY, AAPL). Empty for status queries.
        action: User's intent - review (analyze), buy, or sell
        quantity: Number of shares (optional)
        price: Entry price mentioned (optional)
        asset_type: "stock" (default) or "option"
        timing: Entry timing - "pullback", "dip", "breakout", "limit", or None for immediate

    Returns:
        Parsed request as dictionary
    """
    return {
        "request_type": request_type,
        "ticker": ticker.upper() if ticker else "",
        "action": action,
        "quantity": quantity,
        "price": price,
        "asset_type": asset_type,
        "timing": timing,
    }


# Nothing makes the model keep to the schema (#632): the tool is offered, never run, so its
# arguments are checked here. Anything but an explicit "buy" or "sell" is a review.
_TIMINGS = ("now", "pullback", "dip", "breakout", "limit")


def _action_from(value: Any) -> str:
    return value if value in (ActionType.BUY.value, ActionType.SELL.value) else "review"


def _request_type_from(value: Any) -> str:
    return RequestType.STATUS_QUERY.value if value == "status_query" else RequestType.TRADE.value


def _positive_number(value: Any) -> Optional[float]:
    """The value if it's a finite number above zero, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 < value < float("inf") else None


def _positive_int(value: Any) -> Optional[int]:
    """The value if it's a whole number above zero, else None: 50.0 is 50, 1.5 is not."""
    number = _positive_number(value)
    return int(number) if number is not None and number == int(number) else None


# The parse tool - schema auto-generated from type hints
PARSE_TOOL = FunctionTool(
    func=_parse_trade_request,
    name="parse_trade_request",
    description="Parse user's trade request into structured format",
)


class LLMParser(InputParser):
    """
    Parse natural language input with an LLM tool call (Microsoft Agent Framework).

    Examples:
    - "is SPY at 600 good?" -> TradeRequest(ticker="SPY", action="review", price=600)
    - "buy 50 AAPL" -> TradeRequest(ticker="AAPL", action="buy", quantity=50)
    - "any open orders?" -> TradeRequest(request_type="status_query")
    """

    def __init__(
        self,
        model_client: Optional[RawOpenAIChatCompletionClient] = None,
        model: str = None,
        api_key: str = None,
    ):
        """
        Initialize with an OpenAI chat client.

        Args:
            model_client: Pre-configured client (optional)
            model: Model name (default: gpt-4o-mini from config)
            api_key: API key (default: from config/env)
        """
        if model_client is not None:
            self.client = model_client
        else:
            # Create client using shared config pattern from base_agent.py
            key = api_key or _api_key
            if not key:
                raise ValueError(
                    "OpenAI API key not found. Set OPEN_AI_KEY environment variable "
                    "or update config.json"
                )

            self.client = RawOpenAIChatCompletionClient(model=model or _tool_model, api_key=key)

        self._tool = PARSE_TOOL
        # Deterministic for parsing; the tool is offered, never run
        self._options = {"temperature": 0.0, "max_tokens": 1024, "tools": [self._tool]}
        logger.info(f"LLMParser initialized with model: {model or _tool_model}")

    async def parse(self, user_input: str, user_id: Optional[str] = None) -> TradeRequest:
        """
        Parse user input into TradeRequest with one LLM tool call.

        Args:
            user_input: Raw user input
            user_id: Optional user ID (for future personalization)

        Returns:
            Parsed TradeRequest

        Raises:
            ValueError: If input cannot be parsed
        """
        # Auto-correct obvious errors first
        corrected_input = self._autocorrect(user_input)

        # Build prompt
        prompt = self._build_prompt(corrected_input)

        try:
            # Call the LLM, offering the parse tool
            response = await self.client.get_response(
                [Message("user", [prompt])], options=self._options
            )

            # Find the model's parse_trade_request call
            tool_call = next(
                (
                    content
                    for message in response.messages
                    for content in message.contents
                    if content.type == "function_call" and content.name == "parse_trade_request"
                ),
                None,
            )
            if tool_call is None:
                raise ValueError(f"No tool call in response for: {user_input}")

            # Its arguments are the parsed request
            args = tool_call.arguments
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                raise ValueError(f"Tool call arguments aren't an object for: {user_input}")

            # Create TradeRequest
            request = TradeRequest(
                ticker=str(args.get("ticker") or "").upper(),
                action=_action_from(args.get("action")),
                request_type=_request_type_from(args.get("request_type")),
                quantity=_positive_int(args.get("quantity")),
                price=_positive_number(args.get("price")),
                asset_type=AssetType(args.get("asset_type") or "stock"),
                timing=args.get("timing") if args.get("timing") in _TIMINGS else None,  # #475
                raw_input=user_input,
            )

            logger.info(
                f"Parsed: '{user_input}' -> {request.action} {request.ticker}"
                + (f" qty={request.quantity}" if request.quantity else "")
                + (f" @{request.price}" if request.price else "")
            )

            return request

        except Exception as e:
            # Catch specific OpenAI API auth errors
            error_str = str(e).lower()
            if "401" in error_str or "authentication" in error_str or "api key" in error_str:
                logger.error("OpenAI API authentication error", exc_info=True)
                raise ValueError("Configuration error") from e

            # Generic parse failure - simple message
            logger.debug(f"Parse error for input '{user_input}': {e}", exc_info=True)
            raise ValueError("Could not parse input") from e

    async def validate(self, request: TradeRequest) -> bool:
        """
        Validate parsed request.

        Args:
            request: Parsed TradeRequest

        Returns:
            True if valid
        """
        # Skip validation for status queries
        if request.request_type == "status_query":
            return True

        # Ticker validation (1-5 uppercase letters)
        if request.ticker and not re.match(r"^[A-Z]{1,5}$", request.ticker):
            logger.debug(f"Invalid ticker format: {request.ticker}")
            return False

        # Quantity validation
        if request.quantity is not None and request.quantity <= 0:
            logger.warning(f"Invalid quantity: {request.quantity}")
            return False

        # Price validation
        if request.price is not None and request.price <= 0:
            logger.warning(f"Invalid price: {request.price}")
            return False

        return True

    def _build_prompt(self, user_input: str) -> str:
        """Build the parsing prompt for the LLM."""
        return f"""Parse this user input into structured format:

User input: "{user_input}"

First, determine the request_type:
- "trade" = User wants to buy, sell, or analyze a specific ticker
  Examples: "buy AAPL", "is SPY good?", "sell my TSLA", "10 shares AAPL"
- "status_query" = User is asking about account status, orders, or positions
  Examples: "any open orders?", "what positions do I have?", "show my portfolio"
  Note: Words like "any", "what", "show", "check" at the start usually indicate status queries

If request_type is "trade", extract:
- ticker: Stock symbol (uppercase, 1-5 letters)
- action: IMPORTANT - Default to "review" (analyze) unless user explicitly says "buy" or "sell"
  * "buy AAPL" → action="buy"
  * "sell TSLA" → action="sell"
  * "10 AAPL" → action="review" (quantity alone = review, NOT buy!)
  * "AAPL" → action="review"
  * "is AAPL good?" → action="review"
- quantity: Number of shares if mentioned
- price: Price if mentioned (e.g., "at 600" means price=600)
- asset_type: "stock" (default) or "option"
- timing: Entry timing preference (Issue #475)
  * "pullback" or "on pullback" or "on a dip" → timing="pullback" (wait for lower price)
  * "dip" or "on the dip" → timing="dip" (same as pullback)
  * "breakout" or "on breakout" → timing="breakout" (wait for higher price)
  * "limit" or "limit order" → timing="limit" (use specified price as limit)
  * No timing mentioned → timing=null (execute immediately at market)

If request_type is "status_query":
- ticker: Empty string (unless asking about specific ticker's position)
- action: "review" (default)

Use the parse_trade_request function."""

    def _autocorrect(self, user_input: str) -> str:
        """
        Auto-correct obvious errors in user input.

        Examples:
        - "spy at 60" -> "SPY at 600" (common price typos)
        """
        corrected = user_input

        # Common price typos (SPY at 60 -> 600, QQQ at 50 -> 500)
        common_corrections = {
            r"\bspy at 60\b": "SPY at 600",
            r"\bspy at 6([0-9]{2})\b": r"SPY at 6\1",
            r"\bqqq at 50\b": "QQQ at 500",
            r"\bqqq at 5([0-9]{2})\b": r"QQQ at 5\1",
        }

        for pattern, replacement in common_corrections.items():
            corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)

        if corrected != user_input:
            logger.info(f"Auto-corrected: '{user_input}' -> '{corrected}'")

        return corrected


# The name before #606 (AutoGen); kept so existing imports work
AutoGenLLMParser = LLMParser
