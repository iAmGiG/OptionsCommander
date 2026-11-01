"""
CLI Commands - Command handlers and registry.

This module contains:
- CommandRegistry: Self-registering slash command pattern (#468)
- Slash commands: /about, /faq, /help, /toggle, /schedule, /exit
- Natural language handlers: account, timeframe, voter

Modules:
- registry: Command registration and dispatch
- slash_commands: All / commands (auto-registered on import)
- account_commands: Account management commands (list, switch, refresh)
- timeframe_commands: Timeframe selection and display
- voter_commands: Voter ranking management (#488)
"""

# Import slash commands to trigger registration
from src.cli.commands import slash_commands  # noqa: F401

# Import natural language command handlers
from src.cli.commands.account_commands import AccountCommands, get_account_commands

# Import registry first
from src.cli.commands.registry import CommandRegistry, command
from src.cli.commands.timeframe_commands import TimeframeCommands, get_timeframe_commands
from src.cli.commands.voter_commands import VoterCommands, get_voter_commands

__all__ = [
    # Registry
    "CommandRegistry",
    "command",
    # Natural language handlers
    "AccountCommands",
    "get_account_commands",
    "TimeframeCommands",
    "get_timeframe_commands",
    "VoterCommands",
    "get_voter_commands",
]
