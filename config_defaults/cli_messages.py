"""
CLI Messages Configuration
Centralized configuration for all CLI text, emojis, and formatting.
"""


class CLIMessages:
    """All CLI user-facing messages and UI elements."""

    # ========================================================================
    # EMOJIS
    # ========================================================================
    EMOJI = {
        # Mode indicators
        "auto_mode": "🤖",
        "confirm_mode": "✋",
        # Status indicators
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "📊",
        "critical": "🚨",
        "loading": "⏳",
        "thinking": "💭",
        # Trading signals
        "buy": "⬆️",
        "sell": "⬇️",
        "hold": "➡️",
        "check": "✅",
        # Position status
        "profit": "🟢",
        "loss": "🔴",
        "neutral": "⚪",
        # Feature icons
        "portfolio": "💼",
        "money": "💰",
        "chart": "📈",
        "orders": "📋",
        "alerts": "🔔",
        "scheduler": "⏰",
        "calendar": "📅",
        "clock": "🔮",
        "config": "⚙️",
        "target": "🎯",
        "history": "📜",
        "book": "📖",
        "lightbulb": "💡",
        "wave": "👋",
        "lightning": "⚡",
        # Time of day
        "morning": "🌅",
        "evening": "🌆",
        # Actions
        "retry": "🔄",
    }

    # ========================================================================
    # WELCOME / HELP
    # ========================================================================
    WELCOME_BANNER = "=" * 70
    WELCOME_TITLE = "   AutoGen Trading Assistant - Unified Interactive CLI"

    HELP_COMMANDS = """
System Commands:
  /help      - Show help
  /exit      - Exit (or Ctrl+C)
  /toggle    - Toggle between CONFIRM and AUTO modes
  /schedule  - Enter scheduler management mode
  /tips      - Show trading basics for beginners

Trading:
  > buy 10 AAPL
  > is SPY at $XXX.XX a good entry?
  > sell all TQQQ
  > pltr                        (just type ticker for analysis)

Monitoring:
  > check my alerts
  > show portfolio
  > any open orders
  > what's my position status?

Scheduler:
  > show scheduler status
  > show execution history
  Or use /schedule for full scheduler CLI

Tip: Just type naturally - the LLM will understand!
     New to trading? Type /tips to learn the basics!
"""

    # ========================================================================
    # MODE TOGGLE
    # ========================================================================
    MODE_SWITCHED_AUTO = "🤖 Mode switched to: AUTO (trades execute immediately)"
    MODE_SWITCHED_CONFIRM = "✋ Mode switched to: CONFIRM (asks before executing)"

    # ========================================================================
    # EXIT
    # ========================================================================
    EXIT_MESSAGE = "\n\n👋 Exiting..."

    # ========================================================================
    # COMMANDS
    # ========================================================================
    UNKNOWN_COMMAND = "Unknown command: {command}"
    USE_HELP = "Use /help to see available commands"

    # ========================================================================
    # TRADE REQUEST
    # ========================================================================
    ANALYZING_TRADE = "\n⏳ Analyzing trade..."
    AUTO_EXECUTING = "\n⚡ Auto-executing..."
    TRADE_CANCELLED = "\n❌ Trade cancelled"

    # Sell signal warnings
    SELL_WARNING = """
⚠️  WARNING: SELL signal detected.
   This system does not support short selling.
   Only SELL if you currently hold this position.
   Otherwise, ignore this signal.
"""

    # Error messages
    ERROR_PROCESSING = "\n❌ Error processing request: {error}"
    ERROR_INVALID_TICKER = """
❌ Invalid ticker symbol
   The ticker you entered was not recognized by the market.
   Please check the spelling and try again.
   Example: AAPL (not APPL), TSLA, SPY, MSFT
"""

    # ========================================================================
    # TRADE SUGGESTION DISPLAY
    # ========================================================================
    SUGGESTION_SEPARATOR = "=" * 70
    SUGGESTION_HEADER = "📊 {ticker} @ ${price:.2f}"

    SIGNAL_DISPLAY = "{emoji} {signal} SUGGESTED"
    CONFIDENCE_DISPLAY = "   Confidence: {confidence:.1%}"

    ANALYSIS_HEADER = "\n📈 Analysis:"
    ANALYSIS_ITEM = "   • {reason}"

    ENTRY_PLAN_HEADER = "\n💰 Entry Plan:"
    ENTRY_PLAN = """   Entry:  ${entry:.2f}
   Stop:   ${stop:.2f} ({stop_pct:+.1f}%)
   Target: ${target:.2f} ({target_pct:+.1f}%)
   Qty:    {qty} shares
   Order:  {tif}"""

    PORTFOLIO_IMPACT_HEADER = "\n📊 Portfolio Impact:"
    PORTFOLIO_IMPACT = """   Trade Value: ${trade_value:,.2f}
   Portfolio %: {portfolio_pct:.1f}% (after transaction)
   Max Loss:    ${max_loss:.2f}
   Risk/Reward: {risk_reward:.2f}"""

    WARNINGS_HEADER = "\n⚠️  Warnings:"
    WARNING_ITEM = "   {warning}"

    # ========================================================================
    # CONFIRMATION
    # ========================================================================
    CONFIRM_PROMPT = "\nPlace this order? [yes/no]: "
    CONFIRM_INVALID = "Please enter 'yes' or 'no'"

    # ========================================================================
    # ORDER RESULT
    # ========================================================================
    RESULT_SEPARATOR = "=" * 70

    ORDER_SUCCESS_HEADER = "✅ ORDER PLACED SUCCESSFULLY"
    ORDER_SUCCESS = """
   {qty} shares {ticker}
   Entry Order:  {entry_id}
   Stop Order:   {stop_id}
   Target Order: {target_id}

   {message}
"""

    ORDER_FAILED_HEADER = "❌ ORDER FAILED"
    ORDER_FAILED = """
   {message}
"""
    ORDER_ERROR = "   Error: {error}"

    # ========================================================================
    # ALERTS
    # ========================================================================
    CHECKING_ALERTS = "\n📊 Checking Position Alerts..."
    ALERTS_NOT_INITIALIZED = "❌ Trading cycle not initialized"

    NO_ALERTS = "✅ No active alerts"
    POSITIONS_MONITORED = "   {count} position(s) monitored"

    ALERTS_HEADER = "\n🔔 {count} Alert(s) Generated:"
    ALERT_ITEM = """   {emoji} {ticker}: {alert_type}
      Current: ${price:.2f}"""
    ALERT_DETAIL = "      {key}: {value}"

    ALERT_HISTORY_HEADER = "\n📜 Alert History ({count} total):"
    ALERT_HISTORY_ITEM = "   • {ticker} - {alert_type} at {time}"

    ERROR_CHECKING_ALERTS = "❌ Error checking alerts: {error}"

    # ========================================================================
    # SCHEDULER
    # ========================================================================
    SCHEDULER_HEADER = "\n⏰ Daily Scheduler Status"
    SCHEDULER_SEPARATOR = "=" * 70

    SCHEDULER_NOT_INITIALIZED = """❌ Scheduler not initialized - not running in daemon mode

💡 To start scheduler:
   python main.py --daemon

📖 What the scheduler does:
   • Morning (9:20 AM ET): Check positions, place new orders
   • Evening (3:50 PM ET): Review positions, adjust stops
"""

    SCHEDULER_STATUS = "\n{emoji} Status: {status}"
    SCHEDULER_CONFIG_HEADER = "\n⚙️  Configuration:"
    SCHEDULER_CONFIG = """   Morning Routine: {morning} ET
   Evening Routine: {evening} ET
   Max Retries: {retries}
   Timezone: US/Eastern"""

    SCHEDULER_ROUTINES_HEADER = "\n📅 Scheduled Routines:"

    MORNING_ROUTINE = """
   🌅 Morning Routine (9:20 AM ET):
      • Fetch current broker state
      • Check position alerts (approaching TP/SL)
      • Analyze market conditions
      • Place new GTC orders if signals present
      • Log execution status"""

    EVENING_ROUTINE = """
   🌆 Evening Routine (3:50 PM ET):
      • Review all open positions
      • Check alert triggers
      • Adjust trailing stops if profitable
      • Update position tracker state
      • Log daily summary"""

    SCHEDULER_HISTORY_HEADER = """
📋 Recent Execution History:
   (Last 7 days, showing up to 10 most recent)"""

    SCHEDULER_HISTORY_ITEM = """
   {emoji} {task}
      Status: {status}
      Time: {time}"""

    SCHEDULER_ERROR = "      ⚠️  Error: {error}..."
    SCHEDULER_RETRIES = "      🔄 Retries: {count}"

    SCHEDULER_NO_HISTORY = """
📋 No execution history found
   Scheduler hasn't run yet or history is empty"""

    SCHEDULER_NEXT_HEADER = "\n🔮 Next Scheduled Execution:"
    SCHEDULER_NEXT = """   {task}
   Time: {time} ET
   Countdown: {hours}h {minutes}m from now"""
    SCHEDULER_NEXT_ERROR = "   Unable to calculate next run: {error}"

    SCHEDULER_COMMANDS = """
💡 Scheduler Commands:
   python main.py --daemon      # Run scheduler in background
   python main.py --help        # See all options

📖 Documentation:
   docs/features/02_gtc_scheduler_quickstart.md
   docs/features/03_gtc_scheduler_technical.md"""

    ERROR_CHECKING_SCHEDULER = "❌ Error checking scheduler: {error}"

    # ========================================================================
    # PORTFOLIO
    # ========================================================================
    PORTFOLIO_HEADER = "\n💼 Portfolio Status..."
    PORTFOLIO_NOT_INITIALIZED = "❌ Account monitor not initialized"

    ACCOUNT_HEADER = "\n💰 Account:"
    ACCOUNT_INFO = """   Equity: ${equity:,.2f}
   Cash: ${cash:,.2f}
   Buying Power: ${buying_power:,.2f}
   Pattern Day Trader: {pdt}"""

    POSITIONS_HEADER = "\n📊 Positions ({count}):"
    POSITION_ITEM = """   {emoji} {symbol}: {qty} shares @ ${entry:.2f} (avg entry)
      Current: ${current:.2f}
      Value: ${value:,.2f}
      P/L: ${pl:,.2f} ({pl_pct:+.2f}%)"""

    POSITION_DETAILS_HEADER = "\n{emoji} {symbol} Position Details:"
    POSITION_DETAILS = """   Quantity: {qty} shares
   Entry Price: ${entry:.2f}
   Current Price: ${current:.2f}
   P/L: ${pl:,.2f} ({pl_pct:+.2f}%)"""

    PRICE_TARGETS_HEADER = "\n🎯 Price Targets:"
    PRICE_TARGETS = """   Take Profit: ${tp:.2f}
   Stop Loss: ${sl:.2f}
   Distance to TP: {tp_dist:+.2f}%
   Distance to SL: {sl_dist:+.2f}%"""

    NO_TARGETS = "\n💡 No price targets set for {symbol}"
    NO_POSITION = "\n❌ No position found for {ticker}"
    NO_POSITIONS = "\n📊 No open positions"

    ERROR_CHECKING_PORTFOLIO = "❌ Error checking portfolio: {error}"

    # ========================================================================
    # ORDERS
    # ========================================================================
    CHECKING_ORDERS = "\n📋 Checking Open Orders..."
    ORDERS_NOT_INITIALIZED = "❌ Account monitor not initialized"

    NO_ORDERS = "✅ No open orders"
    ORDERS_HEADER = "\n📊 {count} Open Order(s):"

    ORDER_ITEM = """   {emoji} {side} {qty} {symbol} {price}
      Type: {type}, Status: {status}
      Order ID: {order_id}..."""

    ERROR_CHECKING_ORDERS = "❌ Error checking orders: {error}"


