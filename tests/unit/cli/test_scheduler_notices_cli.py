"""
Where people see the notices (#342): the scheduler CLI's status, `notices` and `ack`, the
routine reports, and the agent's read-only count.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock

import pytest

from src.cli.scheduler.monitor import SchedulerMonitor
from src.cli.scheduler_cli import SchedulerCLI
from src.cli.tools import scheduler_tools
from src.trading.scheduling import daily_scheduler, scheduler_state
from src.trading.scheduling import trading_cycle as cycle_module
from src.trading.scheduling.daily_scheduler import DailyScheduler
from src.trading.scheduling.notices import CRITICAL, WARNING, NoticeStore
from src.trading.scheduling.scheduler_state import SchedulerStateManager
from src.trading.scheduling.trading_cycle import CostEfficientTradeCycle

FIRST_SEEN = "2026-10-27 13:25 UTC"
FAILED = "Morning routine failed after 4 attempts"


@pytest.fixture
def store(tmp_path):
    def clock():
        return datetime(2026, 10, 27, 13, 25, tzinfo=timezone.utc)

    return NoticeStore(tmp_path / "notices.json", raise_errors=True, now=clock)


def _seed(store):
    """Three notices: #1 and #2 critical, #3 a warning."""
    store.raise_notice(
        "routine_failed:morning:2026-10-27",
        "routine_failed",
        CRITICAL,
        FAILED,
        "Attempt 4 failed: broker down",
        market_date="2026-10-27",
        report_path="reports/daily/2026-10-27_morning.md",
    )
    store.raise_notice(
        "stop_breach:AAPL:2026-10-27",
        "stop_breach",
        CRITICAL,
        "AAPL is at or below its stop, still open",
        symbol="AAPL",
    )
    store.raise_notice(
        "target_breach:MSFT:2026-10-27",
        "target_breach",
        WARNING,
        "MSFT is at or above its target, still open",
        symbol="MSFT",
    )


def _run(cli, command):
    asyncio.run(cli._handle_command(command))


# --- The scheduler's status -------------------------------------------------------------


def _status(capsys, store):
    scheduler = MagicMock()
    scheduler.effective_settings.return_value = {"enabled": True}
    scheduler.config = {}
    scheduler.get_execution_history.return_value = []
    scheduler.notice_store = store
    monitor = SchedulerMonitor(scheduler)
    monitor.daemon_manager = MagicMock()
    monitor.daemon_manager.is_running.return_value = False
    monitor.daemon_manager.cron_entry.return_value = "*/5 * * * * main.py --run-due"
    monitor.show_status()
    return capsys.readouterr().out


def test_status_says_when_there_are_no_open_notices(capsys, store):
    assert "No open notices" in _status(capsys, store)


def test_status_counts_the_open_notices_and_lists_them(capsys, store):
    _seed(store)

    out = _status(capsys, store)

    assert "Open notices: 3" in out
    assert f"#1 CRITICAL {FAILED} (first seen {FIRST_SEEN})" in out
    assert f"#3 WARNING MSFT is at or above its target, still open (first seen {FIRST_SEEN})" in out
    assert "No open notices" not in out


def test_status_leaves_out_acknowledged_notices(capsys, store):
    _seed(store)
    store.ack(1)

    out = _status(capsys, store)

    assert "Open notices: 2" in out and FAILED not in out


def test_status_lists_the_latest_five_and_counts_the_rest(capsys, store):
    for i in range(1, 8):
        store.raise_notice(f"k{i}", "k", CRITICAL, f"Notice number {i}")

    out = _status(capsys, store)

    assert "Open notices: 7" in out
    assert [f"#{i} CRITICAL Notice number {i}" in out for i in range(1, 8)] == [False] * 2 + [
        True
    ] * 5
    assert "... and 2 older ones" in out


def test_status_still_prints_when_the_notices_cannot_be_read(capsys):
    broken = Mock(open_notices=Mock(side_effect=OSError("disk")))

    out = _status(capsys, broken)

    assert "Open notices unavailable: disk" in out
    assert "Service: CRON entry installed" in out  # the rest of the status is there


def test_status_without_a_scheduler_reads_the_notices_file(capsys, store, monkeypatch):
    _seed(store)
    monkeypatch.setattr("src.cli.scheduler.monitor.NoticeStore", lambda: store)
    monitor = SchedulerMonitor(None)
    monitor._show_open_notices()

    assert "Open notices: 3" in capsys.readouterr().out


def test_notices_says_when_the_file_cannot_be_read(capsys):
    cli = SchedulerCLI()
    cli._notice_store = Mock(open_notices=Mock(side_effect=OSError("disk")))

    _run(cli, "notices")

    assert "Notices unavailable: disk" in capsys.readouterr().out


