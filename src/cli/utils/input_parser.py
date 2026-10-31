"""
Input Parser Utilities - Parse user intent and extract symbols from input.

Issue #436: Extracted from cli_session.py for better modularity.

This module provides input parsing functions for:
- Detecting user intent (buy/sell/review)
- Extracting ticker symbols from natural language
"""

import re
from typing import Dict, Optional


def detect_user_intent(user_input: str) -> Optional[str]:
    """
    Detect explicit user intent from input.

    Issue #347: Respects user intent when signals disagree.

    Args:
        user_input: User's natural language input

    Returns:
        "buy" if user explicitly wants to buy/go long
        "sell" if user explicitly wants to sell/close
        None if no explicit intent (just querying/analyzing)

    Example:
        >>> detect_user_intent("buy some AAPL")
        'buy'
        >>> detect_user_intent("should I sell TSLA?")
        None
        >>> detect_user_intent("short NVDA")
        'sell'
    """
    input_lower = user_input.lower()

    # Buy/long indicators
    buy_indicators = [
        "buy",
        "long",
        "go long",
        "going long",
        "bullish",
        "bet it goes up",
        "think it will rise",
        "upside",
        "get ",  # Note: space to avoid 'target', 'forget'
        "acquire",
        "purchase",
        "pick up",
        "grab",
    ]
    if any(indicator in input_lower for indicator in buy_indicators):
        return "buy"

    # Sell/close indicators
    sell_indicators = [
        "sell",
        "short",
        "shorting",
        "go short",
        "exit",
        "close",
        "get out",
        "dump",
        "liquidate",
        "cash out",
        "bet against",
        "profit from decline",
    ]
    if any(indicator in input_lower for indicator in sell_indicators):
        return "sell"

    # Review/analyze indicators (no explicit action)
    review_indicators = [
        "analyze",
        "analysis",
        "review",
        "check",
        "look at",
        "what about",
        "how is",
        "should i",
        "is it good",
    ]
    if any(indicator in input_lower for indicator in review_indicators):
        return None  # Just querying, no explicit intent

    return None  # Default: no explicit intent


# Common words that look like tickers in a query; "$NOW" still means the ticker
_NOT_TICKERS = frozenset(
    "A ALL AM AN AND ANY ARE AT BIG BOTH BY CAN DO EVERY FOR FROM GET HOW I ID IN IS IT LEVEL LIST ME "
    "MY NOW OF ON ONE OPEN ORDER OUT S SET SHOW SMALL SOME STOP THAT THE THESE THIS THOSE TO TODAY "
    "WE WHAT WITH YOUR".split()
)


def extract_ticker_from_query(user_input: str) -> Optional[str]:
    """
    Extract ticker symbol from natural language query.

    Issue #348: Parse user queries like 'show orders for AAPL'
    Issue #436: Extracted from cli_session.py for modularity.
    Issue #633: "my orders for aapl" is AAPL, not MY: in order, a $ prefix, the word after
    for/on/of/in, a word written in capitals, then any other word that isn't a common one.

    Args:
        user_input: User's natural language input

    Returns:
        Ticker symbol (uppercase) or None if not found

    Example:
        >>> extract_ticker_from_query("show orders for AAPL")
        'AAPL'
        >>> extract_ticker_from_query("what's the stop on $TSLA?")
        'TSLA'
        >>> extract_ticker_from_query("cancel my orders for aapl")
        'AAPL'
        >>> extract_ticker_from_query("show me the orders")
        None
    """
    explicit = re.search(r"\$([A-Za-z]{1,5})\b", user_input)
    if explicit:
        return explicit.group(1).upper()

    candidates = (
        re.findall(r"\b(?:for|on|of|in)\s+([A-Za-z]{1,5})\b", user_input, re.IGNORECASE)
        + re.findall(r"\b([A-Z]{1,5})\b", user_input)
        + re.findall(r"\b([A-Za-z]{1,5})\b", user_input)
    )
    for candidate in candidates:
        if candidate.upper() not in _NOT_TICKERS:
            return candidate.upper()
    return None


_CANCEL_VERB = re.compile(r"\b(cancel|delete|remove)\b", re.IGNORECASE)
_ORDER_WORD = re.compile(r"\borders?\b", re.IGNORECASE)
_ALL_WORD = re.compile(r"\ball\b", re.IGNORECASE)
# The start of an order ID (a UUID) or all of it, with a digit so "deadbeef" isn't one; a
# date like 2026-10-30 doesn't fit
_ORDER_ID = re.compile(r"\b(?=[a-f-]*[0-9])[0-9a-f]{8}(?:-[0-9a-f-]+)?\b", re.IGNORECASE)


def is_cancel_request(user_input: str) -> bool:
    """
    Whether the input asks to cancel orders. Whole words only (#633): "delete all my alerts"
    and "remove the small one" are not cancel requests.
    """
    if user_input.strip().lower() == "cancel all":
        return True
    return bool(_CANCEL_VERB.search(user_input) and _ORDER_WORD.search(user_input))


def parse_cancel_request(user_input: str) -> Dict[str, str]:
    """
    What a cancel request applies to. A named order or symbol wins over the word "all", so
    "cancel all AAPL orders" is AAPL's orders.

    Returns:
        {"order_id": ...}, {"symbol": ...}, {"all": "all"}, or {} if it names no target
    """
    order_id = _ORDER_ID.search(user_input)
    if order_id:
        return {"order_id": order_id.group(0).lower()}
    ticker = extract_ticker_from_query(user_input)
    if ticker:
        return {"symbol": ticker}
    if _ALL_WORD.search(user_input):
        return {"all": "all"}
    return {}


# Buy/sell indicators exported for use by other modules
BUY_INDICATORS = [
    "buy",
    "long",
    "go long",
    "going long",
    "bullish",
    "bet it goes up",
    "think it will rise",
    "upside",
    "get ",
    "acquire",
    "purchase",
    "pick up",
    "grab",
]

SELL_INDICATORS = [
    "sell",
    "short",
    "shorting",
    "go short",
    "exit",
    "close",
    "get out",
    "dump",
    "liquidate",
    "cash out",
    "bet against",
    "profit from decline",
    "make money when it falls",
]


__all__ = [
    "detect_user_intent",
    "extract_ticker_from_query",
    "is_cancel_request",
    "parse_cancel_request",
    "BUY_INDICATORS",
    "SELL_INDICATORS",
]
