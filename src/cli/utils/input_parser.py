"""
Input Parser Utilities - Parse user intent and extract symbols from input.

Issue #436: Extracted from cli_session.py for better modularity.

This module provides input parsing functions for:
- Detecting user intent (buy/sell/review)
- Extracting ticker symbols from natural language
"""

import re
from typing import Dict, Optional

# Whole words only (#632): "buyback", "belong" and "how long" say nothing about a trade
_QUESTION = re.compile(
    r"^(?:how|what|why|when|should|is|are|do|does)\b|\?\s*$"
    r"|\b(?:should i|what about|how is|is it good|analy[sz]e|analysis|review|check|look at)\b"
)
_BUY_INTENT = re.compile(
    r"^long\b|\bto long\b|\b(?:buy|buying|go long|going long|bullish|bet it goes up"
    r"|think it will rise|upside|acquire|purchase|pick up|grab)\b"
)
_SELL_INTENT = re.compile(
    r"^short\b|\bto short\b|(?<!the )(?<!at )\b(?:close|closing)\b"
    r"|\b(?:sell|selling|shorting|go short|going short|exit|get out|get me out|dump|liquidate"
    r"|cash out|bet against|profit from decline|make money when it falls)\b"
)


# Opening a short, which isn't supported; these also count as sell intent above
_SHORT_INTENT = re.compile(
    r"^short\b|\bto short\b|\b(?:shorting|go short|going short|bet against"
    r"|profit from decline|make money when it falls)\b"
)


def is_question(user_input: str) -> bool:
    """Whether the input asks about a trade, rather than asking for one."""
    return bool(_QUESTION.search(user_input.lower().strip()))


def is_short_request(user_input: str) -> bool:
    """Whether the input asks to open a short, as opposed to selling what's held."""
    return bool(_SHORT_INTENT.search(user_input.lower().strip()))


def detect_user_intent(user_input: str) -> Optional[str]:
    """
    Detect explicit user intent from input.

    Issue #347: Respects user intent when signals disagree.
    Issue #632: A question or analysis request states no intent, and neither does input
    naming both sides.

    Args:
        user_input: User's natural language input

    Returns:
        "buy" if user explicitly wants to buy/go long
        "sell" if user explicitly wants to sell/close
        None if no explicit intent (just querying/analyzing)

    Example:
        >>> detect_user_intent("buy some AAPL")
        'buy'
        >>> detect_user_intent("should I get out of TSLA?")
        >>> detect_user_intent("short NVDA")
        'sell'
    """
    if is_question(user_input):
        return None

    input_lower = user_input.lower().strip()
    wants_buy = bool(_BUY_INTENT.search(input_lower))
    wants_sell = bool(_SELL_INTENT.search(input_lower))
    if wants_buy == wants_sell:
        return None
    return "buy" if wants_buy else "sell"


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


_SCHEDULER_WORDS = re.compile(
    r"\b(?:scheduler|schedule|routines?|(?:morning|evening) (?:report|run|summary))\b",
    re.IGNORECASE,
)
_MODE_COMMAND = re.compile(
    r"\b(?:trading|risk|show|current|set|change|switch|what|which"
    r"|conservative|moderate|aggressive) mode\b"
    r"|^mode\b|^(?:use|set|switch to) (?:conservative|moderate|aggressive)$",
    re.IGNORECASE,
)
MODE_NAMES = ("conservative", "moderate", "aggressive")


def is_scheduler_request(user_input: str) -> bool:
    """
    Whether the input asks about the scheduler. Whole words (#636): "buy 10 AAPL this
    morning" is a trade, and a word naming a trade wins.
    """
    if user_input.strip().lower() in ("morning", "evening"):
        return True
    return bool(_SCHEDULER_WORDS.search(user_input)) and detect_user_intent(user_input) is None


def is_mode_request(user_input: str) -> bool:
    """
    Whether the input is a trading mode command, not a sentence that uses a mode's name
    (#636): "buy SPY aggressively" is a trade.
    """
    if user_input.strip().lower() in MODE_NAMES:
        return True
    return bool(_MODE_COMMAND.search(user_input.strip()))


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


__all__ = [
    "detect_user_intent",
    "is_question",
    "is_short_request",
    "extract_ticker_from_query",
    "is_cancel_request",
    "is_mode_request",
    "is_scheduler_request",
    "MODE_NAMES",
    "parse_cancel_request",
]