# ========================================================================
# HELPER FUNCTIONS
# ========================================================================


def get_signal_emoji(signal_value: str) -> str:
    """Get emoji for trading signal."""
    signal_upper = signal_value.upper()
    if signal_upper == "BUY":
        return CLIMessages.EMOJI["buy"]
    elif signal_upper == "SELL":
        return CLIMessages.EMOJI["sell"]
    elif signal_upper == "HOLD":
        return CLIMessages.EMOJI["hold"]
    return CLIMessages.EMOJI["check"]


def get_pl_emoji(pl_value: float) -> str:
    """Get emoji for profit/loss."""
    if pl_value > 0:
        return CLIMessages.EMOJI["profit"]
    elif pl_value < 0:
        return CLIMessages.EMOJI["loss"]
    return CLIMessages.EMOJI["neutral"]


def get_side_emoji(side: str) -> str:
    """Get emoji for order side."""
    if side.upper() == "BUY":
        return CLIMessages.EMOJI["profit"]
    elif side.upper() == "SELL":
        return CLIMessages.EMOJI["loss"]
    return CLIMessages.EMOJI["neutral"]


def get_status_emoji(status: str) -> str:
    """Get emoji for scheduler execution status."""
    status_map = {
        "completed": CLIMessages.EMOJI["success"],
        "failed": CLIMessages.EMOJI["error"],
        "retrying": CLIMessages.EMOJI["retry"],
        "partial": CLIMessages.EMOJI["warning"],
    }
    return status_map.get(status.lower(), CLIMessages.EMOJI["neutral"])


def get_alert_severity_emoji(severity: str) -> str:
    """Get emoji for alert severity."""
    severity_map = {
        "INFO": CLIMessages.EMOJI["info"],
        "WARNING": CLIMessages.EMOJI["warning"],
        "CRITICAL": CLIMessages.EMOJI["critical"],
    }
    return severity_map.get(severity.upper(), CLIMessages.EMOJI["warning"])
