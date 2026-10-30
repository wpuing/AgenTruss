"""Default single-agent runtime: reuses the existing deterministic turn path.

This is not LangGraph. Today the authoritative dialogue path is
``app.agents.turn.handle_chat`` (linear guards → RAG stub → tool gate).
"""

from __future__ import annotations

from fastapi import HTTPException

from app.agents.graph import run_langgraph_chat
from app.core.config import get_settings
from app.core.platform import Platform, new_id
from app.core.security import AuthContext
from app.runtime.events import EventType, ExecutionEvent
from app.runtime.models import RunRequest, RunResult, RunStatus, RuntimeId, UsageStats


class SingleAgentRuntime:
    """Wraps the in-memory MVP chat path behind the unified RunRequest/Result API."""

    @property
    def runtime_id(self) -> RuntimeId:
        return RuntimeId.SINGLE

    def execute(self, platform: Platform, request: RunRequest) -> RunResult:
        trace_id = request.trace_id or new_id("trc")
        run_id = request.run_id or new_id("run")
        events: list[dict] = []

        def emit(event_type: EventType, message: str | None = None, **payload) -> None:
            events.append(
                ExecutionEvent(
                    type=event_type,
                    trace_id=trace_id,
                    run_id=run_id,
                    tenant_id=request.subject.tenant_id,
                    conversation_id=request.conversation_id,
                    session_id=request.session_id,
                    agent_id=request.agent_id,
                    config_version=request.config_version,
                    message=message,
                    payload=payload,
                ).as_dict()
            )

        emit(EventType.RUN_STARTED, agent_id=request.agent_id)
        emit(EventType.AGENT_STARTED, runtime="single")

        auth = AuthContext(
            user_id=request.subject.user_id,
            tenant_id=request.subject.tenant_id,
            roles=list(request.subject.roles),
            perms=set(request.subject.perms),
        )
        body = {
            "message": request.message,
            "session_id": request.session_id,
            "channel": request.channel,
            "agent_id": request.agent_id,
        }
        try:
            # Default authoritative path: LangGraph (falls back to linear when configured).
            raw = run_langgraph_chat(platform, auth, body)
            if get_settings().workflow_engine == "langgraph" and raw.get("workflow_engine") == "langgraph":
                emit(EventType.AGENT_STARTED, workflow="langgraph")
        except HTTPException:
            # Preserve gateway semantics (e.g. 429 rate limit) for the HTTP layer.
            raise
        except Exception as exc:  # noqa: BLE001 — map to structured failure
            from app.security.redaction import sanitize_exception

            safe = sanitize_exception(exc)
            emit(EventType.RUN_FAILED, message=safe)
            return RunResult(
                status=RunStatus.FAILED,
                runtime_id=self.runtime_id,
                error_code="runtime_error",
                error_message=safe,
                trace_id=trace_id,
                run_id=run_id,
                session_id=request.session_id,
                events=events,
            )

        status = _map_status(raw)
        if status == RunStatus.APPROVAL_REQUIRED:
            emit(EventType.APPROVAL_REQUESTED, ticket_id=raw.get("ticket_id"))
        elif status == RunStatus.BLOCKED:
            emit(EventType.AGENT_FAILED, block_reason=raw.get("block_reason"))
        else:
            emit(EventType.AGENT_COMPLETED)
        emit(EventType.RUN_FINISHED, status=status.value)
        emit(EventType.MESSAGE_EMITTED, preview=(raw.get("reply") or "")[:120])

        return RunResult(
            status=status,
            runtime_id=self.runtime_id,
            reply=raw.get("reply") or "",
            structured={
                "guardrail_hit": bool(raw.get("guardrail_hit")),
                "block_reason": raw.get("block_reason"),
                "llm_called": bool(raw.get("llm_called")),
                "llm_mode": raw.get("llm_mode") or ("live" if raw.get("llm_called") else "stub"),
            },
            error_code=raw.get("block_reason") if status in {RunStatus.BLOCKED, RunStatus.APPROVAL_REQUIRED} else None,
            error_message=None,
            trace_id=trace_id,
            run_id=run_id,
            session_id=raw.get("session_id"),
            audit_id=raw.get("audit_id"),
            ticket_id=raw.get("ticket_id"),
            need_human=bool(raw.get("need_human")),
            guardrail_hit=bool(raw.get("guardrail_hit")),
            sources=list(raw.get("sources") or []),
            usage=UsageStats(llm_calls=1 if raw.get("llm_called") else 0),
            events=events,
        )


def _map_status(raw: dict) -> RunStatus:
    if raw.get("need_human") and raw.get("ticket_id"):
        return RunStatus.APPROVAL_REQUIRED
    if raw.get("guardrail_hit") or raw.get("block_reason") in {
        "injection",
        "too_long",
        "tool_denied",
    }:
        return RunStatus.BLOCKED
    if raw.get("block_reason") in {"off_topic", "low_score"}:
        # Soft refuse: reply returned, not a hard security block.
        return RunStatus.SUCCEEDED
    return RunStatus.SUCCEEDED
