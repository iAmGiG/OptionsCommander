"""
CLI Session - Interactive REPL for trading assistant.

Unified interactive CLI with LLM-driven routing for:
- Trade execution (buy/sell)
- Position alerts
- Scheduler management
- Portfolio status
"""

import atexit
import logging
import os
import re
import sys
from typing import Optional

import yaml

# Add imports for new features
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
# Import CLI messages configuration
from config_defaults.message_loader import CLIMessages as MSG  # noqa: N814

from src.cli.commands import CommandRegistry
from src.cli.commands.account_commands import confirm_live_switch, get_account_commands
from src.cli.commands.timeframe_commands import get_timeframe_commands
from src.cli.handlers import TradeHandler

# Issue #459: Import extracted tool functions for Phase 1E integration
from src.cli.tools.alert_tools import show_alerts
from src.cli.tools.execution_mode_tools import (
    confirm_and_set_auto_mode,
    format_mode_change_result,
    set_execution_mode,
    set_orchestrator,
    show_execution_mode,
)
from src.cli.tools.mode_tools import set_mode, show_current_mode, show_mode_comparison
from src.cli.tools.order_tools import (
    cancel_orders_by_id,
    find_orders_to_cancel,
    format_cancel_preview,
    format_cancel_result,
    show_orders,
    show_position_orders,
)
from src.cli.tools.portfolio_tools import show_portfolio
from src.cli.tools.scheduler_tools import show_scheduler
from src.cli.utils.error_utils import sanitize_error_message
from src.cli.utils.help_system import HelpSystem
from src.cli.utils.input_parser import (
    extract_ticker_from_query,
    is_cancel_request,
    parse_cancel_request,
)
from src.cli.utils.intent_classifier import IntentClassifier

# Issue #436: Ticker completer moved to separate module
from src.cli.utils.ticker_completer import (
    READLINE_AVAILABLE,
    get_ticker_completer,
    is_powershell,
    readline,
)
from src.cli.utils.trading_tips import display_trading_tips, get_tips_dict
from src.cli.utils.ui_utils import get_error_prefix, get_mode_indicator
from src.core.trading_orchestrator import TradingOrchestrator
from src.trading.scheduling.daily_scheduler import DailyScheduler
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle

# Import safe_print for Unicode handling
from src.utils.safe_print import safe_print

logger = logging.getLogger(__name__)

# Global ticker completer instance (from extracted module)
_ticker_completer = get_ticker_completer()


def _switch_account_id(user_input: str) -> Optional[str]:
    """Account ID from a typed switch command, e.g. "switch to account paper_main"."""
    input_lower = user_input.lower()
    for phrase in ["switch to", "use account", "change to", "switch account", "use"]:
        if phrase in input_lower:
            # Take the first word after the phrase, skipping "account" in "switch to account"
            words = user_input[input_lower.find(phrase) + len(phrase) :].split()
            if words and words[0].lower() == "account":
                words = words[1:]
            if words:
                return words[0]
    return None


