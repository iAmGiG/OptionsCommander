"""
CLI Utilities - Support modules for the CLI session.

This module contains utility classes and functions used by cli_session.py
and other CLI components.

Modules:
- ticker_completer: Tab completion for ticker symbols (readline integration)
- faq: FAQ with features, how-to, and resources sections
- about_page: Status dashboard
- help_system: Help command handling and documentation
- suggestion_display: Display formatting for trade suggestions (Issue #436)
- input_parser: User intent detection and ticker extraction (Issue #436)
- trading_tips: Legacy tips module (deprecated, use faq instead)
"""

from src.cli.utils.about_page import display_about, show_about
from src.cli.utils.faq import display_faq, get_available_sections, load_faq, show_faq
from src.cli.utils.help_system import HelpSystem
from src.cli.utils.input_parser import (
    detect_user_intent,
    extract_ticker_from_query,
)
from src.cli.utils.suggestion_display import (
    calc_pct,
    display_position_context,
    display_result,
    display_suggestion,
    get_trade_direction,
)
from src.cli.utils.ticker_completer import (
    READLINE_AVAILABLE,
    TickerCompleter,
    get_ticker_completer,
    is_powershell,
    readline,
)

# Legacy trading_tips imports (deprecated - use faq module instead)
from src.cli.utils.trading_tips import (
    display_trading_tips,
    get_tips_dict,
    load_trading_tips,
    show_trading_tips,
)

__all__ = [
    # Ticker completer
    "READLINE_AVAILABLE",
    "readline",
    "TickerCompleter",
    "get_ticker_completer",
    "is_powershell",
    # FAQ (replaces tips)
    "load_faq",
    "show_faq",
    "display_faq",
    "get_available_sections",
    # About page
    "show_about",
    "display_about",
    # Legacy trading tips (deprecated)
    "load_trading_tips",
    "get_tips_dict",
    "show_trading_tips",
    "display_trading_tips",
    # Help system
    "HelpSystem",
    # Suggestion display
    "calc_pct",
    "get_trade_direction",
    "display_position_context",
    "display_suggestion",
    "display_result",
    # Input parser
    "detect_user_intent",
    "extract_ticker_from_query",
]
