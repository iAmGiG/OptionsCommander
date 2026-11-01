"""
CLI Tools - FunctionTool-based command handlers.

Issue #433: Refactor cli_session.py to use FunctionTool architecture.
Issue #455: Create FunctionTool infrastructure with registry.
Issue #457: Portfolio and account display tools extraction (Phase 1C).
Issue #458: Order, scheduler, and alert tools extraction (Phase 1D).

This module contains all CLI command implementations as FunctionTool objects
(Microsoft Agent Framework's, since #606; they were AutoGen's). The CLI calls the
functions directly; the tool objects carry the name, description and schema for agents.

Architecture Benefits:
- Type hints auto-generate schemas
- Pure functions → easy to test
- Consistent with agent-based design
- Tools can be reused by agents
- ~3000 line monolith → modular 200-line files
"""

import logging
from typing import Dict, List, Optional

from agent_framework import FunctionTool

logger = logging.getLogger(__name__)

# Tool category constants
MODE_TOOLS = "mode"
TIMEFRAME_TOOLS = "timeframe"
ACCOUNT_TOOLS = "account"
PORTFOLIO_TOOLS = "portfolio"
ORDER_TOOLS = "order"
SCHEDULER_TOOLS = "scheduler"
ALERT_TOOLS = "alert"
# DB-backed tools (Issue #481)
USER_ALERT_TOOLS = "user_alert"
WATCHLIST_TOOLS = "watchlist"
PORTFOLIO_OVERRIDE_TOOLS = "portfolio_override"
# A Stream tools (Issues #366, #414)
ENTRY_PLANNING_TOOLS = "entry_planning"
TRAILING_STOP_TOOLS = "trailing_stop"
# Voter tools (Issue #488)
VOTER_TOOLS = "voter"
# Backup tools (Issue #490)
BACKUP_TOOLS = "backup"
# GTT tools (Issue #340)
GTT_TOOLS = "gtt"


class CliToolRegistry:
    """
    Registry for CLI FunctionTools.

    Manages CLI command tools organized by category for easy discovery
    and integration with both CLI session and agents.

    Example:
        >>> registry = CliToolRegistry()
        >>> registry.register_tool(my_tool, category=MODE_TOOLS)
        >>> mode_tools = registry.get_tools_by_category(MODE_TOOLS)
        >>> all_tools = registry.get_all_tools()
    """

    def __init__(self):
        """Initialize empty tool registry."""
        self._tools: Dict[str, FunctionTool] = {}
        self._categories: Dict[str, List[str]] = {}

    def register_tool(self, tool: FunctionTool, category: Optional[str] = None) -> None:
        """
        Register a FunctionTool in the registry.

        Args:
            tool: FunctionTool instance to register
            category: Optional category for organization (e.g., "mode", "timeframe")
        """
        tool_name = tool.name

        if tool_name in self._tools:
            logger.warning(f"Tool '{tool_name}' already registered, overwriting")

        self._tools[tool_name] = tool

        if category:
            if category not in self._categories:
                self._categories[category] = []
            if tool_name not in self._categories[category]:
                self._categories[category].append(tool_name)

        logger.debug(f"Registered tool: {tool_name} (category: {category or 'none'})")

    def get_tool(self, tool_name: str) -> Optional[FunctionTool]:
        """
        Get a tool by name.

        Args:
            tool_name: Name of the tool

        Returns:
            FunctionTool instance or None if not found
        """
        return self._tools.get(tool_name)

    def get_tools_by_category(self, category: str) -> List[FunctionTool]:
        """
        Get all tools in a category.

        Args:
            category: Category name (e.g., MODE_TOOLS)

        Returns:
            List of FunctionTool instances in the category
        """
        tool_names = self._categories.get(category, [])
        return [self._tools[name] for name in tool_names if name in self._tools]

    def get_all_tools(self) -> List[FunctionTool]:
        """
        Get all registered tools.

        Returns:
            List of all FunctionTool instances
        """
        return list(self._tools.values())

    def get_categories(self) -> List[str]:
        """
        Get all category names.

        Returns:
            List of category names
        """
        return list(self._categories.keys())


# Global registry instance
_registry = CliToolRegistry()


def register_cli_tool(tool: FunctionTool, category: Optional[str] = None) -> None:
    """
    Register a CLI tool in the global registry.

    Args:
        tool: FunctionTool to register
        category: Optional category
    """
    _registry.register_tool(tool, category)


def get_cli_tool(tool_name: str) -> Optional[FunctionTool]:
    """
    Get a CLI tool by name.

    Args:
        tool_name: Name of the tool

    Returns:
        FunctionTool or None
    """
    return _registry.get_tool(tool_name)


def get_cli_tools_by_category(category: str) -> List[FunctionTool]:
    """
    Get all CLI tools in a category.

    Args:
        category: Category constant (e.g., MODE_TOOLS)

    Returns:
        List of FunctionTools
    """
    return _registry.get_tools_by_category(category)


def get_all_cli_tools() -> List[FunctionTool]:
    """
    Get all registered CLI tools.

    Returns:
        List of all CLI FunctionTools
    """
    return _registry.get_all_tools()


def _load_module_tools(module_name: str, tools_attr: str, category: Optional[str]) -> None:
    """
    Load tools from a module and register them.

    Args:
        module_name: Name of module to import (relative to this package)
        tools_attr: Attribute name containing the tools list
        category: Category to register tools under
    """
    try:
        import importlib

        module = importlib.import_module(f".{module_name}", package=__name__)
        tools = getattr(module, tools_attr, [])
        for tool in tools:
            register_cli_tool(tool, category=category)
        logger.debug("Loaded %s", module_name)
    except Exception as e:
        logger.warning("Failed to load %s: %s", module_name, e)


