"""Single tool registry. Unregistered names cannot run."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from app.core.crypto import args_integrity_digest, encrypt_json, mask_tool_args
from app.core.platform import new_id

logger = logging.getLogger(__name__)

_TOOLS: dict[str, dict] = {}

# Honesty labels for Admin/docs — NOT a substitute for supports_result_lookup.
# Recovery / LOOKUP_FIRST authority remains supports_result_lookup (+ idempotency).
ALLOWED_CAPABILITY_TIERS = frozenset(
    {
        "local_demo",
        "protocol_sandbox",
        "https_demo",
        "durable_lookup",
        "production",
    }
)


class CapabilityDeclarationError(ValueError):
    """Illegal or inconsistent tool capability metadata."""


def normalize_capability_tier(raw: object | None) -> str:
    """Return a known tier; unknown values are rejected (never upgraded)."""
    tier = str(raw or "local_demo").strip().lower() or "local_demo"
    if tier not in ALLOWED_CAPABILITY_TIERS:
        raise CapabilityDeclarationError(
            f"unknown capability_tier={tier!r}; allowed={sorted(ALLOWED_CAPABILITY_TIERS)}"
        )
    return tier


def validate_capability_meta(meta: dict) -> str:
    """Reject Demo→Production mislabels and inconsistent lookup claims.

    Returns the normalized ``capability_tier``. Does not grant production
    readiness by itself — ``production`` also requires ``production_acceptance_id``.
    """
    if "name" not in meta or not str(meta.get("name") or "").strip():
        raise CapabilityDeclarationError("tool name is required")
    tier = normalize_capability_tier(meta.get("capability_tier"))
    lookup = bool(meta.get("supports_result_lookup", False))
    # local_demo must not advertise durable external result lookup.
    if tier == "local_demo" and lookup:
        raise CapabilityDeclarationError(
            f"{meta['name']}: local_demo cannot set supports_result_lookup=True"
        )
    # Stronger tiers require an explicit lookup capability flag.
    if tier in {"durable_lookup", "production"} and not lookup:
        raise CapabilityDeclarationError(
            f"{meta['name']}: {tier} requires supports_result_lookup=True"
        )
    # production is a gated claim — need an acceptance id (sandbox evidence ref).
    if tier == "production":
        evidence = str(meta.get("production_acceptance_id") or "").strip()
        if not evidence:
            raise CapabilityDeclarationError(
                f"{meta['name']}: production tier requires production_acceptance_id "
                "(sandbox/acceptance evidence id); do not self-label Demo as production"
            )
    return tier


def register(**meta):
    """Register a handler. The decorator returns the original function."""

    def decorator(fn):
        tier = validate_capability_meta(meta)
        stored = {**meta, "capability_tier": tier, "handler": fn}
        _TOOLS[meta["name"]] = stored
        logger.debug(
            "tool registered name=%s tier=%s lookup=%s",
            meta["name"],
            tier,
            bool(meta.get("supports_result_lookup")),
        )
        return fn

    return decorator


def get_tool(name: str) -> dict | None:
    return _TOOLS.get(name)


def registered_names() -> list[str]:
    return sorted(_TOOLS)


def public_tool_catalog() -> list[dict]:
    """Safe tool metadata for admin UI (excludes callables / secrets).

    ``capability_tier`` is declarative honesty for Admin/docs.
    Recovery decisions use ``supports_result_lookup`` / idempotency flags, not tier alone.
    """
    rows: list[dict] = []
    for name in registered_names():
        meta = _TOOLS[name]
        tier = normalize_capability_tier(meta.get("capability_tier"))
        rows.append(
            {
                "name": name,
                "description": meta.get("description") or "",
                "high_risk": bool(meta.get("high_risk")),
                "requires_auth": bool(meta.get("requires_auth", True)),
                "scope": meta.get("scope") or "self_only",
                "required_roles": list(meta.get("required_roles") or []),
                "has_side_effects": bool(meta.get("has_side_effects", meta.get("high_risk"))),
                "supports_idempotency_key": bool(meta.get("supports_idempotency_key", False)),
                "supports_result_lookup": bool(meta.get("supports_result_lookup", False)),
                "capability_tier": tier,
            }
        )
    return rows


@register(
    name="query_order",
    description="查询当前用户订单",
    requires_auth=True,
    scope="self_only",
    high_risk=False,
    required_roles=["tenant_admin", "agent", "end_user"],
    has_side_effects=False,
    supports_idempotency_key=False,
    supports_result_lookup=False,
    capability_tier="local_demo",
)
def query_order(args: dict, ctx: dict) -> str:
    """Read-only order lookup for the token owner."""
    return f"订单查询成功 owner={ctx['owner_user_id']} order={args.get('order_id', 'latest')}"


@register(
    name="refund_order",
    description="退款",
    requires_auth=True,
    scope="self_only",
    high_risk=True,
    # Submitters (end users / admins) may request approval. Approvers use ticket APIs.
    required_roles=["tenant_admin", "agent", "end_user"],
    has_side_effects=True,
    supports_idempotency_key=False,
    supports_result_lookup=False,
    capability_tier="local_demo",  # Demo only — RECONCILE_ONLY, not durable lookup
)
def refund_order(args: dict, ctx: dict) -> str:
    """High-risk refund. Only callable after ticket approval + revalidation."""
    return f"已退款 owner={ctx['owner_user_id']} order={args.get('order_id', '')}"


def extract_tool_call(message: str) -> tuple[str | None, dict]:
    """Map a few demo utterances onto registered tools."""
    if "申请退款" in message:
        return "refund_order", {"order_id": "1001", "user_id": "from-body"}
    if "查询订单" in message:
        args = {}
        matched = re.search(r"user_id=([A-Za-z0-9_\-]+)", message)
        if matched:
            args["user_id"] = matched.group(1)
        return "query_order", args
    return None, {}


def _role_allowed(auth, required_roles: list[str] | None) -> bool:
    if not required_roles:
        return True
    return any(role in auth.roles for role in required_roles)


def _idempotency_key(tenant_id: str, tool_name: str, clean_args: dict) -> str:
    raw = json.dumps(
        {"tenant": tenant_id, "tool": tool_name, "args": clean_args},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def authorize_call(platform, auth, agent: dict, name: str, args: dict) -> dict:
    """Enforce registry, roles, tenant tool allow-list, self scope, and high-risk tickets.

    High-risk tools never run their handler here — they create a pending ticket only.
    Normal users with matching required_roles may submit; approvers act via ticket APIs.
    """
    tool = get_tool(name)
    if tool is None or name not in agent.get("enabled_tools", []):
        return {"ok": False, "reason": "tool_denied", "reply": "工具未注册或未启用。"}
    if tool.get("requires_auth") and not getattr(auth, "user_id", None):
        return {"ok": False, "reason": "tool_denied", "reply": "工具需要登录身份。"}
    if not _role_allowed(auth, tool.get("required_roles")):
        return {"ok": False, "reason": "tool_denied", "reply": "当前角色无权调用该工具。"}

    clean_args = dict(args)
    if tool.get("scope") == "self_only":
        clean_args.pop("user_id", None)
        clean_args["owner_user_id"] = auth.user_id

    if tool.get("high_risk"):
        idem = _idempotency_key(auth.tenant_id, name, clean_args)
        for existing in platform.tickets.values():
            if (
                existing.get("tenant_id") == auth.tenant_id
                and existing.get("idempotency_key") == idem
                and existing.get("status")
                in {
                    "pending",
                    "approved",
                    "revalidated",
                    "executing",
                    "succeeded",
                    "reconciliation_required",
                }
            ):
                if existing["status"] == "succeeded":
                    reply = "相同请求已执行成功，跳过重复副作用。"
                    need_human = False
                    reason = "already_executed"
                elif existing["status"] == "reconciliation_required":
                    reply = "相同请求执行结果待人工对账，请勿重复提交或自动重跑。"
                    need_human = True
                    reason = "reconciliation_required"
                else:
                    reply = "相同请求已有审批单，请勿重复提交。"
                    need_human = True
                    reason = "approval_required"
                return {
                    "ok": False,
                    "reason": reason,
                    "ticket_id": existing["id"],
                    "need_human": need_human,
                    "reply": reply,
                }
        ticket_id = new_id("tck")
        now = datetime.now(timezone.utc).isoformat()
        cipher = encrypt_json(clean_args)
        ticket = {
            "id": ticket_id,
            "type": "risk",
            "tenant_id": auth.tenant_id,
            "status": "pending",
            "user_id": auth.user_id,
            "applicant_id": auth.user_id,
            "applicant_roles": list(auth.roles),
            "agent_id": agent.get("agent_id"),
            "tool_name": name,
            # Sensitive args encrypted at rest; API responses use args_masked only.
            "args_encrypted": cipher,
            "args": None,
            "args_masked": mask_tool_args(clean_args),
            # Bind ciphertext so execute rejects post-approval mutation of args_encrypted.
            "args_digest": args_integrity_digest(cipher),
            "idempotency_key": idem,
            "executed": False,
            "created_at": now,
            "approver_id": None,
            "decided_at": None,
            "decision": None,
            "reason": None,
            "execution_result": None,
            "workflow_thread_id": None,
            "run_id": None,
            "trace_id": None,
        }
        platform.tickets[ticket_id] = ticket
        try:
            from app.infra.ticket_repo import upsert_ticket

            upsert_ticket(ticket)
        except Exception:  # noqa: BLE001 — persistence must not break request path in memory mode
            pass
        return {
            "ok": False,
            "reason": "approval_required",
            "ticket_id": ticket_id,
            "need_human": True,
            "reply": "高风险操作已提交审批，尚未执行。",
        }

    reply = tool["handler"](clean_args, {"owner_user_id": auth.user_id})
    return {"ok": True, "reply": reply, "ticket_id": None, "need_human": False}
