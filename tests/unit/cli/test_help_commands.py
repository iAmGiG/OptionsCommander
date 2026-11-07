"""
/help matches the commands that exist (#662), typed help/exit/quit run their commands, and
alert questions reach the alert view, including the new /alerts (#659). No broker is read.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.cli.cli_session import CLISession
from src.cli.commands import slash_commands  # noqa: F401  registers the commands
from src.cli.commands.registry import CommandRegistry
from src.cli.utils.help_system import HelpSystem
from src.cli.utils.input_parser import is_alert_request, is_cancel_request

REPO = Path(__file__).resolve().parents[3]
ENTRIES = yaml.safe_load((REPO / "config_defaults/help_commands.yaml").read_text())
REMOVED = [
    "morning-routine",
    "approve",
    "reject",
    "monitor",
    "evening-summary",
    "set voter-system",
    "show config",
    "show config-file",
    "show indicators",
    "forward-test start",
    "forward-test report",
    "forward-test status",
    "show watchlist",
]


# Help entries and commands


def test_every_registered_slash_command_has_a_help_entry():
    assert set(CommandRegistry._commands) - set(ENTRIES) == set()


def test_every_slash_entry_is_a_registered_command():
    slash = {name for name in ENTRIES if name.startswith("/")}

    assert slash - set(CommandRegistry._commands) == set()


@pytest.mark.parametrize("name", REMOVED)
def test_typed_commands_with_no_code_are_gone(name):
    assert name not in ENTRIES


def test_related_entries_exist():
    dangling = {
        name: [r for r in entry.get("related") or [] if r not in ENTRIES]
        for name, entry in ENTRIES.items()
    }

    assert {name: refs for name, refs in dangling.items() if refs} == {}


def test_the_timeframe_entry_names_the_real_subcommands_and_presets():
    entry = ENTRIES["/timeframe"]
    text = entry["usage"] + entry["details"]

    for word in ("mode", "list", "presets", "preset", "single", "validate", "trend_following"):
        assert word in text
    assert " set " not in entry["usage"] and "swing" not in text


@pytest.mark.parametrize(
    "asked, shown",
    [
        ("timeframe", "/TIMEFRAME"),  # the slash form beats "timeframe recommendations"
        ("tf", "/TIMEFRAME"),  # an alias
        ("/tf", "/TIMEFRAME"),
        ("quit", "/EXIT"),
        ("alerts", "/ALERTS"),
        ("/gtt", "/GTT"),
        ("rec", "/RECORDS"),  # the start of a name, when nothing else matches
    ],
)
def test_help_finds_a_command_by_name_alias_or_start(asked, shown):
    assert shown in HelpSystem().get_help_command(asked)


# Typed help, exit and quit


@pytest.mark.parametrize(
    "typed, command", [("exit", "/exit"), ("quit", "/exit"), ("help", "/help")]
)
def test_typed_command_words_run_their_slash_command(monkeypatch, typed, command):
    session = CLISession.__new__(CLISession)
    session.autonomy_mode = "confirm"
    session._print_welcome = MagicMock()
    session._process_request = AsyncMock()
    session._handle_command = AsyncMock(return_value=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)

    asyncio.run(session.run())

    session._handle_command.assert_awaited_once_with(command)
    session._process_request.assert_not_awaited()


# Alerts


def routed(typed):
    """The handler a typed request goes to."""
    session = CLISession.__new__(CLISession)
    handlers = (
        "_handle_scheduler_request",
        "_handle_alerts_request",
        "_handle_cancel_request",
        "_handle_account_request",
        "_handle_timeframe_request",
        "_handle_trading_mode_request",
        "_handle_trade_or_status_request",
    )
    for name in handlers:
        setattr(session, name, AsyncMock())
    asyncio.run(session._process_request(typed))
    called = [name for name in handlers if getattr(session, name).await_count]
    assert len(called) == 1
    return called[0]


@pytest.mark.parametrize(
    "typed", ["what are my alerts?", "show alerts", "check my alerts", "any alerts", "alerts"]
)
def test_alert_questions_reach_the_alert_view(typed):
    assert routed(typed) == "_handle_alerts_request"


@pytest.mark.parametrize("typed", ["cancel my alert orders", "cancel all orders"])
def test_a_cancel_that_mentions_alerts_is_still_a_cancel(typed):
    assert not is_alert_request(typed)
    assert routed(typed) == "_handle_cancel_request"


def test_deleting_alerts_is_neither_a_cancel_nor_the_alert_view():
    assert not is_cancel_request("delete all my alerts")
    assert not is_alert_request("delete all my alerts")


@pytest.mark.parametrize("typed", ["alertness of AAPL", "buy 10 AAPL"])
def test_other_words_are_not_alert_requests(typed):
    assert not is_alert_request(typed)


def test_the_alerts_command_shows_the_alert_view(capsys):
    with patch("src.cli.tools.alert_tools.show_alerts", return_value="the alert view") as view:
        handled, keep_going = asyncio.run(CommandRegistry.execute("/alerts", MagicMock()))

    assert handled and keep_going
    view.assert_called_once_with()
    assert "the alert view" in capsys.readouterr().out
