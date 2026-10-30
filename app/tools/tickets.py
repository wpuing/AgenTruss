"""High-risk ticket state machine and side-effect execution."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.crypto import decrypt_json
from app.modules.approvals.domain.enums import TERMINAL_STATUSES
from app.security.redaction import sanitize_exception
from app.tools.registry import get_tool

_LOCKS_GUARD = threading.Lock()
_TICKET_LOCKS: dict[str, threading.Lock] = {}

# Compatibility alias — statuses come from domain enum.
TERMINAL = frozenset(s.value for s in TERMINAL_STATUSES)


def _ticket_lock(ticket_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _TICKET_LOCKS.get(ticket_id)
        if lock is None:
            lock = threading.Lock()
            _TICKET_LOCKS[ticket_id] = lock
        return lock


def _load_args(ticket: dict) -> dict:
    if ticket.get("args_encrypted"):
        return decrypt_json(ticket["args_encrypted"])
    # Legacy in-memory tickets from older runs — never preferred for new tickets.
    return dict(ticket.get("args") or {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist(ticket: dict) -> None:
    """Persist ticket; production/postgres must not silently drop durable writes."""
    import logging

    from app.core.config import get_settings
    from app.infra.ticket_repo import persistence_enabled, upsert_ticket

    logger = logging.getLogger(__name__)
    try:
        upsert_ticket(ticket)
    except Exception as exc:  # noqa: BLE001
        from app.modules.approvals.domain.exceptions import TicketConflictError

        if isinstance(exc, TicketConflictError):
            raise HTTPException(status_code=409, detail=exc.message) from exc
        settings = get_settings()
        if settings.is_production or persistence_enabled():
            logger.exception("ticket persist failed id=%s", ticket.get("id"))
            raise HTTPException(status_code=503, detail="ticket persistence unavailable") from exc
        logger.warning("ticket persist skipped (memory mode): %s", sanitize_exception(exc))


def get_ticket(platform, tenant_id: str, ticket_id: str) -> dict:
    """Prefer PostgreSQL when durable; keep process cache as a hot mirror."""
    from app.infra.ticket_repo import get_ticket_row, persistence_enabled

    if persistence_enabled():
        ticket = get_ticket_row(ticket_id, tenant_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        platform.tickets[ticket_id] = ticket
        return ticket
    ticket = platform.tickets.get(ticket_id)
    if not ticket or ticket["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket


def reject_ticket(platform, auth, ticket_id: str, reason: str | None = None) -> dict:
    """pending → rejected. Prefer ApprovalApplicationService; kept for compatibility."""
    from app.modules.approvals.api import facade as approvals_api

    return approvals_api.reject(platform, auth, ticket_id, reason=reason)


def cancel_ticket(platform, auth, ticket_id: str, reason: str | None = None) -> dict:
    """Cancel via application service (domain transition + applicant/approve perm)."""
    from app.modules.approvals.api import facade as approvals_api

    return approvals_api.cancel(platform, auth, ticket_id, reason=reason)


def approve_ticket(platform, auth, ticket_id: str, reason: str | None = None) -> dict:
    """pending → approved. Does not run side effects (approve ≠ execute)."""
    from app.modules.approvals.api import facade as approvals_api

    return approvals_api.approve(platform, auth, ticket_id, execute=False, reason=reason)


def revalidate_ticket(platform, auth, ticket: dict) -> dict:
    """Re-check applicant, tenant, tool registration, roles, and agent enablement."""
    if ticket["tenant_id"] != auth.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    tool = get_tool(ticket.get("tool_name") or "")
    if tool is None:
        ticket["status"] = "failed"
        ticket["reason"] = "tool_unregistered"
        ticket["execution_result"] = "revalidation failed: tool missing"
        raise HTTPException(status_code=409, detail="revalidation failed: tool missing")

    applicant = next((u for u in platform.users.values() if u["id"] == ticket["applicant_id"]), None)
    if not applicant or applicant["status"] != "active" or applicant["tenant_id"] != ticket["tenant_id"]:
        ticket["status"] = "failed"
        ticket["reason"] = "applicant_invalid"
        raise HTTPException(status_code=409, detail="revalidation failed: applicant invalid")

    required = tool.get("required_roles") or []
    applicant_roles = set(applicant.get("roles") or [])
    if required and not any(role in applicant_roles for role in required):
        ticket["status"] = "failed"
        ticket["reason"] = "permission_revoked"
        raise HTTPException(status_code=409, detail="revalidation failed: permission revoked")

    agent_id = ticket.get("agent_id")
    agent = platform.agents.get(agent_id) if agent_id else None
    if agent is None:
        try:
            agent = platform.default_agent(ticket["tenant_id"])
        except KeyError as exc:
            ticket["status"] = "failed"
            ticket["reason"] = "agent_missing"
            raise HTTPException(status_code=409, detail="revalidation failed: agent missing") from exc
    if agent["tenant_id"] != ticket["tenant_id"]:
        ticket["status"] = "failed"
        ticket["reason"] = "agent_tenant_mismatch"
        raise HTTPException(status_code=409, detail="revalidation failed: agent tenant")
    if ticket["tool_name"] not in agent.get("enabled_tools", []):
        ticket["status"] = "failed"
        ticket["reason"] = "tool_disabled"
        raise HTTPException(status_code=409, detail="revalidation failed: tool disabled")

    ticket["status"] = "revalidated"
    ticket["revalidated_at"] = _now()
    ticket["revalidated_by"] = auth.user_id
    _persist(ticket)
    return ticket


def execute_ticket(platform, auth, ticket_id: str) -> dict:
    """Compatibility entry: orchestration lives in ApprovalApplicationService.execute."""
    from app.modules.approvals.api import facade as approvals_api

    return approvals_api.execute(platform, auth, ticket_id)


def approve_and_optionally_execute(platform, auth, ticket_id: str, *, execute: bool = False, reason: str | None = None) -> dict:
    """Approve; optionally execute (execute=false keeps MVP contract). Approve ≠ execute."""
    from app.modules.approvals.api import facade as approvals_api

    return approvals_api.approve(platform, auth, ticket_id, execute=execute, reason=reason)
