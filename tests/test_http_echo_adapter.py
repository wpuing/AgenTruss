"""Local HTTP echo Adapter: real urllib I/O + durable lookup (in-process server)."""

from __future__ import annotations

import uuid

import pytest

from app.modules.approvals.application.dto import ActorContext, LookupResolveCommand
from app.modules.approvals.application.services import ApprovalApplicationService
from app.modules.approvals.infrastructure.adapters import ReliabilityOutcomePolicy
from app.tools import lookup_registry
from app.tools.http_echo_adapter import (
    get_echo,
    lookup_ref,
    post_echo,
    start_local_echo_server,
    stop_local_echo_server,
)
from app.tools.reliability import (
    ToolCapability,
    ToolExecutionRef,
    ToolOutcomeKind,
    resolve_after_uncertain,
)
from tests.test_execution_reliability import FakeStore, StubExecutor


@pytest.fixture()
def echo_base(monkeypatch):
    base = start_local_echo_server()
    monkeypatch.setenv("HTTP_ECHO_BASE_URL", base)
    monkeypatch.setenv("RUN_HTTP_ECHO_TOOL", "1")
    yield base
    stop_local_echo_server()
    lookup_registry.clear_lookups()


def test_echo_post_then_lookup_success(echo_base):
    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "ok", "order_id": "1"})
    out = get_echo(op)
    assert out.kind == ToolOutcomeKind.SUCCEEDED
    assert "echo_status=ok" in out.evidence


def test_echo_lookup_failed_status(echo_base):
    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "declined"})
    out = get_echo(op)
    assert out.kind == ToolOutcomeKind.FAILED


def test_echo_lookup_pending_stays_unknown(echo_base):
    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "pending"})
    out = get_echo(op)
    assert out.kind == ToolOutcomeKind.UNKNOWN


def test_echo_lookup_not_found_is_unknown_not_failed(echo_base):
    out = get_echo(f"missing-{uuid.uuid4().hex[:8]}")
    assert out.kind == ToolOutcomeKind.UNKNOWN
    assert out.evidence == "echo_not_found"


def test_echo_resolve_after_uncertain_marks_succeeded(echo_base, monkeypatch):
    import app.tools.reliability as rel

    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "ok"})
    lookup_registry.register_lookup("http_echo", lookup_ref)
    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _n: ToolCapability(
            name="http_echo",
            has_side_effects=True,
            supports_idempotency_key=True,
            supports_result_lookup=True,
            high_risk=True,
        ),
    )
    ref = ToolExecutionRef(
        logical_execution_id="t1",
        tenant_id="demo",
        tool_name="http_echo",
        tool_request_id=op,
    )
    action, outcome = resolve_after_uncertain(
        rel.capability_for("http_echo"),
        ref,
        lookup=lookup_ref,
    )
    from app.tools.reliability import ResolveAction

    assert action == ResolveAction.MARK_SUCCEEDED
    assert outcome.kind == ToolOutcomeKind.SUCCEEDED


def test_lookup_resolve_via_echo_never_calls_executor(echo_base, monkeypatch):
    import app.tools.reliability as rel

    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "ok"})
    lookup_registry.register_lookup("http_echo", lookup_ref)
    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _n: ToolCapability(
            name="http_echo",
            has_side_effects=True,
            supports_idempotency_key=True,
            supports_result_lookup=True,
            high_risk=True,
        ),
    )
    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "tool_name": "http_echo",
                "tool_request_id": op,
                "idempotency_key": op,
                "executed": False,
                "applicant_id": "u1",
            }
        }
    )
    executor = StubExecutor()

    class _Lookup:
        def lookup(self, ref):
            return lookup_ref(ref)

    svc = ApprovalApplicationService(
        store,
        executor=executor,
        lookup=_Lookup(),
        outcome_policy=ReliabilityOutcomePolicy(),
    )
    out = svc.lookup_resolve(
        LookupResolveCommand(
            ticket_id="t1",
            actor=ActorContext(
                user_id="admin",
                tenant_id="demo",
                perms=frozenset({"ticket:risk:approve"}),
            ),
        )
    )
    assert out.ticket["status"] == "succeeded"
    assert executor.calls == 0


def test_second_lookup_resolve_conflicts_after_success(echo_base, monkeypatch):
    import app.tools.reliability as rel
    from app.modules.approvals.domain.exceptions import TicketConflictError

    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "ok"})
    lookup_registry.register_lookup("http_echo", lookup_ref)
    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _n: ToolCapability(
            name="http_echo",
            has_side_effects=True,
            supports_idempotency_key=True,
            supports_result_lookup=True,
            high_risk=True,
        ),
    )
    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "tool_name": "http_echo",
                "tool_request_id": op,
                "idempotency_key": op,
                "executed": False,
                "applicant_id": "u1",
            }
        }
    )

    class _Lookup:
        def lookup(self, ref):
            return lookup_ref(ref)

    svc = ApprovalApplicationService(
        store,
        executor=StubExecutor(),
        lookup=_Lookup(),
        outcome_policy=ReliabilityOutcomePolicy(),
    )
    actor = ActorContext(
        user_id="admin",
        tenant_id="demo",
        perms=frozenset({"ticket:risk:approve"}),
    )
    assert svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=actor)).ticket[
        "status"
    ] == "succeeded"
    with pytest.raises(TicketConflictError, match="terminal"):
        svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=actor))


