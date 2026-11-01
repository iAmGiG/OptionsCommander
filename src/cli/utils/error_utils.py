"""
Error handling utilities for CLI.

Issue #521: Extract common error sanitization logic.
"""

import logging

logger = logging.getLogger(__name__)


def sanitize_error_message(error: Exception) -> str:
    """
    Sanitize error messages to remove API keys and sensitive details.

    Returns simple user-friendly messages based on error type.
    Full details are logged separately for debugging.
    """
    error_str = str(error).lower()

    # The model service, not the input (#636)
    if "model service unavailable" in error_str:
        return (
            "The language model isn't responding, so typed requests can't be read right now. "
            "Nothing done. Slash commands still work."
        )

    if "no symbol given" in error_str:
        return "Which symbol? For example: how does SPY look? Nothing done."

    # API key patterns to sanitize
    if "configuration error" in error_str or (
        "api" in error_str
        and ("key" in error_str or "401" in error_str or "authentication" in error_str)
    ):
        return "Configuration error. Nothing done."

    # Parse errors
    if "could not parse" in error_str or "parse error" in error_str:
        return "Didn't understand that. Nothing done."

    # Ticker validation errors - provide actionable feedback
    if "ticker not found" in error_str:
        return "Symbol not found. It may not be available via your broker or data provider."

    if ("asset" in error_str and "not found" in error_str) or "symbol" in error_str:
        return "Symbol not recognized. Check the ticker spelling or try a US-listed stock."

    # Data availability errors
    if (
        "no data" in error_str
        or "insufficient data" in error_str
        or "data unavailable" in error_str
        or "market data may be unavailable" in error_str
        or "invalid entry price" in error_str
    ):
        return "Not enough market data available for analysis. Try a more liquid symbol."

    # Invalid request errors
    if "invalid request" in error_str or "invalid format" in error_str:
        return "Didn't understand that. Nothing done."

    # Format/type errors (likely None values in display)
    if "typeerror" in error_str or "nonetype" in error_str or "unsupported format" in error_str:
        logger.error(f"Display format error: {error}")
        return "Analysis failed - missing price data. Try again or check the ticker."

    # Generic fallback - log the actual error for debugging
    logger.warning(f"Unhandled error type: {type(error).__name__}: {error}")
    return f"Something went wrong. Error: {type(error).__name__}"
