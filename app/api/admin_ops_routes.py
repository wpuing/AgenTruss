"""Admin ops reads: dashboard, overview, executions, workflows, system, audit detail, trace."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import need, need_any, platform_dep
from app.api.readiness import collect_ready_checks
from app.core.config import get_settings
from app.core.platform import THEME_CATALOG, Platform
from app.core.security import AuthContext
from app.memory.store import read_turns_for_api
from app.security.redaction import redact_for_api

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/dashboard")
def dashboard(auth: AuthContext = Depends(need("dashboard:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.infra.ticket_repo import count_pending, persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import dashboard_counts, list_audits

        counts = dashboard_counts(auth.tenant_id)
        audits = list_audits(auth.tenant_id, limit=500)
        return {
            "ready": "ready",
            "chat_count": counts.get("chat_count", 0),
            "guardrail_hits": sum(1 for row in audits if row.get("blocked") or row.get("block_reason")),
            "pending_tickets": count_pending(auth.tenant_id),
            "kb_count": counts.get("kb_count", 0),
            "doc_count": counts.get("doc_count", 0),
            "source": "postgres",
        }
    audits = [row for row in platform.audits if row.get("tenant_id") == auth.tenant_id]
    tickets = [
        row
        for row in platform.tickets.values()
        if row["tenant_id"] == auth.tenant_id and row["status"] == "pending"
    ]
    return {
        "ready": "ready",
        "chat_count": len(audits),
        "guardrail_hits": sum(1 for row in audits if row.get("blocked") or row.get("block_reason")),
        "pending_tickets": len(tickets),
        "kb_count": sum(1 for row in platform.kbs.values() if row["tenant_id"] == auth.tenant_id),
        "doc_count": sum(1 for row in platform.documents.values() if row["tenant_id"] == auth.tenant_id),
        "source": "memory",
    }


@router.get("/api/admin/overview")
def admin_overview(auth: AuthContext = Depends(need("dashboard:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.admin.overview import get_overview

    return get_overview(platform, auth)


@router.get("/api/admin/executions")
def admin_executions(
    auth: AuthContext = Depends(need("audit:exec:view")),
    platform: Platform = Depends(platform_dep),
    limit: int = 100,
) -> dict:
    from app.admin.executions import list_execution_runs

    return list_execution_runs(platform, auth, limit=min(max(limit, 1), 500))


@router.get("/api/admin/executions/{run_id}")
def admin_execution_detail(
    run_id: str,
    auth: AuthContext = Depends(need("audit:exec:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.executions import get_execution_run

    row = get_execution_run(platform, auth, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="execution not found")
    return row


@router.get("/api/admin/workflows")
def admin_workflows(auth: AuthContext = Depends(need("agent:graph:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.admin.workflows import list_workflows

    return list_workflows(platform, auth)


@router.get("/api/admin/workflows/{workflow_id}")
def admin_workflow_detail(
    workflow_id: str,
    auth: AuthContext = Depends(need("agent:graph:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    del auth, platform
    from app.admin.workflows import get_workflow

    row = get_workflow(workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


class WorkflowDemoIn(BaseModel):
    name: str | None = None
    nodes: list[dict] | None = None
    edges: list[dict] | None = None


@router.put("/api/admin/workflows/{workflow_id}/demo-draft")
def admin_workflow_demo_save(
    workflow_id: str,
    body: WorkflowDemoIn,
    auth: AuthContext = Depends(need("agent:graph:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    del auth, platform
    from app.admin.demo_provider import demo_enabled
    from app.admin.workflows import save_workflow_demo

    if not demo_enabled():
        raise HTTPException(status_code=403, detail="admin_demo_mode_disabled")
    if workflow_id == "langgraph_chat":
        raise HTTPException(status_code=400, detail="real langgraph graph is not editable via demo API")
    return save_workflow_demo(workflow_id, body.model_dump(exclude_none=True))


@router.post("/api/admin/workflows/{workflow_id}/demo-reset")
def admin_workflow_demo_reset(
    workflow_id: str,
    auth: AuthContext = Depends(need("agent:graph:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    del auth, platform
    from app.admin.demo_provider import demo_enabled
    from app.admin.workflows import reset_workflow_demo

    if not demo_enabled():
        raise HTTPException(status_code=403, detail="admin_demo_mode_disabled")
    row = reset_workflow_demo(workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="workflow not found")
    return row


@router.get("/api/admin/system/basic")
def system_basic(auth: AuthContext = Depends(need("sys:basic:view")), platform: Platform = Depends(platform_dep)) -> dict:
    """Readonly app + rate-limit snapshot for Admin SPA (no online write)."""
    del auth
    settings = get_settings()
    return {
        "app.name": platform.settings_doc["app.name"],
        "openapi.enabled": True,
        "model.api_key": "********",
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "login_rate_limit_per_minute": settings.login_rate_limit_per_minute,
        "rate_limit_writable": False,
        "note": "限流字段只读；在线改阈值未开放，改 .env 后重启",
    }


@router.get("/api/admin/system/status")
def system_status(auth: AuthContext = Depends(need("sys:basic:view")), platform: Platform = Depends(platform_dep)) -> dict:
    del auth, platform
    settings = get_settings()
    ok, checks = collect_ready_checks(include_minio=True)
    vector_backend = settings.effective_vector_store_backend()
    return {
        "app_env": settings.app_env,
        "storage_backend": settings.storage_backend,
        "embedding_provider": getattr(settings, "embedding_provider", None)
        or getattr(settings, "embedding_mode", "mock"),
        "vector_store_backend": vector_backend,
        "workflow_engine": settings.workflow_engine,
        "agent_runtime": settings.agent_runtime,
        "agentscope_enabled": settings.agentscope_enabled,
        "health": {"status": "ok" if ok else "degraded"},
        "ready": {"status": "ready" if ok else "not_ready", "checks": checks},
        "ready_ok": ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Mock Embedding / Mock Vector Store are for pipeline verification, not production semantic RAG.",
            "Production requires postgres + redis + strong JWT/TICKET secrets.",
        ],
    }


@router.get("/api/admin/audit-logs")
def audit_logs(auth: AuthContext = Depends(need("audit:log:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.admin.audit_reads import list_tenant_audits

    rows, source = list_tenant_audits(platform, auth.tenant_id)
    return {"items": rows, "source": source}


@router.get("/api/admin/audit-logs/{audit_id}")
def audit_log_detail(
    audit_id: str,
    auth: AuthContext = Depends(need("audit:log:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.audit_reads import get_tenant_audit

    row = get_tenant_audit(platform, auth.tenant_id, audit_id)
    if not row:
        raise HTTPException(status_code=404, detail="audit not found")
    return row


@router.get("/api/admin/kb/jobs/{job_id}")
def kb_job_detail(
    job_id: str,
    auth: AuthContext = Depends(need("kb:job:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    job = platform.jobs.get(job_id)
    if not job:
        for row in platform.jobs.values():
            if isinstance(row, dict) and row.get("id") == job_id:
                job = row
                break
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    tenant = job.get("tenant_id")
    if tenant and tenant != auth.tenant_id:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/api/admin/trace/{session_id}")
def session_trace(
    session_id: str,
    auth: AuthContext = Depends(need_any("audit:trace:view", "chat:session:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.audit_reads import audits_for_session
    from app.infra.ticket_repo import persistence_enabled

    turns: list = []
    if persistence_enabled():
        from app.infra.entity_repo import list_session_turns

        turns = list_session_turns(auth.tenant_id, session_id)
    else:
        turns = read_turns_for_api(platform, auth.tenant_id, session_id)
    audits = audits_for_session(platform, auth.tenant_id, session_id)
    return redact_for_api(
        {
            "session_id": session_id,
            "turns": turns,
            "audits": audits,
            "source": "postgres" if persistence_enabled() else "memory",
        }
    )


class ThemeIn(BaseModel):
    theme_id: str = Field(min_length=2, max_length=32)


@router.get("/api/admin/system/themes")
def list_themes(auth: AuthContext = Depends(need("sys:theme:view")), platform: Platform = Depends(platform_dep)) -> dict:
    del auth
    current = platform.settings_doc.get("ui.theme") or "ember"
    return {"current": current, "items": THEME_CATALOG}


@router.put("/api/admin/system/theme")
def update_theme(
    body: ThemeIn,
    auth: AuthContext = Depends(need("sys:theme:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    del auth
    allowed = {row["id"] for row in THEME_CATALOG}
    if body.theme_id not in allowed:
        raise HTTPException(status_code=400, detail="unknown theme")
    platform.settings_doc["ui.theme"] = body.theme_id
    logger.info("ui.theme set to %s", body.theme_id)
    return {"current": body.theme_id}
