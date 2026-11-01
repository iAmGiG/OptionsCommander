"""
Watchlist Tools - FunctionTool wrappers for user-defined watchlists.

Issue #480/#481: CLI tools for SQLite-backed watchlists.

These tools handle:
- Creating and managing watchlists
- Adding/removing symbols from watchlists
- Setting default watchlist
- Displaying watchlist contents

Uses src.trading.alerts_watchlists.AlertsWatchlistsManager for persistence.
"""

import logging
from typing import Any, Dict, Optional

from agent_framework import FunctionTool

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_watchlist_manager():
    """Get AlertsWatchlistsManager instance."""
    try:
        from src.trading.alerts_watchlists import get_alerts_watchlists_manager

        return get_alerts_watchlists_manager()
    except Exception as e:
        logger.error(f"Failed to get watchlist manager: {e}")
        return None


# =============================================================================
# FunctionTool Wrapper Functions
# =============================================================================


def create_watchlist(
    name: str,
    description: Optional[str] = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    """
    Create a new watchlist.

    Args:
        name: Unique name for the watchlist
        description: Optional description
        is_default: Set as default watchlist (default: False)

    Returns:
        Dict with status and created watchlist details
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        watchlist = mgr.create_watchlist(
            name=name,
            description=description,
            is_default=is_default,
        )

        if watchlist:
            return {
                "status": "success",
                "watchlist_id": watchlist.id,
                "name": watchlist.name,
                "is_default": watchlist.is_default,
                "message": f"Watchlist '{name}' created",
            }
        return {
            "status": "error",
            "message": f"Failed to create watchlist '{name}' (may already exist)",
        }

    except Exception as e:
        logger.error(f"Error creating watchlist: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def list_watchlists() -> Dict[str, Any]:
    """
    List all watchlists.

    Returns:
        Dict with status and list of watchlists
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        watchlists = mgr.get_watchlists()

        watchlist_data = [
            {
                "id": w.id,
                "name": w.name,
                "description": w.description,
                "is_default": w.is_default,
                "symbol_count": len(w.items),
                "symbols": [item.symbol for item in w.items],
            }
            for w in watchlists
        ]

        return {
            "status": "success",
            "count": len(watchlist_data),
            "watchlists": watchlist_data,
        }

    except Exception as e:
        logger.error(f"Error listing watchlists: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def get_watchlist(name: str) -> Dict[str, Any]:
    """
    Get a specific watchlist by name.

    Args:
        name: Watchlist name

    Returns:
        Dict with status and watchlist details including symbols
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        watchlist = mgr.get_watchlist(name)

        if not watchlist:
            return {"status": "error", "message": f"Watchlist '{name}' not found"}

        return {
            "status": "success",
            "watchlist": {
                "id": watchlist.id,
                "name": watchlist.name,
                "description": watchlist.description,
                "is_default": watchlist.is_default,
                "items": [
                    {
                        "symbol": item.symbol,
                        "notes": item.notes,
                        "added_at": item.added_at,
                    }
                    for item in watchlist.items
                ],
            },
        }

    except Exception as e:
        logger.error(f"Error getting watchlist: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def delete_watchlist(name: str) -> Dict[str, Any]:
    """
    Delete a watchlist.

    Args:
        name: Name of watchlist to delete

    Returns:
        Dict with status
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        success = mgr.delete_watchlist(name)
        if success:
            return {"status": "success", "message": f"Watchlist '{name}' deleted"}
        return {"status": "error", "message": f"Failed to delete watchlist '{name}'"}

    except Exception as e:
        logger.error(f"Error deleting watchlist: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def add_to_watchlist(
    watchlist_name: str,
    symbol: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Add a symbol to a watchlist.

    Args:
        watchlist_name: Name of watchlist
        symbol: Stock ticker to add
        notes: Optional notes about the symbol

    Returns:
        Dict with status
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        success = mgr.add_to_watchlist(
            watchlist_name=watchlist_name,
            symbol=symbol,
            notes=notes,
        )

        if success:
            return {
                "status": "success",
                "message": f"Added {symbol.upper()} to '{watchlist_name}'",
            }
        return {
            "status": "error",
            "message": f"Failed to add {symbol} to '{watchlist_name}'",
        }

    except Exception as e:
        logger.error(f"Error adding to watchlist: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def remove_from_watchlist(watchlist_name: str, symbol: str) -> Dict[str, Any]:
    """
    Remove a symbol from a watchlist.

    Args:
        watchlist_name: Name of watchlist
        symbol: Stock ticker to remove

    Returns:
        Dict with status
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        success = mgr.remove_from_watchlist(watchlist_name, symbol)

        if success:
            return {
                "status": "success",
                "message": f"Removed {symbol.upper()} from '{watchlist_name}'",
            }
        return {
            "status": "error",
            "message": f"Failed to remove {symbol} from '{watchlist_name}'",
        }

    except Exception as e:
        logger.error(f"Error removing from watchlist: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def get_watchlist_symbols(watchlist_name: str) -> Dict[str, Any]:
    """
    Get all symbols in a watchlist.

    Args:
        watchlist_name: Name of watchlist

    Returns:
        Dict with status and list of symbols
    """
    try:
        mgr = _get_watchlist_manager()
        if not mgr:
            return {"status": "error", "message": "Watchlist manager not available"}

        symbols = mgr.get_watchlist_symbols(watchlist_name)

        return {
            "status": "success",
            "watchlist": watchlist_name,
            "count": len(symbols),
            "symbols": symbols,
        }

    except Exception as e:
        logger.error(f"Error getting watchlist symbols: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# =============================================================================
# Display Functions
# =============================================================================


def show_watchlists() -> str:
    """Display all watchlists with formatted output."""
    result = list_watchlists()

    if result["status"] == "error":
        return f"Error: {result.get('error', result.get('message', 'Unknown'))}"

    watchlists = result.get("watchlists", [])
    if not watchlists:
        return "No watchlists. Use '/watchlist create NAME' to create one."

    lines = [f"Watchlists ({len(watchlists)})", "=" * 40]
    for w in watchlists:
        default_marker = " [DEFAULT]" if w.get("is_default") else ""
        desc = f" - {w['description']}" if w.get("description") else ""
        symbols = ", ".join(w.get("symbols", [])[:5])
        if len(w.get("symbols", [])) > 5:
            symbols += f"... (+{len(w['symbols']) - 5} more)"
        lines.append(f"  {w['name']}{default_marker}{desc}")
        if symbols:
            lines.append(f"    Symbols: {symbols}")

    return "\n".join(lines)


def show_watchlist(name: str) -> str:
    """Display a specific watchlist with formatted output."""
    result = get_watchlist(name)

    if result["status"] == "error":
        return f"Error: {result.get('error', result.get('message', 'Unknown'))}"

    w = result.get("watchlist", {})
    items = w.get("items", [])

    default_marker = " [DEFAULT]" if w.get("is_default") else ""
    lines = [f"Watchlist: {w['name']}{default_marker}", "=" * 40]

    if w.get("description"):
        lines.append(f"Description: {w['description']}")

    if not items:
        lines.append("  (empty)")
    else:
        lines.append(f"Symbols ({len(items)}):")
        for item in items:
            notes = f" - {item['notes']}" if item.get("notes") else ""
            lines.append(f"  {item['symbol']}{notes}")

    return "\n".join(lines)


# =============================================================================
# FunctionTool Registration
# =============================================================================


create_watchlist_tool = FunctionTool(
    func=create_watchlist,
    name="create_watchlist",
    description="Create a new watchlist for tracking symbols.",
)

list_watchlists_tool = FunctionTool(
    func=list_watchlists,
    name="list_watchlists",
    description="List all user-defined watchlists.",
)

get_watchlist_tool = FunctionTool(
    func=get_watchlist,
    name="get_watchlist",
    description="Get a specific watchlist by name with all its symbols.",
)

delete_watchlist_tool = FunctionTool(
    func=delete_watchlist,
    name="delete_watchlist",
    description="Delete a watchlist by name.",
)

add_to_watchlist_tool = FunctionTool(
    func=add_to_watchlist,
    name="add_to_watchlist",
    description="Add a stock symbol to a watchlist.",
)

remove_from_watchlist_tool = FunctionTool(
    func=remove_from_watchlist,
    name="remove_from_watchlist",
    description="Remove a stock symbol from a watchlist.",
)

get_watchlist_symbols_tool = FunctionTool(
    func=get_watchlist_symbols,
    name="get_watchlist_symbols",
    description="Get all symbols in a watchlist.",
)

show_watchlists_tool = FunctionTool(
    func=show_watchlists,
    name="show_watchlists",
    description="Display all watchlists with formatted output.",
)

show_watchlist_tool = FunctionTool(
    func=show_watchlist,
    name="show_watchlist",
    description="Display a specific watchlist with formatted output.",
)


# Export list for CLI tools registry
CLI_WATCHLIST_TOOLS = [
    create_watchlist_tool,
    list_watchlists_tool,
    get_watchlist_tool,
    delete_watchlist_tool,
    add_to_watchlist_tool,
    remove_from_watchlist_tool,
    get_watchlist_symbols_tool,
    show_watchlists_tool,
    show_watchlist_tool,
]

__all__ = [
    "create_watchlist",
    "list_watchlists",
    "get_watchlist",
    "delete_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "get_watchlist_symbols",
    "show_watchlists",
    "show_watchlist",
    "CLI_WATCHLIST_TOOLS",
]
