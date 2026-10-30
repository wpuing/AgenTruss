"""Process-local Demo fixtures. Never used as silent fallback for live failures."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.core.config import get_settings

# In-memory demo drafts (workflow visualizer). Cleared on process restart.
_DEMO_WORKFLOW_DRAFTS: dict[str, dict] = {}


def demo_enabled() -> bool:
    settings = get_settings()
    if settings.is_production:
        return False
    return bool(getattr(settings, "admin_demo_mode", False))


def demo_workflow_catalog() -> list[dict]:
    return [
        {
            "workflow_id": "demo_refund_flow",
            "name": "演示：退款审批流",
            "status": "draft",
            "version": "0.1-demo",
            "engine": "mock",
            "description": "仅用于可视化编排演示，不会写入真实 LangGraph 图。",
            "nodes": [
                {"id": "start", "type": "start", "title": "开始", "x": 40, "y": 80},
                {"id": "guard", "type": "guard", "title": "输入护栏", "x": 180, "y": 80},
                {"id": "retrieve", "type": "retrieve", "title": "检索", "x": 320, "y": 80},
                {"id": "approve", "type": "approval", "title": "人工审批", "x": 460, "y": 80},
                {"id": "end", "type": "end", "title": "结束", "x": 600, "y": 80},
            ],
            "edges": [
                {"from": "start", "to": "guard"},
                {"from": "guard", "to": "retrieve"},
                {"from": "retrieve", "to": "approve"},
                {"from": "approve", "to": "end"},
            ],
        }
    ]


def get_demo_workflow(workflow_id: str) -> dict | None:
    if workflow_id in _DEMO_WORKFLOW_DRAFTS:
        return deepcopy(_DEMO_WORKFLOW_DRAFTS[workflow_id])
    for row in demo_workflow_catalog():
        if row["workflow_id"] == workflow_id:
            return deepcopy(row)
    return None


def save_demo_workflow_draft(workflow_id: str, body: dict) -> dict:
    """Persist a demo draft in-process only."""
    if not demo_enabled():
        raise PermissionError("admin_demo_mode_disabled")
    base = get_demo_workflow(workflow_id) or {
        "workflow_id": workflow_id,
        "name": body.get("name") or workflow_id,
        "status": "draft",
        "version": "demo",
        "engine": "mock",
        "nodes": [],
        "edges": [],
    }
    if "nodes" in body:
        base["nodes"] = body["nodes"]
    if "edges" in body:
        base["edges"] = body["edges"]
    if "name" in body:
        base["name"] = body["name"]
    base["status"] = "demo_draft"
    base["saved_at"] = datetime.now(timezone.utc).isoformat()
    base["demo_only"] = True
    _DEMO_WORKFLOW_DRAFTS[workflow_id] = deepcopy(base)
    return deepcopy(base)


def reset_demo_workflow(workflow_id: str) -> dict | None:
    _DEMO_WORKFLOW_DRAFTS.pop(workflow_id, None)
    return get_demo_workflow(workflow_id)


def demo_execution_extra() -> list[dict]:
    """Optional demo runs when ADMIN_DEMO_MODE=true — marked is_demo."""
    if not demo_enabled():
        return []
    return [
        {
            "run_id": "demo_run_sample",
            "agent_id": "demo_agent",
            "agent_name": "演示 Agent",
            "workflow": "demo_refund_flow",
            "trigger": "demo",
            "status": "succeeded",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": 120,
            "error_summary": None,
            "ticket_id": None,
            "audit_id": None,
            "stages": [
                {"id": "input_guard", "status": "ok"},
                {"id": "retrieve", "status": "ok"},
                {"id": "llm_generate", "status": "ok"},
            ],
            "is_demo": True,
        }
    ]
