"""
User Alert Tools - FunctionTool wrappers for user-defined price alerts.

Issue #480/#481: CLI tools for SQLite-backed user alerts.

These tools handle:
- Creating price alerts (above/below thresholds)
- Listing active alerts
- Deleting/toggling alerts

Uses src.trading.alerts_watchlists.AlertsWatchlistsManager for persistence.
"""

import logging
from typing import Any, Dict, Optional

from agent_framework import FunctionTool

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_alerts_manager():
    """Get AlertsWatchlistsManager instance."""
    try:
        from src.trading.alerts_watchlists import get_alerts_watchlists_manager

        return get_alerts_watchlists_manager()
    except Exception as e:
        logger.error(f"Failed to get alerts manager: {e}")
        return None


# =============================================================================
# FunctionTool Wrapper Functions
# =============================================================================


def create_price_alert(
    symbol: str,
    alert_type: str,
    trigger_value: float,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new price alert.

    Args:
        symbol: Stock ticker symbol (e.g., "AAPL")
        alert_type: Type of alert - "above" or "below"
        trigger_value: Price that triggers the alert
        message: Optional custom message

    Returns:
        Dict with status and created alert details
    """
    try:
        mgr = _get_alerts_manager()
        if not mgr:
            return {"status": "error", "message": "Alerts manager not available"}

        # Map friendly names to AlertType
        from src.trading.alerts_watchlists import AlertType

        type_map = {
            "above": AlertType.PRICE_ABOVE,
            "price_above": AlertType.PRICE_ABOVE,
            "below": AlertType.PRICE_BELOW,
            "price_below": AlertType.PRICE_BELOW,
        }

        alert_type_enum = type_map.get(alert_type.lower())
        if not alert_type_enum:
            return {
                "status": "error",
                "message": (
                    f"Unsupported alert type: {alert_type}. Use above or below: only those "
                    "are ever checked."
                ),
            }

        alert = mgr.create_alert(
            symbol=symbol,
            alert_type=alert_type_enum,
            trigger_value=trigger_value,
            message=message,
        )

        if alert:
            return {
                "status": "success",
                "alert_id": alert.id,
                "symbol": alert.symbol,
                "alert_type": alert.alert_type,
                "trigger_value": alert.trigger_value,
                "message": f"Alert created: {alert.symbol} {alert.alert_type} @ {alert.trigger_value}",
            }
        return {"status": "error", "message": "Failed to create alert"}

    except Exception as e:
        logger.error(f"Error creating alert: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def list_alerts(
    symbol: Optional[str] = None,
    show_triggered: bool = False,
) -> Dict[str, Any]:
    """
    List price alerts.

    Args:
        symbol: Optional filter by symbol
        show_triggered: Include triggered alerts (default: False)

    Returns:
        Dict with status and list of alerts
    """
    try:
        mgr = _get_alerts_manager()
        if not mgr:
            return {"status": "error", "message": "Alerts manager not available"}

        alerts = mgr.get_alerts(
            symbol=symbol,
            enabled_only=True,
            untriggered_only=not show_triggered,
        )

        alert_list = [
            {
                "id": a.id,
                "symbol": a.symbol,
                "type": a.alert_type,
                "trigger": a.trigger_value,
                "message": a.message,
                "triggered": a.triggered_at is not None,
            }
            for a in alerts
        ]

        return {
            "status": "success",
            "count": len(alert_list),
            "alerts": alert_list,
            "filter_symbol": symbol,
        }

    except Exception as e:
        logger.error(f"Error listing alerts: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def delete_alert(alert_id: int) -> Dict[str, Any]:
    """
    Delete a price alert.

    Args:
        alert_id: ID of alert to delete

    Returns:
        Dict with status
    """
    try:
        mgr = _get_alerts_manager()
        if not mgr:
            return {"status": "error", "message": "Alerts manager not available"}

        success = mgr.delete_alert(alert_id)
        if success:
            return {"status": "success", "message": f"Alert {alert_id} deleted"}
        return {"status": "error", "message": f"Failed to delete alert {alert_id}"}

    except Exception as e:
        logger.error(f"Error deleting alert: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def toggle_alert(alert_id: int, enabled: bool) -> Dict[str, Any]:
    """
    Enable or disable an alert.

    Args:
        alert_id: ID of alert
        enabled: True to enable, False to disable

    Returns:
        Dict with status
    """
    try:
        mgr = _get_alerts_manager()
        if not mgr:
            return {"status": "error", "message": "Alerts manager not available"}

        success = mgr.toggle_alert(alert_id, enabled)
        state = "enabled" if enabled else "disabled"
        if success:
            return {"status": "success", "message": f"Alert {alert_id} {state}"}
        return {"status": "error", "message": f"Failed to toggle alert {alert_id}"}

    except Exception as e:
        logger.error(f"Error toggling alert: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# =============================================================================
# Alert Checking (Issue #482)
# =============================================================================


def check_alerts_with_prices(prices: Dict[str, float]) -> Dict[str, Any]:
    """
    Check alerts against current prices and trigger any that match.

    Args:
        prices: Dict mapping symbol -> current price (e.g., {"AAPL": 175.50})

    Returns:
        Dict with status and list of triggered alerts
    """
    try:
        mgr = _get_alerts_manager()
        if not mgr:
            return {"status": "error", "message": "Alerts manager not available"}

        triggered = mgr.check_alerts_batch(prices)

        triggered_list = [
            {
                "id": a.id,
                "symbol": a.symbol,
                "type": a.alert_type,
                "trigger": a.trigger_value,
                "message": a.message,
            }
            for a in triggered
        ]

        return {
            "status": "success",
            "triggered_count": len(triggered_list),
            "triggered_alerts": triggered_list,
            "prices_checked": len(prices),
        }

    except Exception as e:
        logger.error(f"Error checking alerts: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def get_symbols_with_alerts() -> Dict[str, Any]:
    """
    Get list of symbols that have active alerts.

    Returns:
        Dict with status and list of symbols
    """
    try:
        mgr = _get_alerts_manager()
        if not mgr:
            return {"status": "error", "message": "Alerts manager not available"}

        alerts = mgr.get_alerts(enabled_only=True, untriggered_only=True)
        symbols = sorted({a.symbol for a in alerts})

        return {
            "status": "success",
            "count": len(symbols),
            "symbols": symbols,
        }

    except Exception as e:
        logger.error(f"Error getting symbols with alerts: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# =============================================================================
# Display Functions
# =============================================================================


def show_user_alerts(symbol: Optional[str] = None) -> str:
    """Display user-defined price alerts with formatted output."""
    result = list_alerts(symbol=symbol)

    if result["status"] == "error":
        return f"Error: {result.get('error', result.get('message', 'Unknown'))}"

    alerts = result.get("alerts", [])
    if not alerts:
        if symbol:
            return f"No alerts for {symbol.upper()}"
        return "No active alerts. Use 'add alert SYMBOL above/below PRICE' to create one."

    lines = [f"Price Alerts ({len(alerts)})", "=" * 40]
    for a in alerts:
        direction = "+" if "above" in a["type"] or "gain" in a["type"] else "-"
        triggered = " [TRIGGERED]" if a.get("triggered") else ""
        msg = f" - {a['message']}" if a.get("message") else ""
        lines.append(
            f"  [{a['id']}] {direction} {a['symbol']}: "
            f"{a['type']} @ ${a['trigger']:.2f}{triggered}{msg}"
        )

    return "\n".join(lines)


# =============================================================================
# FunctionTool Registration
# =============================================================================


create_price_alert_tool = FunctionTool(
    func=create_price_alert,
    name="create_price_alert",
    description=(
        "Create a price alert for a stock, above or below a price. It only fires when "
        "check_alerts_with_prices is called: no routine checks user alerts."
    ),
)

list_alerts_tool = FunctionTool(
    func=list_alerts,
    name="list_user_alerts",
    description="List user-defined price alerts, optionally filtered by symbol.",
)

delete_alert_tool = FunctionTool(
    func=delete_alert,
    name="delete_alert",
    description="Delete a price alert by ID.",
)

toggle_alert_tool = FunctionTool(
    func=toggle_alert,
    name="toggle_alert",
    description="Enable or disable a price alert.",
)

show_user_alerts_tool = FunctionTool(
    func=show_user_alerts,
    name="show_user_alerts",
    description="Display user-defined price alerts with formatted output.",
)

# Alert checking tools (Issue #482)
check_alerts_tool = FunctionTool(
    func=check_alerts_with_prices,
    name="check_alerts_with_prices",
    description="Check alerts against current prices and trigger any that match.",
)

get_symbols_with_alerts_tool = FunctionTool(
    func=get_symbols_with_alerts,
    name="get_symbols_with_alerts",
    description="Get list of symbols that have active alerts.",
)


# Export list for CLI tools registry
CLI_USER_ALERT_TOOLS = [
    create_price_alert_tool,
    list_alerts_tool,
    delete_alert_tool,
    toggle_alert_tool,
    show_user_alerts_tool,
    check_alerts_tool,
    get_symbols_with_alerts_tool,
]

__all__ = [
    "create_price_alert",
    "list_alerts",
    "delete_alert",
    "toggle_alert",
    "show_user_alerts",
    "check_alerts_with_prices",
    "get_symbols_with_alerts",
    "CLI_USER_ALERT_TOOLS",
]
