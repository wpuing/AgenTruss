"""LangGraph authoritative chat workflow with modular stage nodes.

Stages map 1:1 to ``app.agents.nodes`` — not a second wrapper around handle_chat.
AgentScope remains a delegated runtime via the registry.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from app.agents.nodes import (
    TurnCtx,
    new_turn_ctx,
    public_result,
    stage_input_guard,
    stage_llm_generate,
    stage_retrieve,
    stage_tool_gate,
)
from app.agents.prompting import build_system
from app.core.config import get_settings
from app.core.platform import Platform, new_id
from app.core.security import AuthContext
from app.guardrails.engine import mask_pii
from app.memory.store import append_turn
from app.security.redaction import redact_for_api, redact_for_logs

logger = logging.getLogger(__name__)


class ChatGraphState(TypedDict, total=False):
    tenant_id: str
    user_id: str
    message: str
    session_id: str
    conversation_id: str
    agent_id: str | None
    channel: str
    trace_id: str
    run_id: str
    reply: str
    sources: list[str]
    ticket_id: str | None
    need_human: bool
    guardrail_hit: bool
    block_reason: str | None
    audit_id: str | None
    error: str | None
    paused: bool
    # Internal pipeline handle (not serialized to client)
    _ctx_id: str


# Per-compile ephemeral ctx map keyed by run — avoids putting Platform in state.
_CTX: dict[str, TurnCtx] = {}


def compile_chat_graph(platform: Platform, auth: AuthContext, checkpointer=None):
    """Compile a per-request graph bound to platform/auth (tenant isolation)."""
    from langgraph.graph import END, StateGraph

    graph = StateGraph(ChatGraphState)

    def prepare(state: ChatGraphState) -> dict[str, Any]:
        if state.get("tenant_id") and state["tenant_id"] != auth.tenant_id:
            return {"error": "tenant_mismatch", "guardrail_hit": True, "block_reason": "tenant_mismatch"}
        body = {
            "message": state.get("message") or "",
            "session_id": state.get("session_id"),
            "channel": state.get("channel") or "web",
            "agent_id": state.get("agent_id"),
        }
        ctx = new_turn_ctx(platform, auth, body)
        ctx_id = new_id("ctx")
        _CTX[ctx_id] = ctx
        return {
            "session_id": ctx.session_id,
            "conversation_id": state.get("conversation_id") or ctx.session_id,
            "trace_id": state.get("trace_id") or new_id("trc"),
            "run_id": state.get("run_id") or new_id("run"),
            "channel": body["channel"],
            "tenant_id": auth.tenant_id,
            "user_id": auth.user_id,
            "paused": False,
            "error": None,
            "_ctx_id": ctx_id,
        }

    started = time.perf_counter()

    def input_guard(state: ChatGraphState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        ctx = _CTX[state["_ctx_id"]]
        stage_input_guard(ctx)
        if ctx.stopped:
            return _partial_from_ctx(ctx)
        return {"session_id": ctx.session_id}

    def retrieve(state: ChatGraphState) -> dict[str, Any]:
        ctx = _CTX[state["_ctx_id"]]
        if ctx.stopped:
            return {}
        stage_retrieve(ctx)
        if ctx.stopped:
            return _partial_from_ctx(ctx)
        return {}

    def execute_agent(state: ChatGraphState) -> dict[str, Any]:
        ctx = _CTX[state["_ctx_id"]]
        if ctx.stopped:
            return {}
        stage_llm_generate(ctx)
        if ctx.stopped:
            return _partial_from_ctx(ctx)
        return {"reply": ctx.reply}

    def tool_gate(state: ChatGraphState) -> dict[str, Any]:
        ctx = _CTX[state["_ctx_id"]]
        if not ctx.stopped:
            stage_tool_gate(ctx)
        return _partial_from_ctx(ctx)

    def finalize(state: ChatGraphState) -> dict[str, Any]:
        ctx_id = state.get("_ctx_id")
        ctx = _CTX.pop(ctx_id, None) if ctx_id else None
        if ctx is None:
            return {"error": state.get("error") or "missing_context"}
        result = public_result(ctx)
        audit = ctx.audit
        message = ctx.message
        system_prompt = build_system(ctx.agent["industry"])
        audit["input_message"] = redact_for_logs(audit.get("sanitized") or mask_pii(message))
        audit["system_contains_user"] = bool(message) and message in system_prompt
        audit["output_message"] = redact_for_logs(result["reply"])
        audit["latency_ms"] = int((time.perf_counter() - started) * 1000)
        safe_audit = redact_for_api(audit)
        platform.audits.append(safe_audit)
        try:
            from app.infra.entity_repo import append_audit

            append_audit(safe_audit if isinstance(safe_audit, dict) else audit)
        except Exception:  # noqa: BLE001
            from app.core.config import get_settings
            from app.infra.ticket_repo import persistence_enabled

            if get_settings().is_production or persistence_enabled():
                raise
        append_turn(
            platform,
            auth.tenant_id,
            auth.user_id,
            ctx.session_id,
            audit.get("sanitized") or "",
            result["reply"],
            blocked=bool(audit["blocked"]),
        )
        paused = bool(audit.get("need_human") and audit.get("ticket_id"))
        return {
            "reply": result["reply"],
            "guardrail_hit": result["guardrail_hit"],
            "block_reason": result["block_reason"],
            "audit_id": audit["audit_id"],
            "sources": list(audit.get("sources") or []),
            "need_human": bool(audit.get("need_human")),
            "ticket_id": audit.get("ticket_id"),
            "session_id": ctx.session_id,
            "paused": paused,
        }

    graph.add_node("prepare", prepare)
    graph.add_node("input_guard", input_guard)
    graph.add_node("retrieve", retrieve)
    graph.add_node("execute_agent", execute_agent)
    graph.add_node("tool_gate", tool_gate)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("prepare")
    graph.add_edge("prepare", "input_guard")
    graph.add_edge("input_guard", "retrieve")
    graph.add_edge("retrieve", "execute_agent")
    graph.add_edge("execute_agent", "tool_gate")
    graph.add_edge("tool_gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _partial_from_ctx(ctx: TurnCtx) -> dict[str, Any]:
    return {
        "reply": ctx.reply,
        "guardrail_hit": ctx.guardrail_hit,
        "block_reason": ctx.block_reason,
        "session_id": ctx.session_id,
        "need_human": bool(ctx.audit.get("need_human")),
        "ticket_id": ctx.audit.get("ticket_id"),
        "sources": list(ctx.audit.get("sources") or []),
        "audit_id": ctx.audit.get("audit_id"),
        "paused": bool(ctx.audit.get("need_human") and ctx.audit.get("ticket_id")),
    }


def run_langgraph_chat(platform: Platform, auth: AuthContext, body: dict) -> dict:
    """Invoke LangGraph workflow; falls back to linear turn when disabled/unavailable."""
    from app.agents.turn import handle_chat

    settings = get_settings()
    if settings.workflow_engine != "langgraph":
        return handle_chat(platform, auth, body)

    try:
        import langgraph  # noqa: F401
    except ImportError:
        logger.warning("langgraph not installed; using linear turn path")
        return handle_chat(platform, auth, body)

    # Rate limit once at the edge (same as linear path).
    from app.agents.turn import _rate_limit

    _rate_limit(platform, auth)

    checkpointer = _resolve_checkpointer()
    app = compile_chat_graph(platform, auth, checkpointer=checkpointer)
    thread_id = body.get("session_id") or new_id("thd")
    config = {
        "configurable": {
            "thread_id": f"{auth.tenant_id}:{thread_id}",
        }
    }
    initial: ChatGraphState = {
        "message": str(body.get("message") or ""),
        "session_id": str(thread_id),
        "agent_id": body.get("agent_id"),
        "channel": str(body.get("channel") or "web"),
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
    }
    result = app.invoke(initial, config)
    return {
        "reply": result.get("reply") or "",
        "session_id": result.get("session_id"),
        "audit_id": result.get("audit_id"),
        "sources": result.get("sources") or [],
        "need_human": bool(result.get("need_human")),
        "ticket_id": result.get("ticket_id"),
        "guardrail_hit": bool(result.get("guardrail_hit")),
        "block_reason": result.get("block_reason"),
        "workflow_engine": "langgraph",
        "workflow_thread_id": f"{auth.tenant_id}:{thread_id}",
        "trace_id": result.get("trace_id"),
        "run_id": result.get("run_id"),
        "paused": bool(result.get("paused")),
    }


def _resolve_checkpointer():
    settings = get_settings()
    if (
        settings.checkpointer_backend == "postgres"
        and settings.storage_backend == "postgres"
        and settings.database_url
    ):
        try:
            from app.agents import checkpoint as cp

            saver = cp.get_postgres_checkpointer()
            if saver is not None:
                return saver
        except Exception as exc:  # noqa: BLE001
            logger.warning("postgres checkpointer unavailable: %s", exc)
            if settings.is_production:
                raise
    try:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    except Exception:  # noqa: BLE001
        return None
