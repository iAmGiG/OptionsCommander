"""
The agent framework has one user left, the natural-language parser (#331). Anything else
that imports it has brought back a path to the model that nobody reviewed.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _imports_framework(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "agent_framework" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == (
            "agent_framework"
        ):
            return True
    return False


def test_only_the_parser_imports_the_agent_framework():
    users = {
        str(path.relative_to(REPO))
        for path in (REPO / "src").rglob("*.py")
        if _imports_framework(path)
    }

    assert users == {"src/parsers/llm_parser.py"}
