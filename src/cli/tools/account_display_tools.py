"""
Account Display Tools - FunctionTool wrappers for account management.

Issue #433/#457: Extract account display commands from cli_session.py.

These tools wrap the existing AccountCommands class (from account_commands.py)
as FunctionTool instances for AutoGen integration.

Account commands handle:
- Listing all configured accounts
- Switching between accounts
- Showing current account details
- Refreshing account discovery

Note: The core logic is in src/cli/account_commands.py (Issue #401).
This module provides FunctionTool wrappers for that functionality.
"""

import logging
from typing import Dict

from agent_framework import FunctionTool

from src.cli.commands.account_commands import get_account_commands

logger = logging.getLogger(__name__)


# =============================================================================
# FunctionTool Wrapper Functions
# =============================================================================


def list_accounts(verbose: bool = False) -> Dict:
    """
    List all configured trading accounts.

    Shows account ID, type, portfolio value, and active status.

    Args:
        verbose: Include additional details (account number, status)

    Returns:
        Dict with:
        - status: "success", "no_accounts", or "error"
        - accounts: List of account info dicts
        - active: Currently active account ID (if any)
    """
    try:
        commands = get_account_commands()
        return commands.list_accounts(verbose=verbose)
    except Exception as e:
        logger.error(f"Error listing accounts: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "accounts": []}


def switch_account(account_id: str) -> Dict:
    """
    Switch to a different paper trading account.

    Changes which account the account views call active. Orders and the routines keep the
    account the CLI started with, until it's restarted with --account. Live accounts are
    refused: the user switches to those in the CLI with a typed confirmation.

    Args:
        account_id: The account identifier to switch to

    Returns:
        Dict with:
        - status: "success", "refused" (not a confirmed paper account) or "error"
        - account_id: The requested account ID
        - account_type: "paper" or "live" (on success)
        - portfolio_value: Current portfolio value (on success)
    """
    try:
        commands = get_account_commands()
        return commands.switch_account(account_id)
    except Exception as e:
        logger.error(f"Error switching account: {e}", exc_info=True)
        return {"status": "error", "account_id": account_id, "error": str(e)}


def show_current_account() -> Dict:
    """
    Show details of the currently active account.

    Displays:
    - Account ID and type
    - Portfolio value and buying power
    - Account status and number

    Returns:
        Dict with:
        - status: "success", "no_active_account", or "not_initialized"
        - account_id: Active account ID
        - account_type: "paper" or "live"
        - portfolio_value: Current portfolio value
        - buying_power: Available buying power
    """
    try:
        commands = get_account_commands()
        return commands.show_current_account()
    except Exception as e:
        logger.error(f"Error showing current account: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def refresh_accounts() -> Dict:
    """
    Refresh account discovery and information.

    Re-discovers all configured accounts and updates their status.
    Useful after adding new accounts or if account info is stale.

    Returns:
        Dict with:
        - status: "success" or "error"
        - accounts: Updated list of accounts
    """
    try:
        commands = get_account_commands()
        return commands.refresh_accounts()
    except Exception as e:
        logger.error(f"Error refreshing accounts: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# =============================================================================
# FunctionTool Registration
# =============================================================================


list_accounts_tool = FunctionTool(
    func=list_accounts,
    name="list_accounts",
    description="List all configured trading accounts with portfolio values and active status.",
)

switch_account_tool = FunctionTool(
    func=switch_account,
    name="switch_account",
    description=(
        "Mark a different paper account active for the account views. Orders and the routines "
        "keep the account the CLI started with. Live accounts are refused: the user must "
        "switch to those in the CLI."
    ),
)

show_current_account_tool = FunctionTool(
    func=show_current_account,
    name="show_current_account",
    description="Show details of the currently active trading account.",
)

refresh_accounts_tool = FunctionTool(
    func=refresh_accounts,
    name="refresh_accounts",
    description="Refresh account discovery and update account information.",
)

# Export list for CLI tools registry
CLI_ACCOUNT_DISPLAY_TOOLS = [
    list_accounts_tool,
    switch_account_tool,
    show_current_account_tool,
    refresh_accounts_tool,
]

__all__ = [
    # Functions
    "list_accounts",
    "switch_account",
    "show_current_account",
    "refresh_accounts",
    # FunctionTools
    "CLI_ACCOUNT_DISPLAY_TOOLS",
    "list_accounts_tool",
    "switch_account_tool",
    "show_current_account_tool",
    "refresh_accounts_tool",
]
