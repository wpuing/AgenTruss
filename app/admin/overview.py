"""Dashboard overview aggregation (real metrics + optional demo annotations)."""

from __future__ import annotations

from app.admin.demo_provider import demo_enabled
from app.admin.meta import envelope, utc_now_iso
from app.core.config import get_settings
from app.core.platform import Platform
from app.core.security import AuthContext


def get_overview(platform: Platform, auth: AuthContext) -> dict:
    """Build overview from authoritative sources; never invent healthy rates."""
    from app.infra.ticket_repo import count_pending, persistence_enabled

    settings = get_settings()
    source = "postgres" if persistence_enabled() else "memory"
    if persistence_enabled():
        from app.infra.entity_repo import dashboard_counts, list_audits
        from app.infra.ticket_repo import list_tickets

        counts = dashboard_counts(auth.tenant_id)
        audits = list_audits(auth.tenant_id, limit=200)
        tickets = list_tickets(auth.tenant_id)
        chat_count = counts.get("chat_count", 0)
        kb_count = counts.get("kb_count", 0)
        doc_count = counts.get("doc_count", 0)
        pending = count_pending(auth.tenant_id)
    else:
        audits = [row for row in platform.audits if row.get("tenant_id") == auth.tenant_id]
        tickets = [row for row in platform.tickets.values() if row.get("tenant_id") == auth.tenant_id]
        chat_count = len(audits)
        kb_count = sum(1 for row in platform.kbs.values() if row["tenant_id"] == auth.tenant_id)
        doc_count = sum(1 for row in platform.documents.values() if row["tenant_id"] == auth.tenant_id)
        pending = sum(1 for row in tickets if row.get("status") == "pending")

    agents = [a for a in platform.agents.values() if a.get("tenant_id") == auth.tenant_id]
    published = sum(1 for a in agents if a.get("status") == "published")
    disabled = sum(1 for a in agents if a.get("status") == "disabled")
    blocked_audits = [a for a in audits if a.get("blocked") or a.get("block_reason")]
    recent_audits = list(reversed(audits))[:8]
    recent_tickets = sorted(
        tickets,
        key=lambda t: t.get("updated_at") or t.get("created_at") or "",
        reverse=True,
    )[:8]
    executed_ok = sum(1 for t in tickets if t.get("executed") and t.get("status") in {"executed", "approved"})
    executed_fail = sum(1 for t in tickets if t.get("status") == "failed")

    payload = {
        "agents": {
            "total": len(agents),
            "published": published,
            "disabled": disabled,
            "other": max(0, len(agents) - published - disabled),
            "chat_callable": published,
            "running": None,
            "abnormal": None,
            "note": "仅 published 可被 /api/chat 调用；running/abnormal 需运行时遥测，当前未接入",
        },
        "workflows": {
            "engine": settings.workflow_engine,
            "visualizer_mode": getattr(settings, "workflow_visualizer_mode", "mock"),
            "count": None,
            "note": "真实图数量未持久化为独立实体；见工作流页阶段轨/演示草稿",
        },
        "knowledge": {"kb_count": kb_count, "doc_count": doc_count},
        "chat_count": chat_count,
        "guardrail_hits": len(blocked_audits),
        "tickets": {
            "pending": pending,
            "executed_ok": executed_ok,
            "failed": executed_fail,
            "success_rate": None,
            "note": "无成功率监控时不展示虚构百分比",
        },
        "recent_audits": [
            {
                "audit_id": a.get("audit_id"),
                "topic": a.get("topic"),
                "blocked": bool(a.get("blocked")),
                "block_reason": a.get("block_reason"),
                "session_id": a.get("session_id"),
            }
            for a in recent_audits
        ],
        "recent_tickets": [
            {
                "id": t.get("id"),
                "tool_name": t.get("tool_name"),
                "status": t.get("status"),
                "executed": bool(t.get("executed")),
            }
            for t in recent_tickets
        ],
        "admin_demo_mode": demo_enabled(),
        "agent_execution_mode": settings.agentscope_model_mode,
    }
    return envelope(
        payload,
        source=source,
        is_demo=False,
        note="指标来自审计/工单/实体仓储；不含伪造健康率",
        updated_at=utc_now_iso(),
    )
