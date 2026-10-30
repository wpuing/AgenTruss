"""Dialogue dispatch: route /api/chat through the unified AgentRuntime registry."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.platform import Platform, new_id
from app.core.security import AuthContext
from app.runtime.exceptions import RuntimeConfigError
from app.runtime.models import RunRequest, RunResult, RunStatus, RuntimeId, SubjectContext
from app.runtime.registry import resolve_runtime
from app.security.redaction import sanitize_exception

logger = logging.getLogger(__name__)


def dispatch_chat(platform: Platform, auth: AuthContext, body: dict) -> dict:
    """Build a trusted RunRequest and execute via the configured runtime.

    Identity and tenant always come from ``auth`` (JWT), never from the body.
    Runtime selection comes from settings (authorized config), not client override.
    """
    settings = get_settings()
    try:
        runtime = resolve_runtime(settings)
    except RuntimeConfigError as exc:
        raise HTTPException(status_code=503, detail=sanitize_exception(exc)) from exc

    session_id = body.get("session_id") or new_id("ses")
    conversation_id = body.get("conversation_id") or session_id
    trace_id = new_id("trc")
    run_id = new_id("run")

    request = RunRequest(
        runtime_id=runtime.runtime_id,
        message=body.get("message") or "",
        subject=SubjectContext(
            user_id=auth.user_id,
            tenant_id=auth.tenant_id,
            roles=list(auth.roles),
            perms=sorted(auth.perms),
        ),
        session_id=session_id,
        conversation_id=conversation_id,
        agent_id=body.get("agent_id"),
        channel=body.get("channel") or "web",
        trace_id=trace_id,
        run_id=run_id,
        metadata={k: v for k, v in (body.get("metadata") or {}).items() if k.startswith("inject_") or k == "force_member_tool"},
    )

    result = runtime.execute(platform, request)
    logger.info(
        "chat dispatched runtime=%s status=%s trace=%s run=%s tenant=%s",
        result.runtime_id.value,
        result.status.value,
        result.trace_id,
        result.run_id,
        auth.tenant_id,
    )
    return run_result_to_chat(result)


def run_result_to_chat(result: RunResult) -> dict:
    """Map RunResult to the existing chat API response shape."""
    block_reason = None
    if result.structured:
        block_reason = result.structured.get("block_reason")
    if not block_reason and result.status in {
        RunStatus.BLOCKED,
        RunStatus.APPROVAL_REQUIRED,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.UNAVAILABLE,
    }:
        block_reason = result.error_code

    structured = result.structured or {}
    llm_called = bool(structured.get("llm_called"))
    llm_mode = structured.get("llm_mode") or structured.get("model_mode") or ("live" if llm_called else "stub")
    payload = {
        "reply": result.reply or _fallback_reply(result),
        "session_id": result.session_id,
        "audit_id": result.audit_id,
        "sources": list(result.sources or []),
        "need_human": bool(result.need_human),
        "ticket_id": result.ticket_id,
        "guardrail_hit": bool(result.guardrail_hit),
        "block_reason": block_reason,
        "runtime_id": result.runtime_id.value,
        "trace_id": result.trace_id,
        "run_id": result.run_id,
        "status": result.status.value,
        "conversation_id": result.session_id,
        "llm_called": llm_called,
        "llm_mode": llm_mode,
    }
    if result.status == RunStatus.UNAVAILABLE:
        payload["reply"] = result.error_message or payload["reply"]
    if result.status == RunStatus.TIMEOUT:
        payload["reply"] = result.error_message or "运行超时，请稍后重试。"
    return payload


def _fallback_reply(result: RunResult) -> str:
    if result.error_message:
        return result.error_message
    if result.runtime_id == RuntimeId.AGENTSCOPE_TEAM and result.status == RunStatus.SUCCEEDED:
        return ""
    return ""