def test_status_survives_a_notice_it_cannot_format(capsys):
    broken = Mock(open_notices=Mock(return_value=[object()]))

    assert "Open notices unavailable" in _status(capsys, broken)


def test_status_notes_the_ack_command_when_there_is_something_to_ack(capsys, store):
    _seed(store)

    assert "'ack <number|all>' acknowledges" in _status(capsys, store)


# --- The commands -----------------------------------------------------------------------


def test_notices_and_ack_are_registered_commands_that_need_no_scheduler(capsys):
    cli = SchedulerCLI()  # no scheduler, no trading clients

    assert cli._command_registry["notices"]["requires_scheduler"] is False
    assert cli._command_registry["ack"]["requires_scheduler"] is False
    cli._print_welcome()
    out = capsys.readouterr().out
    assert "notices [all]" in out and "ack <number|all>" in out


def test_notices_lists_the_open_notices_with_their_details(capsys, store):
    _seed(store)
    store.ack(2)
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, "notices")

    out = capsys.readouterr().out
    assert "Notices (open)" in out
    assert f"#1 CRITICAL {FAILED} (first seen {FIRST_SEEN})" in out
    assert "Attempt 4 failed: broker down" in out
    assert "Report: reports/daily/2026-10-27_morning.md" in out
    assert "#3 WARNING" in out and "#2 CRITICAL" not in out  # acknowledged


def test_notices_all_includes_the_acknowledged_ones_marked(capsys, store):
    _seed(store)
    store.ack(2)
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, "notices all")

    out = capsys.readouterr().out
    assert "Notices (all)" in out
    acknowledged = [line for line in out.splitlines() if line.startswith("#2 CRITICAL")]
    assert len(acknowledged) == 1 and acknowledged[0].endswith("[acknowledged]")
    assert "#1 CRITICAL" in out and "#3 WARNING" in out


def test_notices_with_nothing_open_says_so(capsys, store):
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, "notices")

    assert "None" in capsys.readouterr().out


def test_notices_takes_only_all(capsys, store):
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, "notices everything")

    assert "Usage: notices [all]" in capsys.readouterr().out


def test_ack_acknowledges_one_notice_by_number(capsys, store):
    _seed(store)
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, "ack 3")

    assert "Acknowledged 1 notice(s)" in capsys.readouterr().out
    assert [n.seq for n in store.open_notices()] == [1, 2]
    _run(cli, "ack 3")  # again, and one that doesn't exist
    _run(cli, "ack 42")
    assert capsys.readouterr().out.count("No open notice to acknowledge") == 2


def test_ack_all_acknowledges_every_open_notice(capsys, store):
    _seed(store)
    store.ack(1)
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, "ack all")

    assert "Acknowledged 2 notice(s)" in capsys.readouterr().out
    assert store.open_notices() == [] and len(store.all_notices()) == 3


@pytest.mark.parametrize("command", ["ack", "ack soon", "ack 1 2", "ack -1", "ack ²"])
def test_ack_wants_a_number_or_all(capsys, store, command):
    _seed(store)
    cli = SchedulerCLI()
    cli._notice_store = store

    _run(cli, command)

    assert "Usage: ack <number|all>" in capsys.readouterr().out
    assert len(store.open_notices()) == 3


