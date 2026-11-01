"""
Abilities only the agents' tools had, now commands (#331): /plan, /trailing, /gtt oco,
/watchlist delete and /backup cleanup. Market data is mocked; the watchlist, GTT and backup
stores are real, on temp paths. Nothing removes anything without a typed yes.
"""

import asyncio
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from config_defaults.trading_config import ClimbRate, TrailingStopConfig

from src.cli.commands import slash_commands  # noqa: F401  registers the commands
from src.cli.commands.registry import CommandRegistry
from src.cli.tools import (
    backup_tools,
    entry_planning_tools,
    gtt_tools,
    trailing_stop_tools,
    watchlist_tools,
)
from src.trading.alerts_watchlists import AlertsWatchlistsManager
from src.trading.gtt.gtt_manager import GTTManager
from src.trading.orders.trailing_stop_manager import TrailingStopManager
from src.utils.db_backup import DBBackupManager


def run(cmd, capsys):
    """Run a slash command; returns what it printed."""
    handled, keep_going = asyncio.run(CommandRegistry.execute(cmd, MagicMock()))
    assert handled and keep_going
    return capsys.readouterr().out


def answering(monkeypatch, answer):
    """Answer every prompt with this; returns the prompts asked."""
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


# /plan


@pytest.mark.parametrize(
    "cmd, tool, args",
    [
        ("/plan spy", "get_entry_plan", ("SPY", "BUY")),
        ("/plan spy levels", "get_support_resistance", ("SPY",)),
        ("/plan spy volume", "get_volume_analysis", ("SPY",)),
        ("/plan spy ATR", "get_atr", ("SPY",)),
    ],
)
def test_plan_reaches_its_view(cmd, tool, args, capsys):
    with patch(f"src.cli.tools.entry_planning_tools.{tool}", return_value="the view") as view:
        assert "the view" in run(cmd, capsys)
    view.assert_called_once_with(*args)


@pytest.mark.parametrize("cmd", ["/plan", "/plan spy nonsense", "/plan spy levels extra"])
def test_plan_shows_its_usage(cmd, capsys):
    assert "Usage: /plan" in run(cmd, capsys)


@pytest.mark.parametrize(
    "support, resistance, expected",
    [
        (95.0, 110.0, ("-5.0% from current", "+10.0% from current")),
        (102.0, 110.0, ("+2.0% from current", "+10.0% from current")),  # price under its support
        (90.0, 98.0, ("-10.0% from current", "-2.0% from current")),  # price over its resistance
    ],
)
def test_levels_are_signed_from_the_price(support, resistance, expected):
    levels = {"support": support, "resistance": resistance, "range": resistance - support}
    with (
        patch.object(
            entry_planning_tools, "_completed_bars", return_value=(MagicMock(), 100.0, "x")
        ),
        patch.object(entry_planning_tools, "find_support_resistance", return_value=levels),
    ):
        text = entry_planning_tools.get_support_resistance("SPY")

    assert all(distance in text for distance in expected)


# /trailing


def live_stop(entry, price, stop):
    """Where the routines' manager puts the stop, at the live settings."""
    config = MagicMock()
    trailing = TrailingStopConfig(climb_rate="medium")
    config.get_trailing_stop_config.return_value = trailing
    manager = TrailingStopManager(order_manager=None, config=config)
    manager.register_position("X", entry, stop, 1)
    return manager.calculate_new_stop("X", price)


def test_trailing_shows_the_settings(capsys):
    assert "Climb Rate" in run("/trailing", capsys)


def test_trailing_explain_and_compare(capsys):
    locks = ClimbRate.get_gain_locks("fast")

    assert f"Lock {locks[1] * 100:.0f}%" in run("/trailing explain fast", capsys)
    assert "Invalid climb rate" in run("/trailing explain nonsense", capsys)
    assert "breakeven" in run("/trailing compare", capsys)


@pytest.mark.parametrize(
    "price, tier",
    [
        (103.0, "+2-4%: move the stop to breakeven"),
        (105.0, "+4-6%: lock 25%"),
        (108.0, "+6-10%: lock 50%"),
        (112.0, "+10%+: lock 75%"),
    ],
)
def test_the_example_gives_the_routines_level(price, tier, capsys):
    text = run(f"/trailing example 100 {price} 95 medium", capsys)

    assert tier in text
    assert f"New Stop: ${live_stop(100.0, price, 95.0):.2f}" in text


def test_the_example_never_moves_a_stop_down(capsys):
    """+3% is the breakeven tier, and a stop already above breakeven stays."""
    text = run("/trailing example 100 103 101.5 medium", capsys)

    assert "stays at $101.50" in text


