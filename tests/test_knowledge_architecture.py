"""Import-direction checks for knowledge module."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "modules" / "knowledge"

FORBIDDEN_DOMAIN = {
    "fastapi",
    "sqlalchemy",
    "app.api",
    "app.infra",
    "app.rag",
    "app.storage",
    "llama_index",
    "minio",
}

FORBIDDEN_APPLICATION = {
    "fastapi",
    "sqlalchemy",
    "app.rag",
    "app.storage",
    "app.infra",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _violations(directory: str, forbidden: set[str]) -> list[str]:
    bad: list[str] = []
    for path in (ROOT / directory).rglob("*.py"):
        for m in _imports(path):
            for f in forbidden:
                if m == f or m.startswith(f + "."):
                    bad.append(f"{path.relative_to(ROOT)} imports {m}")
    return bad


def test_knowledge_domain_isolation():
    assert _violations("domain", FORBIDDEN_DOMAIN) == []


def test_knowledge_application_isolation():
    assert _violations("application", FORBIDDEN_APPLICATION) == []
