"""One chat turn: composed stages from nodes.py + audit / memory persistence."""

from __future__ import annotations

import time

from fastapi import HTTPException

from app.agents.nodes import (
    new_turn_ctx,
    public_result,
    stage_input_guard,
    stage_llm_generate,
    stage_retrieve,
    stage_tool_gate,
)
from app.agents.prompting import build_system
from app.core.platform import Platform
from app.guardrails.engine import mask_pii
from app.memory.store import append_turn
from app.security.redaction import redact_for_api, redact_for_logs


def handle_chat(platform: Platform, auth, body: dict) -> dict:
    """Run the dialogue path and always write an audit row."""
    started = time.perf_counter()
    _rate_limit(platform, auth)
    ctx = new_turn_ctx(platform, auth, body)
    message = ctx.message
    system_prompt = build_system(ctx.agent["industry"])

    stage_input_guard(ctx)
    stage_retrieve(ctx)
    stage_llm_generate(ctx)
    stage_tool_gate(ctx)
    result = public_result(ctx)

    audit = ctx.audit
    # Audit store keeps redacted views — never raw secrets (mask_pii alone is insufficient).
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
    result.update({
        "session_id": ctx.session_id,
        "audit_id": audit["audit_id"],
        "sources": audit["sources"],
        "need_human": audit["need_human"],
        "ticket_id": audit["ticket_id"],
        "llm_called": bool(audit.get("llm_called")),
        "llm_mode": "stub" if not audit.get("llm_called") else "live",
    })
    return result


def _rate_limit(platform: Platform, auth) -> None:
    from app.core.config import get_settings
    from app.infra.redis_client import get_redis, redis_rate_limit

    settings = get_settings()
    limit = settings.rate_limit_per_minute
    key = f"{auth.tenant_id}:{auth.user_id}"
    if get_redis() is not None:
        if not redis_rate_limit(key, limit, 60):
            raise HTTPException(status_code=429, detail="rate limited")
        return
    if settings.is_production and settings.redis_required:
        raise HTTPException(status_code=503, detail="rate limiter unavailable")
    now = time.time()
    bucket = [stamp for stamp in platform.rate_buckets.get(key, []) if now - stamp < 60]
    if len(bucket) >= limit:
        platform.rate_buckets[key] = bucket
        raise HTTPException(status_code=429, detail="rate limited")
    bucket.append(now)
    platform.rate_buckets[key] = bucket