def test_the_example_checks_its_input(capsys):
    assert "New Stop: none" in run("/trailing example 100 101 95", capsys)
    assert "already have fired" in run("/trailing example 100 95 96", capsys)
    assert "must be positive" in run("/trailing example 0 95 90", capsys)
    assert "Invalid climb rate" in run("/trailing example 100 106 95 warp", capsys)
    assert "Usage: /trailing" in run("/trailing example 100 abc 95", capsys)
    assert "Usage: /trailing" in run("/trailing nonsense", capsys)


def test_the_example_defaults_to_the_live_climb_rate():
    with patch.object(trailing_stop_tools, "_live_climb_rate", return_value="slow"):
        text = trailing_stop_tools.calculate_stop_example(100.0, 105.0, 95.0)

    assert "Climb Rate:    slow" in text
    assert "lock 20%" in text


# /gtt oco


@pytest.fixture
def gtt(tmp_path, monkeypatch):
    manager = GTTManager(db_path=str(tmp_path / "gtt.db"))
    monkeypatch.setattr(gtt_tools, "_get_gtt_manager", lambda: manager)
    return manager


def test_oco_creates_a_linked_pair(gtt, capsys):
    assert "[OK]" in run("/gtt oco spy above 620 below 580 10", capsys)

    pair = {t.condition_type: t for t in gtt.get_triggers(symbol="SPY")}
    assert pair["price_above"].trigger_value == 620.0
    assert pair["price_below"].trigger_value == 580.0
    assert pair["price_above"].oco_group_id == pair["price_below"].oco_group_id is not None
    assert pair["price_above"].max_triggers == 1
    assert pair["price_above"].action_type == "alert"


def test_oco_takes_the_levels_in_either_order(gtt, capsys):
    assert "[OK]" in run("/gtt oco SPY below 580 above 620", capsys)

    assert {t.trigger_value for t in gtt.get_triggers(symbol="SPY")} == {580.0, 620.0}


@pytest.mark.parametrize(
    "cmd, message",
    [
        ("/gtt oco", "Usage: /gtt oco"),
        ("/gtt oco SPY above 620", "Usage: /gtt oco"),
        ("/gtt oco SPY above 620 above 580", "Usage: /gtt oco"),
        ("/gtt oco SPY gain 5 below 580", "Usage: /gtt oco"),
        ("/gtt oco SPY above x below 580", "Usage: /gtt oco"),
        ("/gtt oco SPY above 620 below 580 0", "Usage: /gtt oco"),
        ("/gtt oco SPY above 580 below 620", "must be higher"),
        ("/gtt oco SPY above 620 below -1", "must be higher"),
        ("/gtt oco SPY above 1e999 below 580", "must be higher"),
        ("/gtt oco SPY above nan below 580", "must be higher"),
    ],
)
def test_a_bad_oco_creates_nothing(gtt, cmd, message, capsys):
    assert message in run(cmd, capsys)

    assert gtt.get_triggers(symbol="SPY") == []


# /watchlist delete


@pytest.fixture
def watchlists(tmp_path, monkeypatch):
    manager = AlertsWatchlistsManager(db_path=str(tmp_path / "lists.db"))
    monkeypatch.setattr(watchlist_tools, "_get_watchlist_manager", lambda: manager)
    manager.create_watchlist("tech", "Tech names")
    manager.add_to_watchlist("tech", "AAPL")
    manager.add_to_watchlist("tech", "MSFT")
    return manager


def names(manager):
    return [w["name"] for w in watchlist_tools.list_watchlists()["watchlists"]]


def test_watchlist_delete_asks_and_removes_it(watchlists, monkeypatch, capsys):
    prompts = answering(monkeypatch, "yes")

    assert "[OK]" in run("/watchlist delete tech", capsys)

    assert "'tech' and its 2 symbol(s)" in prompts[0]
    assert "tech" not in names(watchlists)


@pytest.mark.parametrize("answer", ["no", "y", ""])
def test_watchlist_delete_needs_a_typed_yes(watchlists, monkeypatch, capsys, answer):
    answering(monkeypatch, answer)

    assert "Nothing removed" in run("/watchlist delete tech", capsys)

    assert "tech" in names(watchlists)


def test_a_watchlist_that_isnt_there_is_not_reported_deleted(watchlists, monkeypatch, capsys):
    prompts = answering(monkeypatch, "yes")

    assert "No watchlist named 'ghost'" in run("/watchlist delete ghost", capsys)
    assert "Usage: /watchlist delete" in run("/watchlist delete", capsys)

    assert prompts == []
    assert watchlist_tools.delete_watchlist("ghost")["status"] == "error"


