"""Application execute orchestration: CAS before side effects, concurrency."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from app.modules.approvals.application.dto import ActorContext, ExecuteCommand
from app.modules.approvals.application.services import ApprovalApplicationService
from app.modules.approvals.domain.exceptions import (
    ExecutionFailedError,
    TicketConflictError,
    TicketNotExecutableError,
)


class FakeStore:
    def __init__(self, tickets: dict[str, dict[str, Any]]) -> None:
        self.tickets = tickets
        self._claim_lock = threading.Lock()
        self.claim_attempts = 0
        self.claim_wins = 0

    def get(self, tenant_id: str, ticket_id: str) -> dict[str, Any]:
        t = self.tickets.get(ticket_id)
        if not t or t.get("tenant_id") != tenant_id:
            raise LookupError("ticket not found")
        return t

    def save(self, ticket: dict[str, Any]) -> None:
        self.tickets[ticket["id"]] = ticket

    def claim_executing(self, ticket_id: str, tenant_id: str) -> bool:
        with self._claim_lock:
            self.claim_attempts += 1
            t = self.get(tenant_id, ticket_id)
            if t["status"] not in {"approved", "revalidated"}:
                return False
            t["status"] = "executing"
            self.save(t)
            self.claim_wins += 1
            return True

    def find_succeeded_idempotent(self, *, tenant_id, idempotency_key, exclude_ticket_id):
        return None


class StubRevalidation:
    def revalidate(self, ticket, *, actor_id, tenant_id):
        ticket = dict(ticket)
        ticket["status"] = "revalidated"
        ticket["revalidated_by"] = actor_id
        return ticket


class StubArgs:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok

    def verify_integrity(self, ticket):
        return self.ok

    def load_args(self, ticket):
        return {"order_id": "1"}


class StubExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def tool_registered(self, tool_name: str) -> bool:
        return True

    def tool_scope(self, tool_name: str) -> str | None:
        return None

    def run(self, tool_name: str, args: dict[str, Any], context: dict[str, Any]) -> Any:
        self.calls.append(tool_name)
        if self.fail:
            raise RuntimeError("boom")
        return {"ok": True}


def _approved(**extra: Any) -> dict[str, Any]:
    base = {
        "id": "t1",
        "tenant_id": "demo",
        "status": "approved",
        "applicant_id": "u1",
        "tool_name": "demo_refund",
        "executed": False,
        "idempotency_key": "k1",
        "args_encrypted": "x",
        "args_digest": "y",
    }
    base.update(extra)
    return base


def _actor() -> ActorContext:
    return ActorContext(
        user_id="admin",
        tenant_id="demo",
        perms=frozenset({"ticket:risk:approve"}),
    )


def _svc(store, executor=None, args=None):
    return ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=args or StubArgs(),
        executor=executor or StubExecutor(),
    )


def test_execute_happy_path_claims_then_runs():
    store = FakeStore({"t1": _approved()})
    ex = StubExecutor()
    out = _svc(store, ex).execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert out.ticket["status"] == "succeeded"
    assert out.ticket["executed"] is True
    assert ex.calls == ["demo_refund"]
    assert store.claim_wins == 1


def test_cas_failure_does_not_call_executor():
    store = FakeStore({"t1": _approved(status="revalidated")})

    def always_lose(ticket_id, tenant_id):
        store.claim_attempts += 1
        return False

    store.claim_executing = always_lose  # type: ignore[method-assign]
    ex = StubExecutor()
    with pytest.raises(TicketConflictError):
        _svc(store, ex).execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert ex.calls == []


def test_concurrent_execute_only_one_runs_tool():
    store = FakeStore({"t1": _approved()})
    ex = StubExecutor()
    svc = _svc(store, ex)
    errors: list[BaseException] = []

    def attempt():
        try:
            svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: attempt(), range(8)))

    # Exactly one side-effect; losers either conflict mid-race or idempotent-hit after success.
    assert len(ex.calls) == 1
    assert store.claim_wins == 1
    assert store.tickets["t1"]["status"] == "succeeded"
    assert all(
        isinstance(e, (TicketConflictError, TicketNotExecutableError)) for e in errors
    )


def test_pending_does_not_call_executor():
    store = FakeStore({"t1": _approved(status="pending")})
    ex = StubExecutor()
    with pytest.raises(TicketNotExecutableError):
        _svc(store, ex).execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert ex.calls == []
    assert store.claim_attempts == 0


def test_cancelled_blocked():
    store = FakeStore({"t1": _approved(status="cancelled")})
    ex = StubExecutor()
    with pytest.raises(TicketNotExecutableError):
        _svc(store, ex).execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert ex.calls == []


def test_integrity_fail_no_claim():
    store = FakeStore({"t1": _approved()})
    ex = StubExecutor()
    with pytest.raises(TicketNotExecutableError, match="integrity"):
        _svc(store, ex, args=StubArgs(ok=False)).execute(
            ExecuteCommand(ticket_id="t1", actor=_actor())
        )
    assert ex.calls == []
    assert store.tickets["t1"]["status"] == "failed"


def test_tool_failure_marks_failed():
    store = FakeStore({"t1": _approved()})
    ex = StubExecutor()
    ex.fail = True
    with pytest.raises(ExecutionFailedError):
        _svc(store, ex).execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert store.tickets["t1"]["status"] == "failed"
    assert store.tickets["t1"]["executed"] is False


def test_idempotent_succeeded_skips_executor():
    store = FakeStore({"t1": _approved(status="succeeded", executed=True)})
    ex = StubExecutor()
    out = _svc(store, ex).execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert out.ticket["status"] == "succeeded"
    assert ex.calls == []