def _discover_and_register_tools() -> None:
    """
    Auto-discover and register tools from submodules.

    This function imports tool modules and automatically registers
    their tools in the global registry. Called at module import time.
    """
    # Define tool modules to load: (module_name, tools_attr, category)
    tool_modules = [
        # Phase 1B (#456): Mode and timeframe tools
        ("mode_tools", "CLI_MODE_TOOLS", MODE_TOOLS),
        ("timeframe_tools", "CLI_TIMEFRAME_TOOLS", TIMEFRAME_TOOLS),
        # Phase 1C (#457): Portfolio and account display tools
        ("portfolio_tools", "CLI_PORTFOLIO_TOOLS", PORTFOLIO_TOOLS),
        ("account_display_tools", "CLI_ACCOUNT_DISPLAY_TOOLS", ACCOUNT_TOOLS),
        # Phase 1D (#458): Order, scheduler, and alert tools
        ("order_tools", "CLI_ORDER_TOOLS", ORDER_TOOLS),
        ("scheduler_tools", "CLI_SCHEDULER_TOOLS", SCHEDULER_TOOLS),
        ("alert_tools", "CLI_ALERT_TOOLS", ALERT_TOOLS),
        # DB-backed tools (Issue #481): User alerts, watchlists, portfolio overrides
        ("user_alert_tools", "CLI_USER_ALERT_TOOLS", USER_ALERT_TOOLS),
        ("watchlist_tools", "CLI_WATCHLIST_TOOLS", WATCHLIST_TOOLS),
        ("portfolio_override_tools", "CLI_PORTFOLIO_OVERRIDE_TOOLS", PORTFOLIO_OVERRIDE_TOOLS),
        # A Stream tools (Issues #366, #414): Entry planning and trailing stops
        ("entry_planning_tools", "CLI_ENTRY_PLANNING_TOOLS", ENTRY_PLANNING_TOOLS),
        ("trailing_stop_tools", "CLI_TRAILING_STOP_TOOLS", TRAILING_STOP_TOOLS),
        # Voter tools (Issue #488): VoterAgent configuration and explanation
        ("voter_tools", "CLI_VOTER_TOOLS", VOTER_TOOLS),
        # Backup tools (Issue #490): Database backup/restore/export
        ("backup_tools", "CLI_BACKUP_TOOLS", BACKUP_TOOLS),
        # GTT tools (Issue #340): Good-Till-Triggered persistent triggers
        ("gtt_tools", "CLI_GTT_TOOLS", GTT_TOOLS),
    ]

    for module_name, tools_attr, category in tool_modules:
        _load_module_tools(module_name, tools_attr, category)


# Auto-discover tools on import
# Tools that change orders or GTT triggers (which can place orders), the scheduler, the
# execution or trading mode or account, portfolio overrides or the live voter config, or
# that restore or delete data. An agent given one must get a human's approval before it
# runs (Agent Framework's approval_mode, #606); the CLI calls the functions directly.
REQUIRES_APPROVAL = frozenset(
    {
        "cancel_all_orders",
        "cancel_order",
        "cancel_symbol_orders",
        "create_gtt_trigger",
        "create_gtt_oco_pair",
        "delete_gtt_trigger",
        "enable_gtt_trigger",
        "disable_gtt_trigger",
        "enable_scheduler",
        "set_routine_enabled",
        "set_trading_mode",
        "switch_account",
        "set_portfolio_override",
        "clear_portfolio_override",
        "clear_all_portfolio_overrides",
        "apply_voter_preset",
        "promote_voter",
        "demote_voter",
        "set_voter_role",
        "restore_backup",
        "cleanup_old_backups",
    }
)


def _require_approval() -> None:
    """Mark the REQUIRES_APPROVAL tools as needing approval before an agent runs them."""
    for name in REQUIRES_APPROVAL:
        tool = _registry.get_tool(name)
        if tool is None:
            logger.warning("Tool %s needs approval but is not registered", name)
        else:
            tool.approval_mode = "always_require"


_discover_and_register_tools()
_require_approval()

# Export registry for direct access if needed
CLI_TOOL_REGISTRY = _registry

__all__ = [
    # Registry classes
    "CliToolRegistry",
    "CLI_TOOL_REGISTRY",
    # Helper functions
    "register_cli_tool",
    "get_cli_tool",
    "get_cli_tools_by_category",
    "get_all_cli_tools",
    # Category constants
    "MODE_TOOLS",
    "TIMEFRAME_TOOLS",
    "ACCOUNT_TOOLS",
    "PORTFOLIO_TOOLS",
    "ORDER_TOOLS",
    "SCHEDULER_TOOLS",
    "ALERT_TOOLS",
    # DB-backed tool categories (Issue #481)
    "USER_ALERT_TOOLS",
    "WATCHLIST_TOOLS",
    "PORTFOLIO_OVERRIDE_TOOLS",
    # A Stream tool categories (Issues #366, #414)
    "ENTRY_PLANNING_TOOLS",
    "TRAILING_STOP_TOOLS",
    # Voter tools (Issue #488)
    "VOTER_TOOLS",
    # Backup tools (Issue #490)
    "BACKUP_TOOLS",
    # GTT tools (Issue #340)
    "GTT_TOOLS",
    # AutoGen core
    "FunctionTool",
]
