"""
Interactive Help System for Trading CLI

Issue #369: Interactive Help System for CLI
Provides searchable, context-aware help for all trading commands.

Features:
- `/help` - Show all commands grouped by category
- `/help COMMAND` - Show specific command help
- `/help search KEYWORD` - Search help by keyword
- `/help --examples` - Show all examples
"""

import logging
import os
from typing import Dict, List

import yaml

logger = logging.getLogger(__name__)


def _edit_distance(s1: str, s2: str) -> int:
    """Calculate edit distance between two strings."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)  # pylint: disable=arguments-out-of-order
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


class HelpSystem:
    """Interactive help system with searchable documentation."""

    def __init__(self):
        """Initialize help system with command documentation."""
        self.commands = self._load_help_data()
        self.categories = self._extract_categories()

    def _load_help_data(self) -> Dict[str, Dict]:
        """Load help data from YAML configuration file."""

        config_path = os.path.join(
            os.path.dirname(__file__), "../../../config_defaults/help_commands.yaml"
        )

        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if not data:
                    raise ValueError("help_commands.yaml is empty")
                return data
        except Exception as e:
            error_msg = (
                f"ERROR: Could not load help commands from YAML: {e}\n"
                f"Expected file: {config_path}\n"
                f"Please ensure config_defaults/help_commands.yaml exists and is valid."
            )
            raise RuntimeError(error_msg) from e

    def _extract_categories(self) -> Dict[str, List[str]]:
        """Extract unique categories from command data."""
        categories = {}
        for command, data in self.commands.items():
            category = data.get("category", "Other")
            if category not in categories:
                categories[category] = []
            categories[category].append(command)
        return categories

    def get_help_all(self) -> str:
        """Get help for all commands grouped by category."""
        output = []
        output.append("╔════════════════════════════════════════════════════════════════╗")
        output.append("║          OPTIONSCOMMANDER INTERACTIVE CLI - HELP MENU          ║")
        output.append("╚════════════════════════════════════════════════════════════════╝\n")

        for category in sorted(self.categories.keys()):
            output.append(f"\n{category.upper()}")
            output.append("─" * 50)

            for command in sorted(self.categories[category]):
                data = self.commands[command]
                desc = data.get("description", "No description")
                output.append(f"  {command:25} {desc}")

            output.append("")

        output.append("Type '/help COMMAND' for detailed help on any command")
        output.append("Type '/help search KEYWORD' to search for commands")
        output.append("Type '/help --examples' to see command examples")

        return "\n".join(output)

    def _get_similar_commands(self, command: str, max_suggestions: int = 3) -> List[str]:
        """
        Get similar command suggestions using fuzzy matching.

        Uses Levenshtein-like distance to find commands that might be typos.

        Args:
            command: User's input command
            max_suggestions: Maximum number of suggestions to return

        Returns:
            List of similar command names
        """

        # Calculate distances for all commands and aliases
        candidates = []
        command_lower = command.lower()

        for cmd, cmd_data in self.commands.items():
            distance = _edit_distance(command_lower, cmd.lower())
            # Only suggest if edit distance is reasonable (< 40% of command length)
            if distance <= max(2, len(command) * 0.4):
                candidates.append((cmd, distance))

            # Also check aliases
            aliases = cmd_data.get("aliases", [])
            for alias in aliases:
                distance = _edit_distance(command_lower, alias.lower())
                if distance <= max(2, len(command) * 0.4):
                    candidates.append((cmd, distance))

        # Sort by distance and return top suggestions
        candidates.sort(key=lambda x: x[1])
        suggestions = [cmd for cmd, _ in candidates[:max_suggestions]]

        # Remove duplicates while preserving order
        seen = set()
        unique_suggestions = []
        for cmd in suggestions:
            if cmd not in seen:
                seen.add(cmd)
                unique_suggestions.append(cmd)

        return unique_suggestions[:max_suggestions]

    def _matching_commands(self, command: str) -> List[str]:
        """An exact name, then its slash form, then an alias, then names that start with it."""
        for name in (command, "/" + command.lstrip("/")):
            if name in self.commands:
                return [name]
        bare = command.lstrip("/")
        aliased = [
            cmd
            for cmd, data in self.commands.items()
            if bare in (alias.lstrip("/").lower() for alias in data.get("aliases") or [])
        ]
        if aliased:
            return aliased
        return [cmd for cmd in self.commands if cmd.startswith((command, "/" + bare))]

    def get_help_command(self, command: str) -> str:
        """Get detailed help for a specific command."""
        matching = self._matching_commands(command.lower())

        if not matching:
            # Try to suggest similar commands
            suggestions = self._get_similar_commands(command)
            error_msg = f"❌ Command '{command}' not found."

            if suggestions:
                error_msg += "\n\n💡 Did you mean one of these?\n"
                for suggestion in suggestions:
                    desc = self.commands[suggestion].get("description", "")
                    error_msg += f"  - {suggestion:25} {desc}\n"
                error_msg += "\nType '/help COMMAND' to see details for any command."
            else:
                error_msg += " Type '/help' to see all commands."

            return error_msg

        if len(matching) > 1:
            return f"❓ Ambiguous: '{command}' matches: {', '.join(matching)}"

        cmd = matching[0]
        data = self.commands[cmd]

        output = []
        output.append("╔════════════════════════════════════════════════════════════════╗")
        output.append(f"║ {cmd.upper():62} ║")
        output.append("╚════════════════════════════════════════════════════════════════╝\n")

        output.append(f"{data.get('description', 'No description')}\n")

        output.append("USAGE:")
        output.append(f"  {data.get('usage', 'N/A')}\n")

        if data.get("examples"):
            output.append("EXAMPLES:")
            for example in data["examples"]:
                output.append(f"  > {example}")
            output.append("")

        if data.get("aliases"):
            output.append(f"ALIASES: {', '.join(data['aliases'])}")
            output.append("")

        if data.get("details"):
            output.append("DETAILS:")
            output.append(data["details"])
            output.append("")

        if data.get("related"):
            output.append(f"RELATED: {', '.join(data['related'])}")

        return "\n".join(output)

    def search_help(self, keyword: str) -> str:
        """Search help by keyword."""
        keyword = keyword.lower()
        matches = []

        for command, data in self.commands.items():
            # Search in description, usage, tags, and details
            if (
                keyword in data.get("description", "").lower()
                or keyword in data.get("usage", "").lower()
                or keyword in " ".join(data.get("tags", [])).lower()
                or keyword in " ".join(data.get("aliases", [])).lower()
                or keyword in data.get("details", "").lower()
            ):
                matches.append(command)

        if not matches:
            return f"❌ No commands found matching '{keyword}'"

        output = []
        output.append(f"SEARCH RESULTS for '{keyword}' ({len(matches)} found):\n")

        for cmd in sorted(matches):
            data = self.commands[cmd]
            desc = data.get("description", "")
            output.append(f"  {cmd:25} {desc}")

        output.append("")
        output.append("Type '/help COMMAND' for detailed help on any command")

        return "\n".join(output)

    def get_examples(self) -> str:
        """Get all examples."""
        output = []
        output.append("╔════════════════════════════════════════════════════════════════╗")
        output.append("║                      COMMAND EXAMPLES                          ║")
        output.append("╚════════════════════════════════════════════════════════════════╝\n")

        for category in sorted(self.categories.keys()):
            output.append(f"{category.upper()}")
            output.append("─" * 50)

            for command in sorted(self.categories[category]):
                data = self.commands[command]
                if data.get("examples"):
                    output.append(f"\n{command}:")
                    for example in data["examples"]:
                        output.append(f"  > {example}")

            output.append("")

        return "\n".join(output)

    def handle_help_command(self, input_str: str) -> str:
        """
        Handle help command from user input.

        Formats:
        - /help
        - /help COMMAND
        - /help search KEYWORD
        - /help --examples
        """
        parts = input_str.strip().split(None, 2)

        if len(parts) == 1:
            # Just /help
            return self.get_help_all()

        if len(parts) >= 2:
            if parts[1] == "search" and len(parts) >= 3:
                # /help search KEYWORD
                return self.search_help(parts[2])
            elif parts[1] == "--examples":
                # /help --examples
                return self.get_examples()
            else:
                # /help COMMAND
                return self.get_help_command(parts[1])

        return self.get_help_all()
