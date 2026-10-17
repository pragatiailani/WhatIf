"""Nodes (and everything else outside the repo layer) must not import app.db."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
ALLOWED = {APP / "repo", APP / "db"}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_only_repo_imports_db():
    offenders = [
        f"{path.relative_to(APP.parent)}: {name}"
        for path in APP.rglob("*.py")
        if not any(path.is_relative_to(a) for a in ALLOWED)
        for name in _imports(path)
        if name == "app.db" or name.startswith("app.db.")
    ]
    assert not offenders, "import from app.repo instead:\n" + "\n".join(offenders)
