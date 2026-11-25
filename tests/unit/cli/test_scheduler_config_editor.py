"""
The scheduler CLI's editor saves to the YAML and must also reach the session's scheduler
(E6-01). `test morning` right after an edit reads `scheduler.dry_run`, so a dry run just
saved has to be in the live object, or the test acts on the broker while the user believes
it is a dry run. Cron is unaffected: it loads the config on every tick.

The broker is mocked: the trading cycle stands in for CostEfficientTradeCycle and places a
stop unless it runs dry.
"""

import asyncio
from datetime import time as dt_time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import yaml

from src.cli.scheduler.config_editor import SchedulerConfigEditor
from src.cli.scheduler_cli import SchedulerCLI
from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling.daily_scheduler import DailyScheduler, load_scheduler_config


def _cycle():
    """A run that is not a dry run places a stop; a dry run sends nothing."""
    cycle = Mock(last_report_path=None)

    def routine(dry_run=False, **kwargs):
        if not dry_run:
            cycle.order_manager.place_stop_order(symbol="SPY", stop_price=744.34, quantity=14)
        return "report"

    cycle.morning_routine.side_effect = routine
    cycle.evening_routine.side_effect = routine
    return cycle


@pytest.fixture
def session(tmp_path, monkeypatch):
    """One CLI session: a scheduler loaded from a tmp YAML and the editor on the same file."""
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler.time, "sleep", lambda seconds: None)
    config_file = tmp_path / "scheduler_config.yaml"
    config_file.write_text("enabled: true\ndry_run: false\n")
    cycle = _cycle()
    scheduler = DailyScheduler(config_file=str(config_file), trading_cycle=cycle)
    cli = SchedulerCLI(scheduler=scheduler)
    cli.config_editor = SchedulerConfigEditor(str(config_file))  # not the tracked default
    cli.monitor.daemon_manager = MagicMock()
    cli.monitor.daemon_manager.is_running.return_value = False
    cli.monitor.daemon_manager.cron_entry.return_value = None
    return SimpleNamespace(cli=cli, scheduler=scheduler, cycle=cycle, config_file=config_file)


def _answers(monkeypatch, *answers):
    """What the user types at the editor's prompts, in order."""
    typed = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))


def _run(cli, command):
    asyncio.run(cli._handle_command(command))


def _task(scheduler, name):
    return next(t for t in scheduler.tasks if t.name == name)


# --- E6-01: a saved dry run takes effect in the same session ------------------------------


def test_without_the_edit_the_test_command_acts_on_the_broker(session):
    """The stand-in cycle places a stop when the run is live, so the next test means something."""
    _run(session.cli, "test morning")

    session.cycle.morning_routine.assert_called_once_with(dry_run=False)
    session.cycle.order_manager.place_stop_order.assert_called_once()


@pytest.mark.parametrize("routine", ["morning", "evening"])
def test_a_saved_dry_run_takes_effect_in_the_same_session(session, monkeypatch, capsys, routine):
    _answers(monkeypatch, "5", "yes", "0")
    _run(session.cli, "edit")

    assert yaml.safe_load(session.config_file.read_text())["dry_run"] is True
    assert session.scheduler.dry_run is True  # the session's object, not a discarded copy
    assert session.scheduler.config == load_scheduler_config(str(session.config_file))
    assert "Scheduler reloaded" in capsys.readouterr().out

    _run(session.cli, f"test {routine}")

    getattr(session.cycle, f"{routine}_routine").assert_called_once_with(dry_run=True)
    session.cycle.order_manager.place_stop_order.assert_not_called()
    assert "dry run: no orders are sent" in capsys.readouterr().out


def test_a_saved_dry_run_off_takes_effect_too(session, monkeypatch):
    session.config_file.write_text("enabled: true\ndry_run: true\n")
    session.scheduler.config["dry_run"] = True
    _answers(monkeypatch, "5", "no", "0")

    _run(session.cli, "edit")

    assert session.scheduler.dry_run is False


def test_status_shows_the_saved_settings(session, monkeypatch, capsys):
    _answers(monkeypatch, "5", "y", "2", "09:25", "0")
    _run(session.cli, "edit")
    capsys.readouterr()

    _run(session.cli, "status")

    out = capsys.readouterr().out
    assert "Dry Run: True" in out
    assert "Morning: 09:25:00 ET" in out


def test_saved_times_and_retries_reach_the_scheduled_tasks(session, monkeypatch):
    _answers(monkeypatch, "2", "09:25", "3", "15:45:00", "4", "5", "0")

    _run(session.cli, "edit")

    morning, evening = (
        _task(session.scheduler, "morning_routine"),
        _task(session.scheduler, "evening_routine"),
    )
    assert morning.scheduled_time == dt_time(9, 25)
    assert evening.scheduled_time == dt_time(15, 45)
    assert morning.retry_count == evening.retry_count == 5
    assert session.scheduler.config["max_retries"] == 5
    assert session.scheduler.trading_cycle is session.cycle  # the cycle is kept


