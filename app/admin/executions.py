"""Read-only execution run aggregation from audits + tickets (not a full observability store)."""

from __future__ import annotations

from app.admin.demo_provider import demo_enabled, demo_execution_extra
from app.admin.meta import envelope, utc_now_iso
from app.core.platform import Platform
from app.core.security import AuthContext
from app.security.redaction import redact_for_api


def _from_audit(row: dict) -> dict:
    return {
        "run_id": f"aud_{row.get('audit_id')}",
        "kind": "chat_audit",
        "agent_id": row.get("agent_id"),
        "agent_name": None,
        "workflow": row.get("runtime_id") or row.get("workflow_engine"),
        "trigger": "chat",
        "status": "blocked" if row.get("blocked") else "succeeded",
        "started_at": row.get("created_at"),
        "ended_at": row.get("created_at"),
        "duration_ms": row.get("latency_ms"),
        "error_summary": row.get("block_reason"),
        "ticket_id": row.get("ticket_id"),
        "audit_id": row.get("audit_id"),
        "session_id": row.get("session_id"),
        "topic": row.get("topic"),
        "stages": None,
        "is_demo": False,
        "input_preview": (row.get("input_message") or "")[:80],
    }


def _from_ticket(row: dict) -> dict:
    return {
        "run_id": f"tkt_{row.get('id')}",
        "kind": "ticket_tool",
        "agent_id": row.get("agent_id"),
        "agent_name": None,
        "workflow": "tool_gate",
        "trigger": "ticket",
        "status": row.get("status"),
        "started_at": row.get("created_at"),
        "ended_at": row.get("updated_at") or row.get("executed_at"),
        "duration_ms": None,
        "error_summary": row.get("reason") if row.get("status") in {"rejected", "failed"} else None,
        "ticket_id": row.get("id"),
        "audit_id": None,
        "tool_name": row.get("tool_name"),
        "args_masked": row.get("args_masked"),
        "execution_result": row.get("execution_result"),
        "stages": None,
        "is_demo": False,
    }


def list_execution_runs(platform: Platform, auth: AuthContext, *, limit: int = 100) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    source = "postgres" if persistence_enabled() else "memory"
    if persistence_enabled():
        from app.infra.entity_repo import list_audits
        from app.infra.ticket_repo import list_tickets

        audits = list_audits(auth.tenant_id, limit=limit)
        tickets = list_tickets(auth.tenant_id)
    else:
        audits = [row for row in platform.audits if row.get("tenant_id") == auth.tenant_id][-limit:]
        tickets = [row for row in platform.tickets.values() if row.get("tenant_id") == auth.tenant_id]

    items = [_from_audit(redact_for_api(dict(a))) for a in audits]
    items.extend(_from_ticket(redact_for_api(dict(t))) for t in tickets)
    if demo_enabled():
        items.extend(demo_execution_extra())

    def sort_key(row: dict) -> str:
        return str(row.get("started_at") or row.get("ended_at") or row.get("run_id") or "")

    items.sort(key=sort_key, reverse=True)
    items = items[:limit]
    return envelope(
        {"items": items, "coverage": "audits+tickets(+demo_if_enabled)"},
        source=source,
        is_demo=False,
        note="非完整执行追踪；Stub 未执行请求不会记为成功 run",
        updated_at=utc_now_iso(),
    )


def get_execution_run(platform: Platform, auth: AuthContext, run_id: str) -> dict | None:
    listed = list_execution_runs(platform, auth, limit=500)
    for row in listed.get("items") or []:
        if row.get("run_id") == run_id:
            return envelope(row, source=listed["_meta"]["source"], is_demo=bool(row.get("is_demo")))
    return None
