"""
Portfolio Override Tools - FunctionTool wrappers for runtime config overrides.

Issue #479/#481: CLI tools for SQLite-backed portfolio configuration overrides.

These tools handle:
- Setting runtime overrides for portfolio config values
- Clearing overrides to restore defaults
- Viewing active overrides and history
- Managing temporary overrides with expiration

Uses src.trading.portfolio_override.PortfolioOverrideManager for persistence.
"""

import logging
from typing import Any, Dict, Optional

from agent_framework import FunctionTool

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_override_manager():
    """Get PortfolioOverrideManager instance."""
    try:
        from src.trading.portfolio_override import get_portfolio_override_manager

        return get_portfolio_override_manager()
    except Exception as e:
        logger.error(f"Failed to get portfolio override manager: {e}")
        return None


# =============================================================================
# FunctionTool Wrapper Functions
# =============================================================================


def set_portfolio_override(
    key: str,
    value: str,
    reason: Optional[str] = None,
    expires_in_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Set a portfolio configuration override.

    Args:
        key: Config key in dot notation (e.g., 'limits.max_open_positions')
        value: Override value (will be auto-typed)
        reason: Optional reason for the override (for audit trail)
        expires_in_hours: Optional expiration in hours (None = never expires)

    Returns:
        Dict with status and override details
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        # Try to infer the correct type from the value string
        typed_value: Any = value
        if value.lower() in ("true", "false"):
            typed_value = value.lower() == "true"
        elif value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            typed_value = int(value)
        else:
            try:
                typed_value = float(value)
            except ValueError:
                typed_value = value  # Keep as string

        success = mgr.set_override(
            key=key,
            value=typed_value,
            reason=reason,
            expires_in_hours=expires_in_hours,
        )

        if success:
            exp_msg = f" (expires in {expires_in_hours}h)" if expires_in_hours else ""
            return {
                "status": "success",
                "key": key,
                "value": typed_value,
                "expires_in_hours": expires_in_hours,
                "message": f"Override set: {key} = {typed_value}{exp_msg}",
            }
        return {"status": "error", "message": f"Failed to set override for {key}"}

    except Exception as e:
        logger.error(f"Error setting override: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def clear_portfolio_override(
    key: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Clear a portfolio configuration override.

    Args:
        key: Config key to clear
        reason: Optional reason for clearing

    Returns:
        Dict with status
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        success = mgr.clear_override(key, reason=reason)
        if success:
            return {"status": "success", "message": f"Override cleared: {key}"}
        return {"status": "error", "message": f"Failed to clear override for {key}"}

    except Exception as e:
        logger.error(f"Error clearing override: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def clear_all_portfolio_overrides(reason: Optional[str] = None) -> Dict[str, Any]:
    """
    Clear all active portfolio configuration overrides.

    Args:
        reason: Optional reason for clearing

    Returns:
        Dict with status and count of cleared overrides
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        count = mgr.clear_all_overrides(reason=reason)
        return {
            "status": "success",
            "cleared_count": count,
            "message": f"Cleared {count} override(s)",
        }

    except Exception as e:
        logger.error(f"Error clearing all overrides: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def get_portfolio_value(key: str, default: Optional[str] = None) -> Dict[str, Any]:
    """
    Get effective portfolio config value (override or default).

    Args:
        key: Config key in dot notation
        default: Default value if not found

    Returns:
        Dict with status and effective value
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        value = mgr.get_effective_value(key, default=default)

        # Check if it's an override or default
        overrides = mgr.get_all_active_overrides()
        is_override = any(o.config_key == key for o in overrides)

        return {
            "status": "success",
            "key": key,
            "value": value,
            "source": "override" if is_override else "default",
        }

    except Exception as e:
        logger.error(f"Error getting portfolio value: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def list_portfolio_overrides() -> Dict[str, Any]:
    """
    List all active portfolio configuration overrides.

    Returns:
        Dict with status and list of active overrides
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        overrides = mgr.get_all_active_overrides()

        override_list = [
            {
                "key": o.config_key,
                "value": o.override_value,
                "type": o.value_type,
                "reason": o.reason,
                "expires_at": o.expires_at,
                "created_at": o.created_at,
            }
            for o in overrides
        ]

        return {
            "status": "success",
            "count": len(override_list),
            "overrides": override_list,
        }

    except Exception as e:
        logger.error(f"Error listing overrides: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def get_override_history(
    key: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Get portfolio override history.

    Args:
        key: Optional filter by config key
        limit: Max records to return (default: 20)

    Returns:
        Dict with status and history records
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        history = mgr.get_override_history(key=key, limit=limit)

        return {
            "status": "success",
            "count": len(history),
            "filter_key": key,
            "history": history,
        }

    except Exception as e:
        logger.error(f"Error getting override history: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def get_portfolio_summary() -> Dict[str, Any]:
    """
    Get summary of portfolio override status.

    Returns:
        Dict with status and summary
    """
    try:
        mgr = _get_override_manager()
        if not mgr:
            return {"status": "error", "message": "Override manager not available"}

        summary = mgr.get_summary()
        return {
            "status": "success",
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# =============================================================================
# Display Functions
# =============================================================================


def show_portfolio_overrides() -> str:
    """Display active portfolio overrides with formatted output."""
    result = list_portfolio_overrides()

    if result["status"] == "error":
        return f"Error: {result.get('error', result.get('message', 'Unknown'))}"

    overrides = result.get("overrides", [])
    if not overrides:
        return "No active portfolio overrides. Using YAML defaults."

    lines = [f"Portfolio Overrides ({len(overrides)})", "=" * 50]
    for o in overrides:
        exp = f" [expires: {o['expires_at']}]" if o.get("expires_at") else ""
        reason = f" ({o['reason']})" if o.get("reason") else ""
        lines.append(f"  {o['key']} = {o['value']} [{o['type']}]{exp}{reason}")

    return "\n".join(lines)


def show_portfolio_config() -> str:
    """Display effective portfolio configuration (overrides + defaults)."""
    try:
        mgr = _get_override_manager()
        if not mgr:
            return "Error: Override manager not available"

        # Get all overrides
        overrides = mgr.get_all_active_overrides()
        override_keys = {o.config_key for o in overrides}

        # Common portfolio config keys to display
        common_keys = [
            "portfolio.risk_per_trade_pct",
            "portfolio.max_position_pct",
            "portfolio.max_exposure_pct",
            "limits.max_open_positions",
            "limits.max_daily_trades",
            "limits.min_position_usd",
            "limits.max_position_usd",
            "sector_limits.enabled",
            "sector_limits.max_sector_pct",
        ]

        lines = ["Portfolio Configuration", "=" * 50]

        for key in common_keys:
            value = mgr.get_effective_value(key)
            if value is not None:
                marker = " [OVERRIDE]" if key in override_keys else ""
                lines.append(f"  {key}: {value}{marker}")

        if override_keys - set(common_keys):
            lines.append("")
            lines.append("Additional Overrides:")
            for key in override_keys:
                if key not in common_keys:
                    value = mgr.get_effective_value(key)
                    lines.append(f"  {key}: {value} [OVERRIDE]")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# FunctionTool Registration
# =============================================================================


set_portfolio_override_tool = FunctionTool(
    func=set_portfolio_override,
    name="set_portfolio_override",
    description=(
        "Save an override for a portfolio configuration value. It's only stored: sizing, "
        "risk checks and the routines don't read overrides, so no trade changes."
    ),
)

clear_portfolio_override_tool = FunctionTool(
    func=clear_portfolio_override,
    name="clear_portfolio_override",
    description="Clear a portfolio configuration override to restore default.",
)

clear_all_overrides_tool = FunctionTool(
    func=clear_all_portfolio_overrides,
    name="clear_all_portfolio_overrides",
    description="Clear all active portfolio configuration overrides.",
)

get_portfolio_value_tool = FunctionTool(
    func=get_portfolio_value,
    name="get_portfolio_value",
    description=(
        "Get a saved portfolio configuration value (override or default), not the account's "
        "value. Sizing, risk checks and the routines don't read these."
    ),
)

list_portfolio_overrides_tool = FunctionTool(
    func=list_portfolio_overrides,
    name="list_portfolio_overrides",
    description="List all active portfolio configuration overrides.",
)

get_override_history_tool = FunctionTool(
    func=get_override_history,
    name="get_override_history",
    description="Get history of portfolio override changes.",
)

get_portfolio_summary_tool = FunctionTool(
    func=get_portfolio_summary,
    name="get_portfolio_summary",
    description="Get summary of portfolio override status.",
)

show_portfolio_overrides_tool = FunctionTool(
    func=show_portfolio_overrides,
    name="show_portfolio_overrides",
    description="Display active portfolio overrides with formatted output.",
)

show_portfolio_config_tool = FunctionTool(
    func=show_portfolio_config,
    name="show_portfolio_config",
    description="Display effective portfolio configuration.",
)


# Export list for CLI tools registry
CLI_PORTFOLIO_OVERRIDE_TOOLS = [
    set_portfolio_override_tool,
    clear_portfolio_override_tool,
    clear_all_overrides_tool,
    get_portfolio_value_tool,
    list_portfolio_overrides_tool,
    get_override_history_tool,
    get_portfolio_summary_tool,
    show_portfolio_overrides_tool,
    show_portfolio_config_tool,
]

__all__ = [
    "set_portfolio_override",
    "clear_portfolio_override",
    "clear_all_portfolio_overrides",
    "get_portfolio_value",
    "list_portfolio_overrides",
    "get_override_history",
    "get_portfolio_summary",
    "show_portfolio_overrides",
    "show_portfolio_config",
    "CLI_PORTFOLIO_OVERRIDE_TOOLS",
]