class CLISession:
    """
    Interactive CLI session for unified trading assistant.

    Handles:
    - Trade execution via TradingOrchestrator
    - Position alerts via PositionTracker
    - Scheduler management via DailyScheduler
    - Portfolio status via AlpacaAccountMonitor
    """

    def __init__(self, orchestrator: TradingOrchestrator):
        """
        Initialize CLI session with all features.

        Args:
            orchestrator: Wired TradingOrchestrator
        """
        self.orchestrator = orchestrator
        self.autonomy_mode = "confirm"  # or "auto"
        self.user_id = "cli_user"

        # Issue #459: Set orchestrator for execution mode tools
        set_orchestrator(orchestrator)

        # Initialize help system (Issue #369)
        self.help_system = HelpSystem()

        # Initialize account commands (Issue #401)
        self.account_commands = get_account_commands()

        # Set up command history (#362)
        self._setup_history()

        # Initialize additional components for unified CLI
        # Share instances to reduce Alpaca client instantiations
        try:
            self.trading_cycle = CostEfficientTradeCycle()
            # Reuse trading_cycle's account_monitor instead of creating new one
            self.account_monitor = self.trading_cycle.account_monitor
            # Pass trading_cycle to scheduler to reuse instead of creating new one
            self.scheduler = DailyScheduler(trading_cycle=self.trading_cycle)

            logger.info("CLISession initialized with all features (shared instances)")
        except (ImportError, ValueError, AttributeError, OSError) as e:
            logger.warning(f"Some features unavailable: {e}")
            self.trading_cycle = None
            self.scheduler = None
            self.account_monitor = None

        # Load trading configuration for stop/target display
        self.trading_config = self._load_trading_config()

        # Educational tips for novice users (loaded from config)
        self.trading_tips = get_tips_dict()

        # Issue #509: Initialize TradeHandler for modular trade processing
        self.trade_handler = TradeHandler(
            orchestrator=orchestrator,
            account_monitor=self.account_monitor,
            trading_cycle=self.trading_cycle,
            autonomy_mode=self.autonomy_mode,
            user_id=self.user_id,
        )

        # Issue #509: Initialize IntentClassifier for LLM-based intent routing
        self.intent_classifier = IntentClassifier(
            orchestrator=orchestrator,
            ticker_completer=_ticker_completer,
        )

    def _setup_history(self):
        """
        Set up readline features for CLI.

        Issue #362: Arrow key command history navigation
        Issue #399: Advanced readline features (tab completion, word deletion)

        Features enabled:
        - Up/down arrow keys for command history
        - Tab completion for stock tickers
        - Word deletion keybindings (Ctrl+W, Alt+Backspace)
        - History persisted to ~/.autotrader_history
        - Recent tickers persisted to ~/.autotrader_tickers
        """
        if not READLINE_AVAILABLE:
            logger.info("Readline not available - advanced CLI features disabled")
            return

        # Set up history file
        history_file = os.path.expanduser("~/.autotrader_history")

        # Load existing history
        if os.path.exists(history_file):
            try:
                readline.read_history_file(history_file)
                logger.debug(f"Loaded command history from {history_file}")
            except (OSError, ValueError) as e:
                logger.warning(f"Could not load history file: {e}")

        # Set history length (default: 1000 commands)
        readline.set_history_length(1000)

        # Issue #399: Set up tab completion for tickers
        if _ticker_completer:
            readline.set_completer(_ticker_completer.complete)
            # Use tab for completion
            readline.parse_and_bind("tab: complete")
            # Show all completions on double-tab
            readline.parse_and_bind("set show-all-if-ambiguous on")
            # Case-insensitive completion
            readline.parse_and_bind("set completion-ignore-case on")
            logger.debug("Tab completion for tickers enabled")

        # Issue #399: Word deletion keybindings
        # Note: Some keybindings may not work on all terminals/platforms
        try:
            # Ctrl+W: Delete word backward (unix-word-rubout)
            readline.parse_and_bind('"\\C-w": unix-word-rubout')
            # Alt+Backspace: Delete word backward
            readline.parse_and_bind('"\\e\\C-h": backward-kill-word')
            # Alt+d: Delete word forward
            readline.parse_and_bind('"\\ed": kill-word')
            # Ctrl+U: Delete to beginning of line
            readline.parse_and_bind('"\\C-u": unix-line-discard')
            # Ctrl+K: Delete to end of line
            readline.parse_and_bind('"\\C-k": kill-line')
            logger.debug("Word deletion keybindings configured")
        except (OSError, ValueError) as e:
            logger.debug(f"Some keybindings may not be available: {e}")

        # Save history and recent tickers on exit
        def save_on_exit():
            try:
                readline.write_history_file(history_file)
                logger.debug(f"Saved command history to {history_file}")
            except (OSError, ValueError) as e:
                logger.warning(f"Could not save history file: {e}")
            if _ticker_completer:
                _ticker_completer.save_recent_tickers()

        atexit.register(save_on_exit)

        logger.info("Advanced CLI features enabled (history, tab completion)")

    def _load_trading_config(self) -> Optional[dict]:
        """
        Load trading configuration from YAML for stop/target calculations.

        Returns:
            Config dict or None if failed to load
        """
        try:
            # Get path to config_defaults/trading_config.yaml
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, "..", "config_defaults", "trading_config.yaml")
            config_path = os.path.normpath(config_path)

            if not os.path.exists(config_path):
                logger.warning(f"Trading config not found at {config_path}")
                return None

            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded trading config from {config_path}")
                return config
        except (OSError, ValueError, yaml.YAMLError) as e:
            logger.warning(f"Failed to load trading config: {e}")
            return None

    async def run(self):
        """
        Main REPL loop.

        Handles:
        - User input
        - Processing via orchestrator
        - Display suggestions
        - User confirmation
        - Execution
        """
        self._print_welcome()

        while True:
            try:
                # Get user input with mode indicator
                mode_indicator = get_mode_indicator(self.autonomy_mode)
                user_input = input(f"\n({mode_indicator}) > ").strip()

                if not user_input:
                    continue

                # Handle commands
                if user_input.startswith("/"):
                    if not await self._handle_command(user_input):
                        break  # Exit
                    continue

                # Process trade request
                await self._process_request(user_input)

            except KeyboardInterrupt:
                print(MSG.EXIT_MESSAGE)
                break
            except Exception as e:  # pylint: disable=broad-exception-caught  # Main REPL safety net
                # Use platform-appropriate error prefix
                error_prefix = get_error_prefix()
                sanitized_msg = sanitize_error_message(e)
                print(f"\n{error_prefix} {sanitized_msg}")
                logger.error(f"CLI error: {e}", exc_info=True)
                # Don't show traceback to user - it's logged

    def _print_welcome(self):
        """Print welcome message."""
        print(MSG.WELCOME_BANNER)
        print(MSG.WELCOME_TITLE)
        print(MSG.WELCOME_BANNER)
        print(f"Mode: {self.autonomy_mode.upper()}")
        print(MSG.HELP_COMMANDS)

        # PowerShell notice - tab completion doesn't work there
        if is_powershell():
            print("\nNote: Tab completion is not available in PowerShell.")
            print("      For tab completion, use cmd.exe or Git Bash.")

        # Issue #399: Update ticker completer with current positions
        self._update_position_tickers()

    def _update_position_tickers(self):
        """Update ticker completer with tickers from current positions."""
        if not _ticker_completer or not self.account_monitor:
            return
        try:
            positions = self.account_monitor.get_positions()
            tickers = [p.get("symbol") for p in positions if p.get("symbol")]
            _ticker_completer.set_position_tickers(tickers)
            logger.debug(f"Updated position tickers: {tickers}")
        except (AttributeError, ValueError, OSError) as e:
            logger.debug(f"Could not update position tickers: {e}")

    async def _handle_command(self, command: str) -> bool:
        """
        Handle CLI commands via CommandRegistry.

        Issue #468: Refactored to use self-registering command pattern.

        Args:
            command: Command string (starts with /)

        Returns:
            True to continue, False to exit
        """
        # Use CommandRegistry for slash command dispatch
        handled, should_continue = await CommandRegistry.execute(command, self)

        if handled:
            return should_continue

        # Command not found in registry
        print(MSG.UNKNOWN_COMMAND.format(command=command))
        print(MSG.USE_HELP)
        return True

    def _show_trading_tips(self):
        """Display educational trading tips. Issue #436: Uses config-based tips."""
        display_trading_tips()

    async def _classify_intent(self, user_input: str) -> dict:
        """Classify user intent using IntentClassifier. Issue #509: Delegated to module."""
        return await self.intent_classifier.classify_intent(user_input)

    async def _resolve_ticker_with_llm(self, user_input: str) -> tuple:
        """Resolve ticker using IntentClassifier. Issue #509: Delegated to module."""
        return await self.intent_classifier.resolve_ticker_with_llm(user_input)

    async def _process_request(self, user_input: str):
        """
        Process user request with LLM-based intelligent routing.

        The LLM parser determines if this is a status_query or trade request.
        We use minimal keyword hints only for scheduler/alerts (system features).

        Args:
            user_input: User's natural language input
        """
        input_lower = user_input.lower()

        # Only use keyword routing for system-specific features (scheduler, alerts)
        # Let LLM parser handle trade vs status_query distinction

        if any(
            word in input_lower
            for word in ["scheduler", "schedule", "execution", "morning", "evening", "routine"]
        ):
            # Scheduler queries
            await self._handle_scheduler_request(user_input)

        elif (
            any(word in input_lower for word in ["alert", "approaching"]) and "check" in input_lower
        ):
            # Alert queries
            await self._handle_alerts_request(user_input)

        elif is_cancel_request(user_input):
            # Order cancellation (Issue #360)
            await self._handle_cancel_request(user_input)

        elif any(
            phrase in input_lower
            for phrase in ["execution mode", "execution-mode", "set execution", "show execution"]
        ):
            # Execution mode management (Issue #332)
            await self._handle_execution_mode_request(user_input)

        elif (
            any(
                phrase in input_lower
                for phrase in [
                    "list account",
                    "show accounts",  # Plural = list accounts
                    "switch account",
                    "switch to account",
                    "use account",
                    "change account",
                    "current account",
                    "refresh account",
                ]
            )
            or input_lower.strip() == "accounts"
        ):
            # Account management (Issue #401) - multi-account switching/listing
            # Note: "accounts" alone = list accounts, "account" alone = show portfolio
            await self._handle_account_request(user_input)

        elif any(
            phrase in input_lower
            for phrase in [
                "timeframe",
                "interval",
                "change timeframe",
                "set timeframe",
                "current timeframe",
                "show timeframe",
                "list timeframe",
            ]
        ) or (
            # Only match standalone timeframe patterns (not "1 zm" where m is part of ticker)
            # Match: "1h", "5m alone", "set 1d", but NOT "1 zm" or "5 msft"
            any(
                input_lower.strip() == tf
                for tf in ["5m", "15m", "30m", "1h", "4h", "1d", "1w", "1m"]
            )
            or any(
                f" {tf}" in input_lower or f"set {tf}" in input_lower
                for tf in ["5m", "15m", "30m", "1h", "4h", "1d", "1w", "1m"]
            )
        ):
            # Timeframe management (Issue #365)
            await self._handle_timeframe_request(user_input)

        elif any(
            phrase in input_lower
            for phrase in [
                "trading mode",
                "risk mode",
                "show mode",
                "current mode",
                "set mode",
                "change mode",
                "conservative",
                "moderate",
                "aggressive",
            ]
        ):
            # Trading mode management (Issue #400)
            await self._handle_trading_mode_request(user_input)

        else:
            # For everything else, let LLM parser decide: trade vs status_query
            # This includes: orders, positions, portfolio, and actual trades
            await self._handle_trade_or_status_request(user_input)

    def _reformat_bare_ticker(self, user_input: str) -> str:
        """
        Check if input is a bare ticker symbol and reformat if needed.

        Args:
            user_input: User input string

        Returns:
            Reformatted string (e.g., "meta" → "analyze META") or original input
        """
        input_stripped = user_input.strip().upper()
        if input_stripped.isalpha() and 1 <= len(input_stripped) <= 5:
            # Looks like a bare ticker - reformat as explicit trade request
            return f"analyze {input_stripped}"
        return user_input

    async def _handle_trade_or_status_request(self, user_input: str):
        """
        Use LLM parser to determine if this is a trade or status query.

        The LLM will classify as:
        - "trade" → buy/sell/analyze specific ticker (e.g., "buy AAPL", "is SPY good?")
        - "status_query" → asking about orders/positions/portfolio (e.g., "any open orders?")

        Args:
            user_input: User's natural language input
        """
        try:
            # Check if input looks like a bare ticker symbol (e.g., "ibm", "meta", "SPY")
            # Reformat to explicit request to prevent LLM parser misclassification
            formatted_input = self._reformat_bare_ticker(user_input)
            if formatted_input != user_input:
                # Was a bare ticker, now reformatted
                await self._handle_trade_request(formatted_input)
                return

            # Let LLM parser classify the request type
            request = await self.orchestrator.parser.parse(user_input, self.user_id)

            if request.request_type == "status_query":
                # Status query detected - route based on content
                input_lower = user_input.lower()

                # Issue #348: Route stop/target queries with symbols to order details
                if any(
                    phrase in input_lower
                    for phrase in [
                        "stop level on",
                        "stop price on",
                        "stop loss on",
                        "stop on",
                        "target price on",
                        "take profit on",
                        "tp on",
                        "exit orders on",
                        "bracket orders on",
                        "orders for",
                        "show orders for",
                    ]
                ):
                    # Specific symbol stop/target query → show detailed orders
                    await self._handle_position_orders(user_input)
                elif any(word in input_lower for word in ["order", "orders"]):
                    await self._handle_orders_request(user_input)
                elif any(
                    phrase in input_lower
                    for phrase in ["stop", "target", "take profit", "exit level", "stop loss"]
                ):
                    # Generic stop/target queries → show portfolio with exit levels
                    await self._handle_portfolio_request(user_input)
                elif any(
                    word in input_lower for word in ["position", "positions", "holding", "holdings"]
                ):
                    await self._handle_portfolio_request(user_input)
                elif any(
                    word in input_lower
                    for word in [
                        "portfolio",
                        "account",
                        "balance",
                        "buying power",
                        "equity",
                        "cash",
                    ]
                ):
                    await self._handle_portfolio_request(user_input)
                else:
                    # Parser said status_query but no keywords matched
                    # Likely just a ticker (e.g., "ibm") - treat as trade request
                    await self._handle_trade_request(user_input)
            else:
                # Trade request → process through orchestrator
                await self._handle_trade_request(user_input)

        except (ValueError, OSError, RuntimeError, AttributeError) as e:
            logger.error(f"Error routing request: {e}", exc_info=True)
            error_prefix = get_error_prefix()
            sanitized_msg = sanitize_error_message(e)
            print(f"\n{error_prefix} {sanitized_msg}")

    async def _handle_trade_request(self, user_input: str):
        """
        Process user trade request via orchestrator.

        Issue #509: Delegates to TradeHandler for modular processing.
        Issue #347: Respects user intent - when user says "buy", we show "BUY (as requested)"
        and treat signals as context, not override.

        Args:
            user_input: User's natural language input
        """
        # Sync autonomy mode in case it changed
        self.trade_handler.set_autonomy_mode(self.autonomy_mode)
        await self.trade_handler.handle_trade_request(user_input)

    def _get_stop_loss_pct(self) -> float:
        """
        Get configured stop loss percentage from trading config.

        Returns:
            Stop loss percentage (default: 0.05 = 5%)
        """
        if not self.trading_config:
            return 0.05  # Default fallback

        try:
            exits = self.trading_config.get("strategy_parameters", {}).get("exits", {})
            default_strategy = exits.get("default", "balanced")
            strategy_config = exits.get(default_strategy, {})
            return strategy_config.get("stop_loss", 0.05)
        except (AttributeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to get stop_loss from config: {e}")
            return 0.05

    def _get_take_profit_pct(self) -> float:
        """
        Get configured take profit percentage from trading config.

        Returns:
            Take profit percentage (default: 0.08 = 8%)
        """
        if not self.trading_config:
            return 0.08  # Default fallback

        try:
            exits = self.trading_config.get("strategy_parameters", {}).get("exits", {})
            default_strategy = exits.get("default", "balanced")
            strategy_config = exits.get(default_strategy, {})
            return strategy_config.get("take_profit", 0.08)
        except (AttributeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to get take_profit from config: {e}")
            return 0.08

    async def _handle_alerts_request(self, _user_input: str):
        """Handle position alerts request. Issue #459: Uses show_alerts()."""
        output = show_alerts()
        print(output)

    async def _handle_scheduler_request(self, _user_input: str):
        """Handle scheduler status request. Issue #459: Uses show_scheduler()."""
        output = show_scheduler()
        print(output)

    async def _handle_portfolio_request(self, user_input: str):
        """
        Handle portfolio/account status request.

        Issue #459: Refactored to use extracted show_portfolio() from portfolio_tools.

        Args:
            user_input: User's natural language input
        """
        # Extract specific ticker if asking about stop/target
        input_lower = user_input.lower()
        specific_ticker = None

        keywords = [
            "target on",
            "target for",
            "stop on",
            "stop for",
            "stop level on",
            "stop loss on",
            "exit level",
            "take profit",
        ]

        if any(keyword in input_lower for keyword in keywords):
            words = user_input.upper().split()
            common_tickers = ["SPY", "QQQ", "TQQQ", "SQQQ", "AAPL", "MSFT", "TSLA", "NVDA", "META"]
            for word in words:
                if word in common_tickers:
                    specific_ticker = word
                    break

        # Use extracted show_portfolio function from portfolio_tools
        output = show_portfolio(
            account_monitor=self.account_monitor,
            trading_cycle=self.trading_cycle,
            specific_ticker=specific_ticker,
            stop_loss_pct=self._get_stop_loss_pct(),
            take_profit_pct=self._get_take_profit_pct(),
        )
        print(output)

    async def _handle_orders_request(self, _user_input: str):
        """
        Handle order status request - shows pending/open orders.
        Issue #459: Refactored to use show_orders() from order_tools.
        """
        output = show_orders()
        print(output)

    async def _handle_position_orders(self, user_input: str):
        """
        Show detailed orders for specific position.
        Issue #459: Refactored to use show_position_orders() from order_tools.
        """
        # Extract ticker from query
        ticker = extract_ticker_from_query(user_input)
        if not ticker:
            print("❌ Could not identify symbol in query")
            print("ℹ️  Try: 'show orders for AAPL' or 'stop level on META'")
            return
        output = show_position_orders(ticker)
        print(output)

    async def _handle_cancel_request(self, user_input: str):
        """
        Handle order cancellation requests.

        Issue #360: Order cancellation functionality.
        Issue #633: Lists the orders and asks every time, in AUTO too, then cancels exactly
        the orders it listed.

        Supports:
        - cancel all orders
        - cancel order <ID>
        - cancel orders for <SYMBOL>
        """
        target = parse_cancel_request(user_input)
        if not target:
            print("❌ Could not determine what to cancel")
            print("ℹ️  Usage:")
            print("   • cancel all orders")
            print("   • cancel orders for AAPL")
            print("   • cancel order <order-id>")
            return

        found = find_orders_to_cancel(symbol=target.get("symbol"), order_id=target.get("order_id"))
        if found["status"] != "success":
            print(f"❌ {found.get('message') or found.get('error', 'Unknown error')}")
            return
        orders = found["orders"]
        if not orders:
            suffix = f" for {target['symbol']}" if "symbol" in target else ""
            print(f"ℹ️  No open orders{suffix}")
            return

        print(format_cancel_preview(orders))
        if input("Type 'yes' to cancel them (anything else keeps them): ").strip().lower() != "yes":
            print("ℹ️  Nothing cancelled")
            return
        print(format_cancel_result(cancel_orders_by_id([o["id"] for o in orders])))

    async def _handle_execution_mode_request(self, user_input: str):
        """
        Handle execution mode view/change requests.
        Issue #332/#459: Uses execution_mode_tools functions.
        """
        input_lower = user_input.lower()

        # Determine if this is a "show" or "set" request
        is_show = any(word in input_lower for word in ["show", "what", "current", "get"])
        is_set = any(word in input_lower for word in ["set", "change", "switch"])

        if is_show and not is_set:
            print(show_execution_mode())
            return

        # Extract target mode from input
        target_mode = None
        for mode in ["confirm", "auto", "paper", "disabled"]:
            if mode in input_lower:
                target_mode = mode
                break

        if not target_mode:
            print(show_execution_mode())
            return

        # Set new mode using tools
        result = set_execution_mode(target_mode)

        if result["status"] == "requires_confirmation":
            # AUTO mode needs user confirmation
            print(format_mode_change_result(result))
            print("\nSwitch to AUTO mode? [yes/no]: ", end="")
            confirm = input().strip().lower()
            if confirm != "yes":
                print("❌ Mode change cancelled")
                return
            result = confirm_and_set_auto_mode()

        print(format_mode_change_result(result))

    async def _handle_trading_mode_request(self, user_input: str):
        """
        Handle trading mode view/change requests.
        Issue #400: Trading Modes Configuration System
        Issue #459: Refactored to use mode_tools functions.

        Supports:
        - show mode / show trading mode / current mode
        - set mode conservative/moderate/aggressive
        - conservative / moderate / aggressive (direct command)

        Args:
            user_input: User's natural language input
        """
        input_lower = user_input.lower()

        # Determine if this is a "show" or "set" request
        is_show = any(word in input_lower for word in ["show", "what", "current", "get", "display"])
        is_set = any(word in input_lower for word in ["set", "change", "switch", "use"])
        mode_name_only = input_lower.strip() in ["conservative", "moderate", "aggressive"]

        # Check for comparison request
        if "compare" in input_lower or "comparison" in input_lower:
            print(show_mode_comparison())
            return

        if (is_show and not is_set) or (not is_show and not is_set and not mode_name_only):
            # Show current trading mode using mode_tools
            print(show_current_mode())
            print("\nTo change mode: set mode {conservative|moderate|aggressive}")
            return

        # Try to extract target mode from input
        target_mode_str = None
        for mode in ["conservative", "moderate", "aggressive"]:
            if mode in input_lower:
                target_mode_str = mode
                break

        if not target_mode_str:
            print("❌ Could not determine trading mode")
            print("ℹ️  Usage: set mode {conservative|moderate|aggressive}")
            return

        # Set new mode using mode_tools
        result = set_mode(target_mode_str)
        print(result)

    async def _handle_account_request(self, user_input: str):
        """
        Handle account management requests.
        Issue #401: Multi-account portfolio management

        Supports:
        - list accounts / show accounts
        - switch to account <ID>
        - use account <ID>
        - show current account
        - refresh accounts

        Args:
            user_input: User's natural language input
        """
        input_lower = user_input.lower()

        # Determine intent from input
        if any(phrase in input_lower for phrase in ["list account", "show accounts"]):
            # List all accounts (note: "show accounts" plural, not "show account" singular)
            verbose = "verbose" in input_lower or "detail" in input_lower
            self.account_commands.list_accounts(verbose=verbose)

        elif any(phrase in input_lower for phrase in ["switch", "use", "change"]):
            account_id = _switch_account_id(user_input)

            if account_id:
                # Typed by the user, so a live account can be confirmed at a prompt
                result = self.account_commands.switch_account(
                    account_id, confirm_live=confirm_live_switch
                )
                if result["status"] == "success":
                    # Update orchestrator if needed
                    print("\n💡 Tip: Restart trading assistant to use new account for trades")
            else:
                print("❌ Please specify an account ID")
                print("ℹ️  Usage: switch to account <ACCOUNT_ID>")
                print("   Example: switch to account paper_main")

        elif any(phrase in input_lower for phrase in ["current account", "active account"]):
            # Show current account
            self.account_commands.show_current_account()

        elif "refresh" in input_lower:
            # Refresh account data
            self.account_commands.refresh_accounts()

        else:
            # Default: show account list
            self.account_commands.list_accounts()

    async def _handle_timeframe_request(self, user_input: str):
        """
        Handle timeframe management requests.
        Issue #365: Timeframe specification for multi-timeframe analysis

        Supports:
        - timeframe <TF>  (e.g., "timeframe 1d", "timeframe 1M")
        - list timeframes / show timeframes
        - timeframe recommendations

        Args:
            user_input: User's natural language input
        """

        tf_commands = get_timeframe_commands()
        input_lower = user_input.lower()

        # First: Check if input contains a timeframe value to SET
        # Alpaca supports: 1-59 minutes, 1-23 hours, days, weeks, months
        # Pattern: digits + unit (m=minute, h=hour, d=day, w=week, M=month)
        # Note: 1M = month (uppercase M), 1m = minute (lowercase m)
        tf_match = re.search(r"\b(\d{1,2}[mhdw]|1M)\b", user_input)

        if tf_match:
            # User wants to set a timeframe (e.g., "timeframe 1d", "1h", "set 4h")
            timeframe = tf_match.group(1)
            output = tf_commands.set_timeframe(timeframe)
            safe_print(output)

        elif any(
            phrase in input_lower for phrase in ["list timeframe", "show timeframe", "available"]
        ):
            # List all timeframes
            verbose = (
                "verbose" in input_lower or "detail" in input_lower or "description" in input_lower
            )
            output = tf_commands.list_timeframes(verbose=verbose)
            safe_print(output)

        elif any(
            phrase in input_lower for phrase in ["recommendation", "suggest", "best timeframe"]
        ):
            # Show recommendations
            output = tf_commands.show_timeframe_recommendations()
            safe_print(output)

        else:
            # Default: show full list with current highlighted (Issue #470)
            output = tf_commands.list_timeframes(verbose=True)
            safe_print(output)
