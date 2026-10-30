"""Composable chat pipeline stages used by linear turn and LangGraph nodes.

Each stage mutates a shared TurnCtx and returns a stop signal when the turn
should end early (blocked / low score / approval). Stages must not re-run
guards that already completed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from app.agents.prompting import build_system
from app.core.platform import Platform, new_id
from app.guardrails.engine import check_input, check_output
from app.memory.store import history_messages, read_turns
from app.rag.service import retrieve
from app.security.redaction import redact_for_llm
from app.tools.registry import authorize_call, extract_tool_call


@dataclass
class TurnCtx:
    platform: Platform
    auth: Any
    message: str
    session_id: str
    agent: dict
    industry: dict
    audit: dict
    sanitized: str = ""
    hits: list[dict] = field(default_factory=list)
    reply: str = ""
    guardrail_hit: bool = False
    block_reason: str | None = None
    stopped: bool = False


def new_turn_ctx(platform: Platform, auth, body: dict) -> TurnCtx:
    session_id = body.get("session_id") or new_id("ses")
    agent = _resolve_agent(platform, auth.tenant_id, body.get("agent_id"))
    industry = platform.industries[agent["industry"]]
    message = body.get("message") or ""
    system_prompt = build_system(agent["industry"])
    audit = {
        "audit_id": new_id("aud"),
        "user_id": auth.user_id,
        "tenant_id": auth.tenant_id,
        "session_id": session_id,
        "channel": body.get("channel") or "web",
        "agent_id": agent["agent_id"],
        "input_message": message,
        "llm_called": False,
        "blocked": False,
        "need_human": False,
        "ticket_id": None,
        "sources": [],
        "system_contains_user": message[:40] in system_prompt if message else False,
    }
    return TurnCtx(
        platform=platform,
        auth=auth,
        message=message,
        session_id=session_id,
        agent=agent,
        industry=industry,
        audit=audit,
    )


def stage_input_guard(ctx: TurnCtx) -> TurnCtx:
    """Validate / sanitize input and enforce topic / injection boundaries."""
    guard = check_input(ctx.message, ctx.industry)
    ctx.audit["topic"] = guard.topic
    ctx.audit["confidence"] = guard.confidence
    ctx.audit["sanitized"] = guard.sanitized_message
    ctx.sanitized = guard.sanitized_message
    if not guard.allowed:
        ctx.audit["blocked"] = guard.reason in {"injection", "too_long"}
        ctx.audit["block_reason"] = guard.reason
        ctx.audit["guardrail_hits"] = [guard.reason]
        ctx.reply = guard.block_message
        ctx.guardrail_hit = guard.reason != "off_topic"
        ctx.block_reason = guard.reason
        ctx.stopped = True
        if guard.reason == "off_topic":
            ctx.audit["need_human"] = True
    return ctx


def stage_retrieve(ctx: TurnCtx) -> TurnCtx:
    """Authorized RAG retrieval; stop on insufficient evidence."""
    if ctx.stopped:
        return ctx
    hits: list[dict] = []
    if ctx.agent["kb_binding"].get("rag_enabled") and ctx.agent["kb_binding"].get("kb_id"):
        hits = retrieve(
            ctx.platform,
            ctx.auth.tenant_id,
            ctx.agent["kb_binding"]["kb_id"],
            ctx.sanitized,
            None,
        )
        if not hits:
            ctx.audit["block_reason"] = "low_score"
            ctx.audit["guardrail_hits"] = ["low_score"]
            ctx.audit["need_human"] = True
            ctx.reply = "知识库中没有足够依据，建议转人工。"
            ctx.guardrail_hit = False
            ctx.block_reason = "low_score"
            ctx.stopped = True
            return ctx
    ctx.hits = hits
    return ctx


def stage_llm_generate(ctx: TurnCtx) -> TurnCtx:
    """Credential gate + stub/model reply + output guard. Honors redact_for_llm.blocked."""
    if ctx.stopped:
        return ctx
    history = history_messages(read_turns(ctx.platform, ctx.auth.tenant_id, ctx.session_id))
    ctx.audit["history_roles"] = [item["role"] for item in history]
    llm_prep = redact_for_llm(ctx.sanitized)
    if llm_prep.blocked:
        ctx.audit["blocked"] = True
        ctx.audit["block_reason"] = "credential_in_prompt"
        ctx.audit["guardrail_hits"] = ["credential_in_prompt"]
        ctx.audit["llm_called"] = False
        ctx.reply = "请求包含不可发送给模型的凭证信息，已拦截。"
        ctx.guardrail_hit = True
        ctx.block_reason = "credential_in_prompt"
        ctx.stopped = True
        return ctx
    safe_hits = []
    for hit in ctx.hits:
        row = dict(hit)
        if "text" in row:
            hit_prep = redact_for_llm(str(row["text"]))
            row["text"] = "[REDACTED_CONTEXT]" if hit_prep.blocked else hit_prep.value
        safe_hits.append(row)
    reply, called = _stub_llm(llm_prep.value, safe_hits, history)
    ctx.audit["llm_called"] = called
    output = check_output(reply, ctx.industry)
    if not output.allowed:
        ctx.audit["blocked"] = True
        ctx.audit["block_reason"] = output.reason
        ctx.audit["guardrail_hits"] = [output.reason]
        ctx.reply = output.block_message
        ctx.guardrail_hit = True
        ctx.block_reason = output.reason
        ctx.stopped = True
        return ctx
    ctx.audit["sources"] = [hit["segment_id"] for hit in ctx.hits]
    ctx.reply = reply if ctx.hits else reply
    return ctx


def stage_tool_gate(ctx: TurnCtx) -> TurnCtx:
    """Tool whitelist + high-risk approval. No side effects before approve/execute."""
    if ctx.stopped:
        return ctx
    tool_name, args = extract_tool_call(ctx.sanitized)
    if not tool_name:
        ctx.guardrail_hit = False
        ctx.block_reason = None
        return ctx
    decision = authorize_call(ctx.platform, ctx.auth, ctx.agent, tool_name, args)
    ctx.audit["tool_name"] = tool_name
    ctx.audit["need_human"] = bool(decision.get("need_human"))
    ctx.audit["ticket_id"] = decision.get("ticket_id")
    if not decision["ok"]:
        ctx.audit["blocked"] = decision.get("reason") == "tool_denied"
        ctx.audit["block_reason"] = decision.get("reason")
        denied = decision.get("reason") == "tool_denied"
        ctx.reply = decision["reply"]
        ctx.guardrail_hit = denied
        ctx.block_reason = decision.get("reason")
        ctx.stopped = True
        return ctx
    ctx.reply = decision["reply"]
    ctx.guardrail_hit = False
    ctx.block_reason = None
    ctx.stopped = True  # tool path replaces LLM reply as final
    return ctx


def public_result(ctx: TurnCtx) -> dict:
    return {
        "reply": ctx.reply,
        "guardrail_hit": ctx.guardrail_hit,
        "block_reason": ctx.block_reason,
    }


def _stub_llm(message: str, hits: list[dict], history: list[dict]) -> tuple[str, bool]:
    """Deterministic stub reply. Always ``llm_called=False`` — never claim a real model call."""
    prior = len(history) // 2
    if "系统提示词" in message or "system prompt" in message.lower():
        return "system prompt: hidden rules", False
    if hits:
        prefix = f"（参考前 {prior} 轮）" if prior else ""
        return f"{prefix}根据知识库：{hits[0]['text']}", False
    if prior:
        return f"已收到您的问题（会话已有 {prior} 轮上下文）。", False
    return "已收到您的问题。", False


def _resolve_agent(platform: Platform, tenant_id: str, agent_id: str | None) -> dict:
    if agent_id:
        agent = platform.agents.get(agent_id)
        if not agent or agent["tenant_id"] != tenant_id:
            raise HTTPException(status_code=404, detail="agent not found")
        # Keep 404 (not 403) so clients cannot probe existence across tenants;
        # detail distinguishes disabled/draft for the admin SPA toast.
        if agent.get("status") != "published":
            status = agent.get("status") or "unknown"
            raise HTTPException(
                status_code=404,
                detail=f"agent not published (status={status})",
            )
        return agent
    try:
        return platform.default_agent(tenant_id)
    except KeyError as exc:
        # Same gate as explicit id: no published default → 404 (not 200 FAILED).
        raise HTTPException(
            status_code=404,
            detail="agent not published (status=disabled)",
        ) from exc
