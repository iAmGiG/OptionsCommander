"""
Every import of project code in src/, scripts/ and main.py resolves, lazy ones included (#331).

A stale import inside a function only fails when that line runs, which for a routine means
during a live run, and the import test that runs main.py doesn't reach it. This reads the
source instead of running it, so it covers every branch.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
# main.py puts src/ on the path for the CLI, so these also name project packages
SRC_PACKAGES = {path.name for path in SRC.iterdir() if (path / "__init__.py").exists()}


def _location(module):
    """The file for a dotted project module, or None."""
    parts = module.split(".")
    base = REPO if parts[0] in ("src", "config_defaults") else SRC
    path = base.joinpath(*parts)
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _is_project(module):
    top = module.split(".")[0]
    return top in ("src", "config_defaults") or top in SRC_PACKAGES


def _module_level(statements):
    """A module's statements that run on import: into if and try blocks, not into functions."""
    for node in statements:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            yield from _module_level(getattr(node, field, []))
        for handler in getattr(node, "handlers", []):
            yield from _module_level(handler.body)


def _defined_names(path):
    """The names importing a module gives; None if it uses a star, which can't be told."""
    names = set()
    for node in _module_level(ast.parse(path.read_text()).body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name.split(".")[0])
        else:
            names.update(
                target.id
                for target in ast.walk(node)
                if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)
            )
    return names


def _dotted_module(path, node):
    """The absolute module an ImportFrom names, from the file it's in."""
    if not node.level:
        return node.module or ""
    parts = list(path.relative_to(REPO).with_suffix("").parts)[:-1]
    parts = parts[: len(parts) - (node.level - 1)]
    return ".".join(parts + ([node.module] if node.module else []))


def _from_import_problems(path, node):
    """What's wrong with one ``from x import y`` of project code."""
    module = _dotted_module(path, node)
    if not _is_project(module):
        return []
    target = _location(module)
    if target is None:
        return [f"{path}:{node.lineno}: no module {module}"]
    names = _defined_names(target)
    if names is None:
        return []
    return [
        f"{path}:{node.lineno}: {module} has no {alias.name}"
        for alias in node.names
        if alias.name != "*"
        and alias.name not in names
        and _location(f"{module}.{alias.name}") is None
    ]


def _problems(path):
    problems = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            problems += [
                f"{path}:{node.lineno}: no module {alias.name}"
                for alias in node.names
                if _is_project(alias.name) and _location(alias.name) is None
            ]
        elif isinstance(node, ast.ImportFrom):
            problems += _from_import_problems(path, node)
    return problems


FILES = [REPO / "main.py", *sorted(SRC.rglob("*.py")), *sorted((REPO / "scripts").rglob("*.py"))]


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_project_imports_resolve(path):
    assert _problems(path) == []


def test_a_function_local_name_is_not_importable(tmp_path):
    module = tmp_path / "module.py"
    module.write_text("KEPT = 1\ndef f():\n    local = 1\n")

    assert _defined_names(module) == {"KEPT", "f"}


def test_the_check_notices_a_stale_import(tmp_path):
    stale = tmp_path / "probe.py"
    stale.write_text(
        "def f():\n"
        "    from src.trading.orders.no_such_module import Gone\n"
        "    from src.trading.orders import NoSuchName\n"
    )

    found = _problems(stale)

    assert "no module src.trading.orders.no_such_module" in found[0]
    assert "src.trading.orders has no NoSuchName" in found[1]


@pytest.mark.parametrize(
    "gone",
    [
        "src.autogen_agents",
        "src.market_data.market_context_tool",
        "src.market_data.vxx_volatility_tool",
        "src.utils.agent_utils",
        "src.cli.tools.account_display_tools",
        "src.cli.tools.portfolio_override_tools",
        "src.cli.tools.user_alert_tools",
        "src.cli.tools.voter_tools",
        "src.cli.commands.trailing_stop_commands",
        "src.trading.portfolio_override",
        "src.trading.scheduling.trading_pipeline",
        "src.trading.orders.order_manager",
        "src.cli.utils.decision_formatter",
    ],
)
def test_the_retired_modules_stay_gone(gone):
    """#331: nothing should bring these back by accident."""
    assert _location(gone) is None
