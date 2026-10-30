"""Workflow catalog: real LangGraph stage rail + optional demo visualizer drafts."""

from __future__ import annotations

from app.admin.demo_provider import (
    demo_enabled,
    demo_workflow_catalog,
    get_demo_workflow,
    reset_demo_workflow,
    save_demo_workflow_draft,
)
from app.admin.meta import envelope, utc_now_iso
from app.core.config import get_settings
from app.core.platform import Platform
from app.core.security import AuthContext

LANGGRAPH_STAGES = [
    {"id": "prepare", "title": "准备", "desc": "装载 Agent / 会话"},
    {"id": "input_guard", "title": "输入护栏", "desc": "清洗、注入、话题、PII"},
    {"id": "retrieve", "title": "检索", "desc": "RAG / Mock 向量"},
    {"id": "llm_generate", "title": "生成", "desc": "LLM 或 Stub"},
    {"id": "tool_gate", "title": "工具门禁", "desc": "白名单 + 高风险工单"},
    {"id": "finalize", "title": "结束", "desc": "审计与响应"},
]


def list_workflows(platform: Platform, auth: AuthContext) -> dict:
    settings = get_settings()
    items = [
        {
            "workflow_id": "langgraph_chat",
            "name": "权威对话工作流",
            "status": "active",
            "version": "runtime",
            "engine": settings.workflow_engine,
            "description": "与 app.agents.graph 节点一致；非拖拽设计器产物",
            "stages": LANGGRAPH_STAGES,
            "is_demo": False,
        }
    ]
    if demo_enabled() or getattr(settings, "workflow_visualizer_mode", "mock") == "mock":
        for row in demo_workflow_catalog():
            draft = get_demo_workflow(row["workflow_id"]) or row
            items.append({**draft, "is_demo": True})
    # Tenant isolation — never leak other tenants' agent catalog via workflows API.
    agents = [
        {
            "agent_id": a.get("agent_id"),
            "name": a.get("name"),
            "status": a.get("status"),
            # SPA debug gate trusts chat_callable; do not infer from status alone.
            "chat_callable": a.get("status") == "published",
        }
        for a in platform.agents.values()
        if a.get("tenant_id") == auth.tenant_id
    ]
    return envelope(
        {
            "items": items,
            "agents": agents,
            "visualizer_mode": getattr(settings, "workflow_visualizer_mode", "mock"),
            "admin_demo_mode": demo_enabled(),
        },
        source="langgraph_nodes+demo_optional",
        is_demo=False,
        updated_at=utc_now_iso(),
    )


def get_workflow(workflow_id: str) -> dict | None:
    settings = get_settings()
    if workflow_id == "langgraph_chat":
        return envelope(
            {
                "workflow_id": workflow_id,
                "name": "权威对话工作流",
                "engine": settings.workflow_engine,
                "stages": LANGGRAPH_STAGES,
                "is_demo": False,
                "editable": False,
            },
            source="code",
            is_demo=False,
        )
    demo = get_demo_workflow(workflow_id)
    if demo:
        return envelope({**demo, "editable": demo_enabled()}, source="demo", is_demo=True)
    return None


def save_workflow_demo(workflow_id: str, body: dict) -> dict:
    saved = save_demo_workflow_draft(workflow_id, body)
    return envelope(saved, source="demo", is_demo=True, note="演示草稿，未写入真实 LangGraph")


def reset_workflow_demo(workflow_id: str) -> dict | None:
    row = reset_demo_workflow(workflow_id)
    if not row:
        return None
    return envelope(row, source="demo", is_demo=True)
