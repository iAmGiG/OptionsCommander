"""
The cron entry (`main.py --run-due`) imports everything it needs, creates no files outside
the state and cache directories, and does not load the interactive CLI: an import error
there would stop every scheduled routine, and the CLI's modules cost seconds on each of the
288 ticks a day. The interactive path still finds the CLI.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

RUN_DUE_SCRIPT = """
import runpy, sys
sys.argv = ["main.py", "--run-due"]
try:
    runpy.run_path({main!r}, run_name="__main__")
except SystemExit as stop:
    code = stop.code
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in ("cli", "agent_framework", "openai")
    or name in ("core.factory", "src.cli") or name.startswith("src.cli.")
    or name == "src.autogen_agents" or name.startswith("src.autogen_agents.")
)
print("EXIT", code)
print("LOADED", loaded)
"""


def _env(tmp_path):
    env = dict(os.environ)
    env.pop("RUN_PAPER_ORDER_TESTS", None)
    env.update(
        AUTOTRADER_STATE_DIR=str(tmp_path / "state"),
        AUTOTRADER_CACHE_DIR=str(tmp_path / "cache"),
        PYTHONPATH=str(REPO),
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


def _workdir(tmp_path):
    work = tmp_path / "cwd"
    work.mkdir()
    # The tracked config (scheduler disabled), so the check finds nothing due
    (work / "config_defaults").symlink_to(REPO / "config_defaults")
    return work


def test_run_due_loads_no_cli_and_creates_nothing_else(tmp_path):
    work = _workdir(tmp_path)
    result = subprocess.run(
        # -s: the cron line runs with the user site disabled
        [sys.executable, "-s", "-c", RUN_DUE_SCRIPT.format(main=str(REPO / "main.py"))],
        cwd=work,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    lines = result.stdout.splitlines()
    assert "EXIT 0" in lines
    assert "LOADED []" in lines  # no src.cli, cli, core.factory or agent_framework
    state, cache = tmp_path / "state", tmp_path / "cache"
    outside = [p for p in tmp_path.rglob("*") if p not in (state, cache) and state not in p.parents]
    # Only the working directory and its config link; no cache or file beside them
    assert sorted(p for p in outside if cache not in p.parents) == [work, work / "config_defaults"]


def test_interactive_path_still_finds_the_cli(tmp_path):
    work = _workdir(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import main; print('BEFORE', main.CLI_AVAILABLE); "
            "print('LOADS', main._load_cli()); print('AFTER', main.CLI_AVAILABLE)",
        ],
        cwd=work,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    lines = result.stdout.splitlines()
    assert "BEFORE None" in lines  # importing main loads nothing of the CLI
    assert "LOADS True" in lines
    assert "AFTER True" in lines
