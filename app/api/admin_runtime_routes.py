"""Chat / session / agent / tool registry HTTP routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.dispatch import dispatch_chat
from app.api.deps import need, platform_dep
from app.core.config import get_settings
from app.core.platform import Platform
from app.core.security import AuthContext
from app.memory.store import memory_key, read_turns_for_api
from app.rag import service as rag
from app.security.redaction import redact_for_api


def _agent_public_row(agent: dict) -> dict:
    """Admin list/detail shape: never leak secrets; mark chat eligibility."""
    safe = {
        k: v
        for k, v in agent.items()
        if k not in {"api_key", "model_api_key", "secret", "password"}
    }
    status = str(safe.get("status") or "")
    safe["chat_callable"] = status == "published"
    return safe

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatIn(BaseModel):
    message: str = Field(max_length=2000)
    session_id: str | None = None
    channel: str = "web"
    agent_id: str | None = None
    # Accepted for client compatibility but never trusted — identity comes from JWT only.
    user_id: str | None = None
    tenant_id: str | None = None


class AgentPatch(BaseModel):
    kb_id: str | None = None
    rag_enabled: bool | None = None
    status: str | None = None


@router.post("/api/chat")
def chat(
    body: ChatIn,
    auth: AuthContext = Depends(need("chat:debug:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    """Dialogue entry via unified runtime registry. Body user/tenant ids are ignored."""
    try:
        return dispatch_chat(platform, auth, body.model_dump())
    except HTTPException as exc:
        if exc.status_code == 429:
            platform.audits.append({
                "audit_id": "rate",
                "user_id": auth.user_id,
                "tenant_id": auth.tenant_id,
                "blocked": True,
                "block_reason": "rate_limited",
                "input_message": "",
            })
            logger.info("rate limited user=%s", auth.user_id)
        raise


@router.get("/api/admin/sessions")
def list_sessions(auth: AuthContext = Depends(need("chat:session:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_sessions as list_sessions_db

        return {"items": list_sessions_db(auth.tenant_id), "source": "postgres"}
    rows = [row for row in platform.session_index.values() if row["tenant_id"] == auth.tenant_id]
    return {"items": rows, "source": "memory"}


@router.get("/api/admin/sessions/{session_id}")
def session_detail(session_id: str, auth: AuthContext = Depends(need("chat:session:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_session_turns
        from app.infra.entity_repo import list_sessions as list_sessions_db

        sessions = {row["session_id"]: row for row in list_sessions_db(auth.tenant_id)}
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="session not found")
        turns = list_session_turns(auth.tenant_id, session_id)
        return redact_for_api({"session_id": session_id, "turns": turns, "source": "postgres"})
    key = memory_key(auth.tenant_id, session_id)
    row = platform.memories.get(key)
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    return redact_for_api({
        "session_id": session_id,
        "turns": read_turns_for_api(platform, auth.tenant_id, session_id),
        "source": "memory",
    })


@router.get("/api/admin/agents")
def list_agents(
    auth: AuthContext = Depends(need("agent:instance:view")),
    platform: Platform = Depends(platform_dep),
    status: str | None = Query(default=None, description="optional status filter"),
    for_chat: bool = Query(
        default=False,
        description="when true, only return Agents the SPA may invoke via /api/chat",
    ),
) -> dict:
    """List tenant Agents. SPA uses chat_callable / for_chat to avoid disabled defaults."""
    rows = [row for row in platform.agents.values() if row["tenant_id"] == auth.tenant_id]
    if for_chat:
        rows = [row for row in rows if row.get("status") == "published"]
    elif status:
        allowed = {"published", "disabled", "draft"}
        if status not in allowed:
            raise HTTPException(status_code=400, detail=f"invalid status; allowed={sorted(allowed)}")
        rows = [row for row in rows if row.get("status") == status]
    items = [_agent_public_row(row) for row in rows]
    return {
        "items": items,
        "counts": {
            "total": len(items),
            "published": sum(1 for r in items if r.get("status") == "published"),
            "disabled": sum(1 for r in items if r.get("status") == "disabled"),
            "chat_callable": sum(1 for r in items if r.get("chat_callable")),
        },
    }


@router.patch("/api/admin/agents/{agent_id}")
def patch_agent(
    agent_id: str,
    body: AgentPatch,
    auth: AuthContext = Depends(need("agent:instance:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    agent = platform.agents.get(agent_id)
    if not agent or agent["tenant_id"] != auth.tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    # Snapshot so PG upsert failure cannot leave a dirty in-memory agent.
    before = {
        "kb_id": (agent.get("kb_binding") or {}).get("kb_id"),
        "rag_enabled": (agent.get("kb_binding") or {}).get("rag_enabled"),
        "status": agent.get("status"),
    }
    if body.kb_id is not None:
        rag.get_kb(platform, auth.tenant_id, body.kb_id)
        agent.setdefault("kb_binding", {})["kb_id"] = body.kb_id
    if body.rag_enabled is not None:
        agent.setdefault("kb_binding", {})["rag_enabled"] = body.rag_enabled
    if body.status is not None:
        allowed = {"published", "disabled", "draft"}
        if body.status not in allowed:
            raise HTTPException(status_code=400, detail=f"invalid status; allowed={sorted(allowed)}")
        agent["status"] = body.status
    try:
        from app.infra.entity_repo import upsert_agent

        upsert_agent(agent)
    except Exception:  # noqa: BLE001
        from app.infra.ticket_repo import persistence_enabled

        if persistence_enabled():
            agent.setdefault("kb_binding", {})["kb_id"] = before["kb_id"]
            agent.setdefault("kb_binding", {})["rag_enabled"] = before["rag_enabled"]
            agent["status"] = before["status"]
            raise
    return _agent_public_row(agent)


@router.get("/api/admin/agents/{agent_id}")
def admin_agent_detail(
    agent_id: str,
    auth: AuthContext = Depends(need("agent:instance:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    agent = platform.agents.get(agent_id)
    if not agent or agent.get("tenant_id") != auth.tenant_id:
        raise HTTPException(status_code=404, detail="agent not found")
    safe = _agent_public_row(agent)
    from app.admin.meta import envelope

    settings = get_settings()
    return envelope(
        {
            **safe,
            "execution_mode": settings.agentscope_model_mode,
            "prompt_preview": (safe.get("system_prompt") or safe.get("prompt") or "")[:400],
        },
        source="platform",
        is_demo=False,
    )


@router.get("/api/admin/tools")
def list_tools(auth: AuthContext = Depends(need("agent:tool:view")), platform: Platform = Depends(platform_dep)) -> dict:
    """Public tool registry metadata (no handlers / no secrets)."""
    del platform, auth
    from app.tools.registry import public_tool_catalog

    return {"items": public_tool_catalog()}