def test_a_deleted_watchlist_leaves_no_symbols_behind(watchlists, monkeypatch, capsys):
    answering(monkeypatch, "yes")
    run("/watchlist delete tech", capsys)

    watchlists.create_watchlist("tech", "Again")

    assert (
        next(w for w in watchlist_tools.list_watchlists()["watchlists"] if w["name"] == "tech")[
            "symbols"
        ]
        == []
    )


# /backup cleanup


@pytest.fixture
def backups(tmp_path, monkeypatch):
    """Eight old user backups, one recent, and a file that isn't a database backup."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("AUTOTRADER_STATE_DIR", str(state))
    manager = DBBackupManager()
    monkeypatch.setattr(backup_tools, "_get_backup_manager", lambda: manager)
    old = time.time() - 90 * 86400
    for i in range(8):
        path = manager.backup_dir / f"user_2020010{i}_000000.db"
        path.write_bytes(b"x")
        os.utime(path, (old + i, old + i))
    (manager.backup_dir / "user_20261030_000000.db").write_bytes(b"x")
    notes = manager.backup_dir / "CLAUDE.md.backup"
    notes.write_text("keep me")
    os.utime(notes, (old, old))
    return manager


def backup_files(manager):
    return sorted(path.name for path in manager.backup_dir.iterdir())


def test_cleanup_lists_what_would_go_and_removes_only_that(backups, monkeypatch, capsys):
    prompts = answering(monkeypatch, "yes")

    text = run("/backup cleanup", capsys)

    assert "Remove these 4 backup(s)?" in prompts[0]
    assert "user_20200100_000000.db" in text
    assert "user_20200104_000000.db" not in text
    assert "Removed 4 backup(s)" in text
    left = backup_files(backups)
    assert "CLAUDE.md.backup" in left  # only database backups are candidates
    assert "user_20261030_000000.db" in left
    assert len([name for name in left if name.startswith("user_")]) == 5


@pytest.mark.parametrize("answer", ["no", "y", ""])
def test_cleanup_needs_a_typed_yes(backups, monkeypatch, capsys, answer):
    answering(monkeypatch, answer)
    before = backup_files(backups)

    assert "Nothing removed" in run("/backup cleanup 30", capsys)

    assert backup_files(backups) == before


def test_cleanup_removes_only_what_it_listed(backups, monkeypatch, capsys):
    """A file that ages past the cutoff while the prompt waits isn't one the user saw."""
    listed = backup_tools.backups_to_clean(30)
    kept, shown = listed[0], listed[1:]

    assert "Removed 3 backup(s)" in backup_tools.cleanup_old_backups(30, only=shown)

    left = backup_files(backups)
    assert kept in left
    assert not set(shown) & set(left)


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_leaving_the_prompt_removes_nothing(backups, watchlists, monkeypatch, capsys, error):
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=error))
    before = backup_files(backups)

    assert "Nothing removed" in run("/backup cleanup", capsys)
    assert "Nothing removed" in run("/watchlist delete tech", capsys)

    assert backup_files(backups) == before
    assert "tech" in names(watchlists)


@pytest.mark.parametrize("arg", ["0", "-5", "soon", "99999999"])
def test_cleanup_refuses_a_bad_age(backups, monkeypatch, capsys, arg):
    prompts = answering(monkeypatch, "yes")
    before = backup_files(backups)

    assert "Usage: /backup cleanup" in run(f"/backup cleanup {arg}", capsys)

    assert prompts == []
    assert backup_files(backups) == before


def test_cleanup_with_nothing_old_doesnt_ask(backups, monkeypatch, capsys):
    prompts = answering(monkeypatch, "yes")

    assert "No backups to remove" in run("/backup cleanup 3650", capsys)

    assert prompts == []


def test_cleanup_removes_nothing_without_the_manager(backups, monkeypatch):
    monkeypatch.setattr(backup_tools, "_get_backup_manager", lambda: None)
    before = backup_files(backups)

    assert "nothing removed" in backup_tools.cleanup_old_backups(30)
    assert backup_tools.backups_to_clean(30) == []

    assert backup_files(backups) == before


# /watchlist scanner, from the config module's new home


def test_watchlist_scanner_still_reads_its_config(capsys):
    from src.trading.instruments import scanner_config

    assert scanner_config.SCANNER_CONFIG_PATH.exists()
    assert "Scanner Tiered Watchlist" in run("/watchlist scanner", capsys)


# Help


@pytest.mark.parametrize("name", ["/plan", "/trailing"])
def test_the_new_commands_are_listed_with_help(name):
    import yaml

    with open("config_defaults/help_commands.yaml", encoding="utf-8") as handle:
        entries = yaml.safe_load(handle)

    assert CommandRegistry.get(name) is not None
    assert entries[name]["usage"].startswith(name)