def test_the_commands_use_the_schedulers_own_store(capsys, store, tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    scheduler = DailyScheduler(
        config_file=str(tmp_path / "none.yaml"),
        trading_cycle=Mock(),
        state_manager=SchedulerStateManager(),
        notice_store=store,
    )
    _seed(store)
    cli = SchedulerCLI(scheduler=scheduler)

    _run(cli, "ack 1")

    assert [n.seq for n in store.open_notices()] == [2, 3]
    assert cli.notice_store is store


# --- The routine report -----------------------------------------------------------------


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    """A trading cycle with its broker and state steps stubbed out."""
    monkeypatch.setattr(cycle_module, "GTT_AVAILABLE", False)
    monkeypatch.setattr(cycle_module, "is_market_hours", lambda: True)
    c = object.__new__(CostEfficientTradeCycle)
    c.fetch_broker_state = Mock(return_value={"positions": {}, "orders": {}})
    c.reconcile_state = Mock(return_value=[])
    c.check_and_repair_stops = Mock(return_value=[])
    c.check_position_alerts = Mock(return_value=[])
    c.check_gtt_triggers = Mock(return_value=[])
    c.calculate_stop_adjustments = Mock(return_value=[])
    c.trailing_stop_manager = Mock()
    c.local_state_manager = Mock(state={})
    c.generate_routine_report = Mock(return_value="report")
    c._get_report_file_path = lambda routine: str(tmp_path / f"{routine}.md")
    c.record_daily = Mock(return_value=[])
    c.last_report_path = None
    return c


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_the_report_lists_the_open_notices(cycle, store, routine):
    _seed(store)
    store.resolve("stop_breach:AAPL:2026-10-27")
    store.ack(3)
    cycle.notice_store = store

    report = getattr(cycle, routine)(raise_errors=True)

    assert report.startswith("report\n")
    assert "## Open Notices" in report
    assert f"- #1 CRITICAL {FAILED} (first seen {FIRST_SEEN})" in report
    assert (
        f"- #2 CRITICAL AAPL is at or below its stop, still open "
        f"(first seen {FIRST_SEEN}, resolved {FIRST_SEEN})" in report
    )
    assert "MSFT" not in report  # acknowledged
    assert cycle.last_report_path.endswith(f"{routine.split('_')[0]}.md")


@pytest.mark.parametrize("routine", ["morning_routine", "evening_routine"])
def test_the_report_has_no_notices_section_without_open_notices(cycle, store, routine):
    cycle.notice_store = store  # none raised

    assert getattr(cycle, routine)(raise_errors=True) == "report"

    _seed(store)
    store.ack("all")
    assert getattr(cycle, routine)(raise_errors=True) == "report"


def test_a_cycle_without_a_store_reports_as_before(cycle):
    assert cycle.notice_store is None  # the class default

    assert cycle.morning_routine(raise_errors=True) == "report"


def test_a_notice_store_that_fails_does_not_fail_the_routine(cycle):
    cycle.notice_store = Mock(open_notices=Mock(side_effect=OSError("disk")))

    assert cycle.morning_routine(raise_errors=True) == "report"


def test_a_notice_the_report_cannot_format_does_not_fail_the_routine(cycle):
    cycle.notice_store = Mock(open_notices=Mock(return_value=[object()]))

    assert cycle.morning_routine(raise_errors=True) == "report"


def test_the_scheduler_hands_its_store_to_the_trading_cycle(store, tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    cycle = Mock()
    scheduler = DailyScheduler(
        config_file=str(tmp_path / "none.yaml"),
        trading_cycle=cycle,
        state_manager=SchedulerStateManager(),
        notice_store=store,
    )
    assert cycle.notice_store is store

    replacement = Mock()
    scheduler.trading_cycle = replacement
    assert replacement.notice_store is store

    other = NoticeStore(tmp_path / "other.json")
    scheduler.notice_store = other
    assert replacement.notice_store is other and scheduler.notice_store is other


def test_a_manual_test_run_raises_no_notices(tmp_path, store, monkeypatch, capsys):
    """`test morning` calls the routine directly, not through the scheduler's run hooks."""
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    monkeypatch.setattr(daily_scheduler.time, "sleep", lambda seconds: None)
    cycle = Mock()
    cycle.morning_routine.side_effect = RuntimeError("broker down")
    scheduler = DailyScheduler(
        config_file=str(tmp_path / "none.yaml"),
        trading_cycle=cycle,
        state_manager=SchedulerStateManager(),
        notice_store=store,
    )

    _run(SchedulerCLI(scheduler=scheduler), "test morning")

    assert "Test failed" in capsys.readouterr().out
    assert store.all_notices() == []


# --- The agent's tool -------------------------------------------------------------------


@pytest.fixture
def agent_scheduler(store, tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler_state, "_scheduler_state_manager", None)
    config_file = tmp_path / "scheduler_config.yaml"
    config_file.write_text("enabled: true\n")
    scheduler = DailyScheduler(
        config_file=str(config_file),
        trading_cycle=Mock(),
        state_manager=SchedulerStateManager(),
        notice_store=store,
    )
    monkeypatch.setattr(scheduler_tools, "_get_scheduler", lambda: scheduler)
    return scheduler


def test_the_scheduler_status_tool_reports_the_open_notice_count(agent_scheduler, store):
    assert scheduler_tools.get_scheduler_status()["open_notices"] == 0

    _seed(store)
    store.ack(1)
    status = scheduler_tools.get_scheduler_status()

    assert status["status"] == "success" and status["enabled"] is True
    assert status["open_notices"] == 2


def test_the_status_tool_still_answers_when_the_notices_cannot_be_read(agent_scheduler):
    agent_scheduler._notice_store = Mock(open_notices=Mock(side_effect=OSError("disk")))

    status = scheduler_tools.get_scheduler_status()

    assert status["status"] == "success" and status["open_notices"] is None
