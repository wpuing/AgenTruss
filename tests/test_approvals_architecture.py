"""Lightweight import-direction checks for approvals module."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "modules" / "approvals"

FORBIDDEN_DOMAIN = {
    "fastapi",
    "sqlalchemy",
    "app.api",
    "app.infra",
    "app.tools",
    "langgraph",
    "llama_index",
}

FORBIDDEN_APPLICATION = {
    "fastapi",
    "sqlalchemy",
    "app.infra.models",
    "app.infra.ticket_repo",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0] if alias.name.startswith("fastapi") else alias.name)
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _violations(directory: str, forbidden: set[str]) -> list[str]:
    bad: list[str] = []
    base = ROOT / directory
    for path in base.rglob("*.py"):
        mods = _imports(path)
        for f in forbidden:
            for m in mods:
                if m == f or m.startswith(f + "."):
                    bad.append(f"{path.relative_to(ROOT)} imports {m}")
    return bad


def test_domain_has_no_framework_imports():
    assert _violations("domain", FORBIDDEN_DOMAIN) == []


def test_application_has_no_fastapi_or_orm_imports():
    bad = _violations("application", FORBIDDEN_APPLICATION | {"app.tools", "app.api"})
    assert bad == [], bad


def test_api_routes_do_not_import_sqlalchemy():
    bad = _violations("api", {"sqlalchemy", "app.infra.models"})
    assert bad == []


def test_application_execute_no_longer_imports_legacy_tickets():
    """Regression: execute orchestration must not call app.tools.tickets from services."""
    services = ROOT / "application" / "services.py"
    text = services.read_text(encoding="utf-8")
    assert "app.tools.tickets" not in text
    assert "legacy_execute" not in text


def test_application_does_not_import_tools_reliability():
    """Regression (cbd4ff8 root cause): Application must use UncertainOutcomePolicyPort only."""
    bad = _violations("application", {"app.tools"})
    assert bad == [], bad


def test_outcome_policy_lives_in_infrastructure():
    adapters = (ROOT / "infrastructure" / "adapters.py").read_text(encoding="utf-8")
    assert "class ReliabilityOutcomePolicy" in adapters
    ports = (ROOT / "application" / "ports.py").read_text(encoding="utf-8")
    assert "UncertainOutcomePolicyPort" in ports
    # Infrastructure may import tools; Application must not (AST-checked above).
    assert "app.tools" in adapters or "reliability" in adapters
