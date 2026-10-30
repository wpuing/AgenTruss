"""Persist risk tickets to PostgreSQL when storage_backend=postgres."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.infra.db import get_engine, session_scope
from app.infra.models import TicketRow

logger = logging.getLogger(__name__)

# Test-only hook: called after row lock + load, before applying ticket fields.
# Must not be set in production. Used to prove lock serialization without sleep races.
_UPSERT_AFTER_LOCK_HOOK: Callable[[Any, TicketRow | None], None] | None = None

def _claimable_status_values() -> tuple[str, ...]:
    """Claimable statuses from domain — keep CAS aligned with transition rules."""
    from app.modules.approvals.domain.enums import CLAIMABLE_STATUSES

    return tuple(s.value for s in CLAIMABLE_STATUSES)


def persistence_enabled() -> bool:
    return get_settings().storage_backend == "postgres" and get_engine() is not None


def claim_ticket_executing(ticket_id: str, tenant_id: str) -> bool:
    """Database-level CAS: claim execution iff status is approved|revalidated.

    Returns True only for the winner. Safe across processes sharing one PostgreSQL.
    When persistence is disabled, caller must use process locks (returns True).
    """
    if not persistence_enabled():
        return True
    claimable = _claimable_status_values()
    with session_scope() as session:
        result = session.execute(
            update(TicketRow)
            .where(
                TicketRow.id == ticket_id,
                TicketRow.tenant_id == tenant_id,
                TicketRow.status.in_(claimable),
            )
            .values(status="executing")
        )
        # DML rowcount exists at runtime; Result stubs may omit the attribute (mypy 2.x / SA).
        claimed = int(getattr(result, "rowcount", 0) or 0) == 1
        if claimed:
            logger.info("ticket claimed executing id=%s tenant=%s", ticket_id, tenant_id)
        return claimed


# Status sets aligned with domain enums (string values for row comparisons).
_HARD_TERMINAL = frozenset({"succeeded", "failed", "rejected", "cancelled", "expired"})
_EXECUTION_OUTCOMES = frozenset({"succeeded", "failed", "reconciliation_required"})


def _upsert_write_mode(db_status: str, new_status: str) -> str:
    """Classify how ``upsert_ticket`` may mutate an existing row.

    Returns:
        ``full`` — pre-claim lifecycle (identity + status).
        ``complete`` — ``executing`` → outcome / reconciliation (integrity frozen).
        ``reconcile`` — ``reconciliation_required`` → ``succeeded``/``failed``.
        ``busy_meta`` — still ``executing``; correlation only, no execution overwrite.
        ``noop`` — refuse (hard terminal, stale claimable, CAS bypass, illegal regress).
    """
    if db_status in _HARD_TERMINAL:
        return "noop"
    if db_status == "reconciliation_required":
        if new_status in {"succeeded", "failed"}:
            return "reconcile"
        return "noop"
    if db_status == "executing":
        if new_status in _EXECUTION_OUTCOMES:
            return "complete"
        if new_status == "executing":
            return "busy_meta"
        return "noop"
    # Claimable / other non-terminal: entering executing is CAS-only.
    if new_status == "executing":
        return "noop"
    return "full"


def upsert_ticket(ticket: dict) -> None:
    """Persist ticket with row lock; DB status is authoritative across processes.

    Uses ``SELECT ... FOR UPDATE`` so checks see committed status (not a stale
    ORM cache), serializing against ``claim_ticket_executing`` on the same PK.

    Rules (Phase 5.12/5.13):
    - Hard terminal rows are immutable via upsert.
    - ``approved``/``revalidated`` → ``executing`` only via CAS, not upsert.
    - ``executing`` → ``succeeded``/``failed``/``reconciliation_required`` allowed.
    - ``reconciliation_required`` → ``succeeded``/``failed`` via reconcile only.
    - Tenant mismatch → ``TicketConflictError`` (no tenant reassignment).
    """
    if not persistence_enabled():
        return
    from app.modules.approvals.domain.exceptions import TicketConflictError

    with session_scope() as session:
        row = session.execute(
            select(TicketRow).where(TicketRow.id == ticket["id"]).with_for_update()
        ).scalar_one_or_none()
        if _UPSERT_AFTER_LOCK_HOOK is not None:
            _UPSERT_AFTER_LOCK_HOOK(session, row)
        if row is None:
            row = TicketRow(
                id=ticket["id"],
                tenant_id=ticket["tenant_id"],
                tool_name=ticket["tool_name"],
                status=ticket["status"],
                applicant_id=ticket.get("applicant_id") or ticket.get("user_id") or "",
                args_masked_json=json.dumps(ticket.get("args_masked") or {}, ensure_ascii=False),
                idempotency_key=ticket.get("idempotency_key") or ticket["id"],
            )
            session.add(row)
            _apply_ticket_fields(row, ticket, apply_execution=True, freeze_integrity=False)
            return

        if row.tenant_id != ticket["tenant_id"]:
            logger.warning(
                "ticket tenant conflict id=%s keep=%s reject=%s",
                ticket.get("id"),
                row.tenant_id,
                ticket["tenant_id"],
            )
            raise TicketConflictError("ticket tenant conflict")

        new_status = ticket["status"]
        mode = _upsert_write_mode(row.status, new_status)
        if mode == "noop":
            logger.info(
                "skip illegal upsert id=%s keep=%s reject=%s",
                ticket.get("id"),
                row.status,
                new_status,
            )
            return
        if mode == "busy_meta":
            _apply_ticket_fields(
                row, ticket, apply_execution=False, freeze_integrity=True
            )
            return
        if mode in {"complete", "reconcile"}:
            _apply_ticket_fields(
                row, ticket, apply_execution=True, freeze_integrity=True
            )
            return
        # full
        _apply_ticket_fields(
            row, ticket, apply_execution=True, freeze_integrity=False
        )


def _apply_ticket_fields(
    row: TicketRow,
    ticket: dict,
    *,
    apply_execution: bool,
    freeze_integrity: bool,
) -> None:
    """Apply ticket dict onto ORM row.

    ``freeze_integrity`` keeps tenant/tool/args/digest/idempotency/applicant/type/
    approver/decision/agent — winner completion may only attach correlation ids
    and (when ``apply_execution``) outcome fields.
    """
    if not freeze_integrity:
        row.tenant_id = ticket["tenant_id"]
        row.type = ticket.get("type") or "risk"
        row.applicant_id = ticket.get("applicant_id") or ticket.get("user_id") or ""
        row.approver_id = ticket.get("approver_id")
        row.agent_id = ticket.get("agent_id")
        row.tool_name = ticket["tool_name"]
        row.args_encrypted = ticket.get("args_encrypted")
        row.args_masked_json = json.dumps(ticket.get("args_masked") or {}, ensure_ascii=False)
        row.args_digest = ticket.get("args_digest")
        row.idempotency_key = ticket.get("idempotency_key") or ticket["id"]
        row.decision = ticket.get("decision")
        row.workflow_thread_id = ticket.get("workflow_thread_id")
        row.run_id = ticket.get("run_id")
        row.trace_id = ticket.get("trace_id")
    else:
        if ticket.get("workflow_thread_id") is not None:
            row.workflow_thread_id = ticket.get("workflow_thread_id")
        if ticket.get("run_id") is not None:
            row.run_id = ticket.get("run_id")
        if ticket.get("trace_id") is not None:
            row.trace_id = ticket.get("trace_id")
    if apply_execution:
        row.status = ticket["status"]
        row.executed = bool(ticket.get("executed"))
        row.reason = ticket.get("reason")
        row.execution_result = ticket.get("execution_result")
        if ticket.get("decided_at"):
            try:
                row.decided_at = datetime.fromisoformat(ticket["decided_at"])
            except ValueError:
                row.decided_at = datetime.now(timezone.utc)


def load_all_into(platform) -> int:
    """Hydrate in-memory ticket map from DB (restart recovery for pending work)."""
    if not persistence_enabled():
        return 0
    count = 0
    with session_scope() as session:
        rows = session.query(TicketRow).all()
        for row in rows:
            platform.tickets[row.id] = {
                "id": row.id,
                "type": row.type,
                "tenant_id": row.tenant_id,
                "status": row.status,
                "user_id": row.applicant_id,
                "applicant_id": row.applicant_id,
                "approver_id": row.approver_id,
                "agent_id": row.agent_id,
                "tool_name": row.tool_name,
                "args_encrypted": row.args_encrypted,
                "args": None,
                "args_masked": json.loads(row.args_masked_json or "{}"),
                "args_digest": row.args_digest,
                "idempotency_key": row.idempotency_key,
                "executed": row.executed,
                "decision": row.decision,
                "reason": row.reason,
                "execution_result": row.execution_result,
                "workflow_thread_id": row.workflow_thread_id,
                "run_id": row.run_id,
                "trace_id": row.trace_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            }
            count += 1
    logger.info("loaded %s tickets from postgres", count)
    return count


def list_paused_for_recovery() -> list[dict]:
    """Tickets awaiting human decision or approved-not-executed (restart scan)."""
    if not persistence_enabled():
        return []
    statuses = (
        "pending",
        "approved",
        "revalidated",
        "executing",
        "reconciliation_required",
    )
    out: list[dict] = []
    with session_scope() as session:
        rows = session.query(TicketRow).filter(TicketRow.status.in_(statuses)).all()
        for row in rows:
            out.append(
                {
                    "id": row.id,
                    "tenant_id": row.tenant_id,
                    "status": row.status,
                    "workflow_thread_id": row.workflow_thread_id,
                    "run_id": row.run_id,
                    "trace_id": row.trace_id,
                }
            )
    return out


def _row_to_ticket(row: TicketRow) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "tenant_id": row.tenant_id,
        "status": row.status,
        "user_id": row.applicant_id,
        "applicant_id": row.applicant_id,
        "approver_id": row.approver_id,
        "agent_id": row.agent_id,
        "tool_name": row.tool_name,
        "args_encrypted": row.args_encrypted,
        "args": None,
        "args_masked": json.loads(row.args_masked_json or "{}"),
        "args_digest": row.args_digest,
        "idempotency_key": row.idempotency_key,
        "executed": row.executed,
        "decision": row.decision,
        "reason": row.reason,
        "execution_result": row.execution_result,
        "workflow_thread_id": row.workflow_thread_id,
        "run_id": row.run_id,
        "trace_id": row.trace_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


def get_ticket_row(ticket_id: str, tenant_id: str) -> dict | None:
    """Authoritative ticket read from PostgreSQL."""
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = session.get(TicketRow, ticket_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return _row_to_ticket(row)


def list_tickets(tenant_id: str, *, ticket_type: str | None = "risk") -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        q = session.query(TicketRow).filter(TicketRow.tenant_id == tenant_id)
        if ticket_type:
            q = q.filter(TicketRow.type == ticket_type)
        return [_row_to_ticket(row) for row in q.all()]


def count_pending(tenant_id: str) -> int:
    if not persistence_enabled():
        return 0
    with session_scope() as session:
        return (
            session.query(TicketRow)
            .filter(TicketRow.tenant_id == tenant_id, TicketRow.status == "pending")
            .count()
        )
