"""Read-only console catalog: industries, roles, tenants, channels, feedback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.admin.meta import envelope, utc_now_iso
from app.agents.prompting import build_system
from app.core.config import ROOT
from app.core.platform import ALL_PERMS, Platform, new_id
from app.core.rbac import ROLE_PERMS
from app.core.security import AuthContext
from app.security.redaction import redact_for_api


def list_industries(platform: Platform) -> dict:
    items = []
    for name, cfg in platform.industries.items():
        items.append(
            {
                "id": name,
                "industry": name,
                "topics": list(cfg.get("topics") or []),
                "tools": list(cfg.get("tools") or []),
                "confidence_threshold": cfg.get("confidence_threshold"),
                "max_message_length": cfg.get("max_message_length"),
                "off_topic_message": cfg.get("off_topic_message"),
                "risky_promises": list(cfg.get("risky_promises") or []),
                "template_path": f"templates/{name}/system.txt",
            }
        )
    return envelope({"items": items}, source="config_yaml", is_demo=False, updated_at=utc_now_iso())


def list_tenants(platform: Platform) -> dict:
    items = [
        {
            "code": row.get("code"),
            "name": row.get("name"),
            "industry": row.get("industry"),
            "status": row.get("status") or "active",
        }
        for row in platform.tenants.values()
    ]
    return envelope({"items": items}, source="tenants_yaml", is_demo=False)


def list_roles() -> dict:
    labels = {
        "tenant_admin": ("租户管理员", "演示账号 admin / finadmin"),
        "auditor": ("审计员", "只读审计；不可对话/审批"),
    }
    items = []
    for code, perms in ROLE_PERMS.items():
        name, note = labels.get(code, (code, ""))
        items.append({"code": code, "name": name, "perms": sorted(perms), "note": note})
    return envelope({"items": items}, source="rbac_catalog", is_demo=False)


def permissions_tree() -> dict:
    groups: dict[str, list[str]] = {}
    for perm in sorted(ALL_PERMS):
        prefix = perm.split(":", 1)[0]
        groups.setdefault(prefix, []).append(perm)
    tree = [{"group": g, "perms": ps} for g, ps in sorted(groups.items())]
    return envelope({"items": tree}, source="ALL_PERMS", is_demo=False)


def list_channels() -> dict:
    settings_note = "渠道接入为二期配置面；当前仅 Web API + 内嵌 Admin。企微/公众号未接凭证。"
    items = [
        {
            "id": "web",
            "name": "Web / Admin",
            "enabled": True,
            "mode": "live_api",
            "endpoint": "/api/chat · /admin",
            "note": "JWT Bearer；身份只信 Claim",
        },
        {
            "id": "wecom",
            "name": "企业微信",
            "enabled": False,
            "mode": "not_configured",
            "endpoint": None,
            "note": settings_note,
        },
        {
            "id": "mp",
            "name": "公众号",
            "enabled": False,
            "mode": "not_configured",
            "endpoint": None,
            "note": settings_note,
        },
    ]
    return envelope({"items": items}, source="static_catalog", is_demo=False, updated_at=utc_now_iso())


def agent_industry_config(platform: Platform, auth: AuthContext, agent_id: str) -> dict:
    agent = platform.agents.get(agent_id)
    if not agent or agent["tenant_id"] != auth.tenant_id:
        raise KeyError(agent_id)
    industry_name = agent["industry"]
    cfg = platform.industries.get(industry_name) or {}
    prompt = build_system(industry_name)
    return envelope(
        {
            "agent_id": agent_id,
            "industry": industry_name,
            "status": agent.get("status"),
            "kb_binding": agent.get("kb_binding"),
            "enabled_tools": list(agent.get("enabled_tools") or []),
            "topics": list(cfg.get("topics") or []),
            "confidence_threshold": cfg.get("confidence_threshold"),
            "max_message_length": cfg.get("max_message_length"),
            "off_topic_message": cfg.get("off_topic_message"),
            "risky_promises": list(cfg.get("risky_promises") or []),
            "prompt_preview": prompt[:400],
            "prompt_length": len(prompt),
            "prompt_full": prompt,
            "security_rules_path": "templates/common/security_rules.txt",
            "writable": False,
            "note": "行业规则来自 config/*.yaml + templates；控制台只读，换行业改配置不改底座。",
        },
        source="yaml+templates",
        is_demo=False,
    )


def list_feedback(platform: Platform, auth: AuthContext) -> dict:
    rows = getattr(platform, "feedbacks", None)
    if rows is None:
        platform.feedbacks = []  # type: ignore[attr-defined]
        rows = platform.feedbacks  # type: ignore[attr-defined]
    items = [redact_for_api(dict(r)) for r in rows if r.get("tenant_id") == auth.tenant_id]
    return envelope({"items": items}, source="memory", is_demo=False, note="进程内反馈；重启清空")


def create_feedback(platform: Platform, auth: AuthContext, body: dict[str, Any]) -> dict:
    if not hasattr(platform, "feedbacks"):
        platform.feedbacks = []  # type: ignore[attr-defined]
    row = {
        "id": new_id("fb"),
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
        "kb_id": body.get("kb_id"),
        "query": (body.get("query") or "")[:500],
        "rating": body.get("rating") or "neutral",
        "comment": (body.get("comment") or "")[:1000],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    platform.feedbacks.append(row)  # type: ignore[attr-defined]
    return redact_for_api(row)


def list_human_tickets(platform: Platform, auth: AuthContext) -> dict:
    """Human queue: explicit type=human tickets + soft-refuse audit suggestions."""
    tickets = [
        redact_for_api(dict(t))
        for t in platform.tickets.values()
        if t.get("tenant_id") == auth.tenant_id and t.get("type") == "human"
    ]
    suggestions = []
    for audit in platform.audits:
        if audit.get("tenant_id") != auth.tenant_id:
            continue
        reason = audit.get("block_reason")
        if reason in {"low_score", "off_topic"} or audit.get("need_human"):
            if audit.get("ticket_id"):
                continue  # already a risk ticket
            suggestions.append(
                redact_for_api(
                    {
                        "id": f"sug_{audit.get('audit_id')}",
                        "type": "suggestion",
                        "status": "open",
                        "reason": reason or "need_human",
                        "audit_id": audit.get("audit_id"),
                        "session_id": audit.get("session_id"),
                        "input_preview": (audit.get("input_message") or "")[:120],
                        "created_at": audit.get("created_at"),
                    }
                )
            )
    return envelope(
        {
            "items": tickets,
            "suggestions": suggestions[-50:],
            "note": "独立 human 工单+对话软拒建议；高风险工具仍走 /tickets/risk",
        },
        source="memory_audits+tickets",
        is_demo=False,
    )


def create_human_ticket(platform: Platform, auth: AuthContext, body: dict[str, Any]) -> dict:
    tid = new_id("hum")
    row = {
        "id": tid,
        "tenant_id": auth.tenant_id,
        "type": "human",
        "status": "pending",
        "applicant_id": auth.user_id,
        "tool_name": "human_handoff",
        "args_masked": {"note": (body.get("note") or "")[:200]},
        "reason": body.get("reason") or "manual",
        "session_id": body.get("session_id"),
        "audit_id": body.get("audit_id"),
        "executed": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    platform.tickets[tid] = row
    return redact_for_api(row)


def resolve_human_ticket(platform: Platform, auth: AuthContext, ticket_id: str, *, note: str | None) -> dict:
    ticket = platform.tickets.get(ticket_id)
    if not ticket or ticket.get("tenant_id") != auth.tenant_id or ticket.get("type") != "human":
        raise KeyError(ticket_id)
    ticket["status"] = "succeeded"
    ticket["reason"] = note or "handled"
    ticket["approver_id"] = auth.user_id
    ticket["executed"] = False
    return redact_for_api(ticket)


def patch_strategy(
    platform: Platform,
    kind: str,
    strategy_id: str,
    body: dict[str, Any],
    *,
    actor_id: str | None = None,
) -> dict:
    """Patch strategy in memory and persist to PostgreSQL when durable storage is on.

    Optional body.expected_version enables optimistic concurrency (409 on mismatch).
    On ConflictError the in-memory store is left unchanged (DB is authority).
    """
    store = platform.chunk_strategies if kind == "chunk" else platform.retrieve_strategies
    row = store.get(strategy_id)
    if not row:
        raise KeyError(strategy_id)
    current_version = int(row.get("version") or 1)
    expected = body.get("expected_version")
    if expected is not None and current_version != int(expected):
        from app.infra.entity_repo import ConflictError

        raise ConflictError(f"strategy version conflict: have={current_version} expected={expected}")

    candidate = dict(row)
    if kind == "chunk":
        for key in ("chunk_size", "child_size", "overlap", "name"):
            if key in body and body[key] is not None:
                candidate[key] = body[key]
    else:
        for key in ("vector_min_score", "rerank_min_score", "top_k", "name"):
            if key in body and body[key] is not None:
                candidate[key] = body[key]
    candidate["version"] = current_version + 1
    candidate["updated_by"] = actor_id

    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import upsert_chunk_strategy, upsert_retrieve_strategy

        payload = {**candidate, "expected_version": current_version, "updated_by": actor_id}
        # Persist first; only then mutate memory so ConflictError cannot dirty the registry.
        if kind == "chunk":
            persisted = upsert_chunk_strategy(payload)
        else:
            persisted = upsert_retrieve_strategy(payload)
        candidate.update(persisted)
        store[strategy_id] = candidate
        return dict(candidate)

    store[strategy_id] = candidate
    return dict(candidate)


def template_snippet(industry: str, *, max_chars: int = 800) -> str:
    path = ROOT / "templates" / industry / "system.txt"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    return text[:max_chars]
