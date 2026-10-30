"""Application-layer tests with Fake TicketStore (no DB / FastAPI TestClient)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.approvals.application.dto import (
    ActorContext,
    ApproveCommand,
    CancelCommand,
    RejectCommand,
)
from app.modules.approvals.application.services import ApprovalApplicationService
from app.modules.approvals.domain.exceptions import InvalidTransitionError


class FakeStore:
    def __init__(self, tickets: dict[str, dict[str, Any]] | None = None) -> None:
        self.tickets = tickets or {}
        self.saved: list[str] = []
        self.claims: list[tuple[str, str]] = []

    def get(self, tenant_id: str, ticket_id: str) -> dict[str, Any]:
        t = self.tickets.get(ticket_id)
        if not t or t.get("tenant_id") != tenant_id:
            raise LookupError("ticket not found")
        return t

    def save(self, ticket: dict[str, Any]) -> None:
        self.tickets[ticket["id"]] = ticket
        self.saved.append(ticket["id"])

    def claim_executing(self, ticket_id: str, tenant_id: str) -> bool:
        self.claims.append((ticket_id, tenant_id))
        t = self.get(tenant_id, ticket_id)
        if t["status"] not in {"approved", "revalidated"}:
            return False
        t["status"] = "executing"
        self.save(t)
        return True


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_ticket_event(self, **kwargs: Any) -> None:
        # Ensure no encrypted args leak into audit kwargs
        assert "args_encrypted" not in kwargs
        assert "args" not in kwargs
        self.events.append(kwargs)


def _pending(**extra: Any) -> dict[str, Any]:
    base = {
        "id": "t1",
        "tenant_id": "demo",
        "status": "pending",
        "applicant_id": "u1",
        "tool_name": "delete_order",
        "executed": False,
    }
    base.update(extra)
    return base


def _actor(*, user_id: str = "admin", perms: frozenset[str] | None = None) -> ActorContext:
    return ActorContext(
        user_id=user_id,
        tenant_id="demo",
        perms=(
            frozenset({"ticket:risk:approve", "ticket:risk:reject"})
            if perms is None
            else perms
        ),
    )


def test_approve_happy_path():
    store = FakeStore({"t1": _pending()})
    audit = RecordingAudit()
    svc = ApprovalApplicationService(store, audit=audit)
    result = svc.approve(ApproveCommand(ticket_id="t1", actor=_actor(), reason="ok"))
    assert result.ticket["status"] == "approved"
    assert result.ticket["executed"] is False
    assert result.ticket["approver_id"] == "admin"
    assert "args_encrypted" not in result.ticket or result.ticket.get("args_encrypted") is None
    assert audit.events[0]["action"] == "approve"
    assert "secret" not in str(audit.events)


def test_approve_repeat_conflict():
    store = FakeStore({"t1": _pending(status="approved")})
    svc = ApprovalApplicationService(store)
    with pytest.raises(InvalidTransitionError):
        svc.approve(ApproveCommand(ticket_id="t1", actor=_actor()))


def test_reject_happy_and_illegal():
    store = FakeStore({"t1": _pending()})
    svc = ApprovalApplicationService(store)
    assert svc.reject(RejectCommand(ticket_id="t1", actor=_actor())).ticket["status"] == "rejected"
    with pytest.raises(InvalidTransitionError):
        svc.reject(RejectCommand(ticket_id="t1", actor=_actor()))


def test_cancel_requires_applicant_or_approve_perm():
    store = FakeStore({"t1": _pending(applicant_id="u1")})
    svc = ApprovalApplicationService(store)
    with pytest.raises(PermissionError):
        svc.cancel(CancelCommand(ticket_id="t1", actor=_actor(user_id="other", perms=frozenset())))
    # applicant can cancel
    store2 = FakeStore({"t1": _pending(applicant_id="u1")})
    svc2 = ApprovalApplicationService(store2)
    out = svc2.cancel(CancelCommand(ticket_id="t1", actor=_actor(user_id="u1", perms=frozenset())))
    assert out.ticket["status"] == "cancelled"


def test_audit_failure_propagates():
    class BoomAudit:
        def record_ticket_event(self, **kwargs: Any) -> None:
            raise RuntimeError("audit down")

    store = FakeStore({"t1": _pending()})
    svc = ApprovalApplicationService(store, audit=BoomAudit())
    with pytest.raises(RuntimeError, match="audit down"):
        svc.approve(ApproveCommand(ticket_id="t1", actor=_actor()))
    # State was already saved before audit — document this boundary
    assert store.tickets["t1"]["status"] == "approved"