def test_concurrent_lookup_resolve_single_success(echo_base, monkeypatch):
    """Two overlapping lookups: both may call external GET; final status succeeded once."""
    import threading

    import app.tools.reliability as rel
    from app.modules.approvals.domain.exceptions import TicketConflictError

    op = f"op-{uuid.uuid4().hex[:12]}"
    post_echo(op, {"status": "ok"})
    lookup_registry.register_lookup("http_echo", lookup_ref)
    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _n: ToolCapability(
            name="http_echo",
            has_side_effects=True,
            supports_idempotency_key=True,
            supports_result_lookup=True,
            high_risk=True,
        ),
    )

    class ThreadSafeStore(FakeStore):
        def __init__(self, tickets):
            super().__init__(tickets)
            self._lock = threading.Lock()

        def get(self, tenant_id: str, ticket_id: str):
            with self._lock:
                return super().get(tenant_id, ticket_id)

        def save(self, ticket):
            with self._lock:
                # Reject clobber of hard terminal (simulates CAS-ish protection).
                cur = self.tickets.get(ticket["id"])
                if cur and cur.get("status") in {"succeeded", "failed"}:
                    if ticket.get("status") != cur["status"]:
                        raise TicketConflictError("ticket already in terminal state")
                return super().save(ticket)

    store = ThreadSafeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "tool_name": "http_echo",
                "tool_request_id": op,
                "idempotency_key": op,
                "executed": False,
                "applicant_id": "u1",
            }
        }
    )
    lookups = {"n": 0}
    lock = threading.Lock()

    class _Lookup:
        def lookup(self, ref):
            with lock:
                lookups["n"] += 1
            return lookup_ref(ref)

    svc = ApprovalApplicationService(
        store,
        executor=StubExecutor(),
        lookup=_Lookup(),
        outcome_policy=ReliabilityOutcomePolicy(),
    )
    actor = ActorContext(
        user_id="admin",
        tenant_id="demo",
        perms=frozenset({"ticket:risk:approve"}),
    )
    errors: list[BaseException] = []

    def worker():
        try:
            svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=actor))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert store.tickets["t1"]["status"] == "succeeded"
    assert store.tickets["t1"]["executed"] is True
    assert lookups["n"] >= 1
    for exc in errors:
        assert isinstance(exc, TicketConflictError)


def test_execute_uncertain_then_echo_lookup_closes(echo_base, monkeypatch):
    """Execute times out → reconciliation_required → lookup succeeds without re-run."""
    from app.modules.approvals.application.dto import ExecuteCommand
    from app.modules.approvals.domain.exceptions import OutcomeUncertainError
    from tests.test_execution_reliability import StubArgs, StubRevalidation

    import app.tools.reliability as rel

    op = f"op-{uuid.uuid4().hex[:12]}"
    # External side effect already visible (simulates timeout after remote commit).
    post_echo(op, {"status": "ok"})
    lookup_registry.register_lookup("http_echo", lookup_ref)
    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _n: ToolCapability(
            name="http_echo",
            has_side_effects=True,
            supports_idempotency_key=True,
            supports_result_lookup=True,
            high_risk=True,
        ),
    )

    class UncertainOnce(StubExecutor):
        def run(self, tool_name: str, args: dict, ctx: dict) -> str:
            self.calls += 1
            raise OutcomeUncertainError(
                "tool_outcome_unknown: timed out;decision=lookup_first"
            )

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "approved",
                "tool_name": "http_echo",
                "tool_request_id": op,
                "idempotency_key": op,
                "executed": False,
                "applicant_id": "u1",
            }
        }
    )
    executor = UncertainOnce()

    class _Lookup:
        def lookup(self, ref):
            return lookup_ref(ref)

    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=executor,
        lookup=_Lookup(),
        outcome_policy=ReliabilityOutcomePolicy(),
    )
    actor = ActorContext(
        user_id="admin",
        tenant_id="demo",
        perms=frozenset({"ticket:risk:approve"}),
    )
    with pytest.raises(OutcomeUncertainError):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=actor))
    assert store.tickets["t1"]["status"] == "reconciliation_required"
    assert executor.calls == 1
    out = svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=actor))
    assert out.ticket["status"] == "succeeded"
    assert executor.calls == 1  # no re-execute