def test_the_reload_keeps_the_monitor_and_wizard_on_the_same_scheduler(session, monkeypatch):
    _answers(monkeypatch, "5", "yes", "0")

    _run(session.cli, "edit")

    assert session.cli.monitor.scheduler is session.scheduler
    assert session.cli.setup_wizard.scheduler is session.scheduler
    assert session.cli.monitor.scheduler.dry_run is True


# --- The editor's other paths -------------------------------------------------------------


def test_cancel_changes_nothing(session, monkeypatch, capsys):
    before = session.config_file.read_text()
    _answers(monkeypatch, "5", "yes", "q")

    result = asyncio.run(session.cli.config_editor.edit_interactive(session.scheduler))

    assert result is None
    assert session.config_file.read_text() == before
    assert session.scheduler.dry_run is False
    assert "Cancelled" in capsys.readouterr().out


def test_invalid_values_are_rejected_and_valid_ones_kept(session, monkeypatch, capsys):
    _answers(monkeypatch, "2", "25:99", "2", "09:30", "4", "11", "4", "abc", "4", "2", "x", "0")

    config = asyncio.run(session.cli.config_editor.edit_interactive())

    assert config["morning_routine_time"] == "09:30:00"
    assert config["max_retries"] == 2
    saved = yaml.safe_load(session.config_file.read_text())
    assert saved["morning_routine_time"] == "09:30:00" and saved["max_retries"] == 2
    out = capsys.readouterr().out
    assert out.count("Invalid time format") == 1
    assert out.count("Value must be between 1 and 10") == 2
    assert "Invalid choice" in out
    assert "Reloading scheduler" not in out  # no scheduler was passed


def test_the_enabled_flag_is_saved_to_state_and_the_scheduler(session, monkeypatch):
    assert session.scheduler.effective_settings()["enabled"] is True
    _answers(monkeypatch, "1", "no", "0")

    _run(session.cli, "edit")

    assert session.scheduler.effective_settings()["enabled"] is False
    assert yaml.safe_load(session.config_file.read_text())["enabled"] is False
    assert session.scheduler.config["enabled"] is False  # the in-memory object too


def test_enable_and_disable_commands_update_the_scheduler(session):
    _run(session.cli, "disable")
    assert session.scheduler.effective_settings()["enabled"] is False
    assert session.scheduler.config["enabled"] is False

    _run(session.cli, "enable")
    assert session.scheduler.effective_settings()["enabled"] is True
    assert session.scheduler.config["enabled"] is True


def test_the_reload_does_not_depend_on_reading_the_file_back(session, monkeypatch, capsys):
    """load_scheduler_config falls back to the defaults (dry run off) on a failed read, so a
    re-read of a file damaged after the save would reload as a live session."""
    editor = session.cli.config_editor
    real_save = editor.save

    def save_then_damage(config):
        saved = real_save(config)
        session.config_file.write_text("dry_run: [unclosed\n")
        return saved

    monkeypatch.setattr(editor, "save", save_then_damage)
    _answers(monkeypatch, "5", "yes", "0")

    asyncio.run(editor.edit_interactive(session.scheduler))

    assert load_scheduler_config(str(session.config_file))["dry_run"] is False  # the fallback
    assert session.scheduler.dry_run is True
    assert "Scheduler reloaded" in capsys.readouterr().out


def test_a_failed_reload_leaves_the_scheduler_whole_and_the_save_stands(
    session, monkeypatch, capsys
):
    def boom():
        raise RuntimeError("no reload")

    monkeypatch.setattr(session.scheduler, "_create_default_tasks", boom)
    config_before, tasks_before = session.scheduler.config, session.scheduler.tasks
    _answers(monkeypatch, "5", "yes", "4", "5", "0")

    config = asyncio.run(session.cli.config_editor.edit_interactive(session.scheduler))

    assert config["dry_run"] is True
    assert yaml.safe_load(session.config_file.read_text())["dry_run"] is True
    # neither half of the reload took: no new dry_run beside the old tasks' retry counts
    assert session.scheduler.config is config_before
    assert session.scheduler.tasks is tasks_before
    assert session.scheduler.dry_run is False
    assert _task(session.scheduler, "morning_routine").retry_count != 5
    out = capsys.readouterr().out
    assert "Configuration saved!" in out
    assert "Could not reload: no reload" in out
    assert "keeps its previous settings" in out
    assert "Scheduler reloaded" not in out


def test_a_failed_save_reloads_nothing(session, monkeypatch, capsys):
    monkeypatch.setattr(session.cli.config_editor, "save", lambda config: False)
    _answers(monkeypatch, "5", "yes", "0")

    asyncio.run(session.cli.config_editor.edit_interactive(session.scheduler))

    assert session.scheduler.dry_run is False
    out = capsys.readouterr().out
    assert "Failed to save configuration" in out
    assert "Reloading scheduler" not in out


def test_the_default_editor_points_at_the_tracked_yaml():
    assert SchedulerConfigEditor().config_file == "config_defaults/scheduler_config.yaml"
