"""Architecture / honesty gates for tool capability & recovery authority."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "app" / "tools"
HTTP = ROOT / "app" / "api" / "http.py"
RUNTIME_ROUTES = ROOT / "app" / "api" / "admin_runtime_routes.py"


def _fn_body_names(path: Path, fn_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            names: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name):
                    names.add(child.id)
                elif isinstance(child, ast.Attribute):
                    names.add(child.attr)
            return names
    raise AssertionError(f"function {fn_name} not found in {path}")


def test_register_decorator_calls_validate_capability_meta() -> None:
    """Registration must not skip the honesty gate."""
    text = (TOOLS / "registry.py").read_text(encoding="utf-8")
    assert "def register(" in text
    # Nested decorator must invoke validate_capability_meta before storing.
    assert "validate_capability_meta(meta)" in text


def test_recovery_decisions_ignore_capability_tier_attribute() -> None:
    """LOOKUP_FIRST / SAFE_RETRY must not branch on capability_tier labels."""
    path = TOOLS / "reliability.py"
    for fn in ("decide_after_uncertain", "decide_after_timeout"):
        names = _fn_body_names(path, fn)
        assert "capability_tier" not in names, f"{fn} must not read capability_tier"


def test_admin_tools_endpoint_is_catalog_only() -> None:
    """Admin tools route must expose public catalog, not execute handlers."""
    # Route lives in admin_runtime_routes after HTTP domain split; http.py must mount it.
    http_text = HTTP.read_text(encoding="utf-8")
    assert "admin_runtime_router" in http_text or "admin_runtime_routes" in http_text
    text = RUNTIME_ROUTES.read_text(encoding="utf-8")
    assert '@router.get("/api/admin/tools")' in text
    assert "public_tool_catalog" in text
    # No admin HTTP execute surface for registered tools.
    assert '@router.post("/api/admin/tools' not in text
    assert "handler(" not in text.split('@router.get("/api/admin/tools")', 1)[1][:800]


def test_demo_tools_remain_local_demo_without_lookup() -> None:
    """Built-in Demo tools must stay local_demo and non-lookup."""
    from app.tools.registry import get_tool

    for name in ("query_order", "refund_order"):
        meta = get_tool(name)
        assert meta is not None
        assert meta.get("capability_tier") == "local_demo"
        assert meta.get("supports_result_lookup") is False
