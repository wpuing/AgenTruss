"""Execution reliability: uncertain outcomes, reconcile, no blind retry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from app.agents.recovery import recover_on_startup
from app.modules.approvals.application.dto import (
    ActorContext,
    ExecuteCommand,
    ReconcileCommand,
)
from app.modules.approvals.application.services import ApprovalApplicationService
from app.modules.approvals.domain.enums import TicketStatus
from app.modules.approvals.domain.exceptions import (
    TicketConflictError,
    TicketNotExecutableError,
)


class FakeStore:
    def __init__(self, tickets: dict[str, dict[str, Any]]) -> None:
        self.tickets = tickets
        self.save_calls = 0
        self.fail_on_save_status: str | None = None

    def get(self, tenant_id: str, ticket_id: str) -> dict[str, Any]:
        t = self.tickets.get(ticket_id)
        if not t or t.get("tenant_id") != tenant_id:
            raise LookupError("ticket not found")
        return dict(t)

    def save(self, ticket: dict[str, Any]) -> None:
        self.save_calls += 1
        if self.fail_on_save_status and ticket.get("status") == self.fail_on_save_status:
            raise RuntimeError("injected_persist_failure")
        self.tickets[ticket["id"]] = dict(ticket)

    def claim_executing(self, ticket_id: str, tenant_id: str) -> bool:
        t = self.get(tenant_id, ticket_id)
        if t["status"] not in {"approved", "revalidated"}:
            return False
        t["status"] = "executing"
        self.save(t)
        return True

    def find_succeeded_idempotent(self, **kwargs):
        return None


class StubRevalidation:
    def revalidate(self, ticket, *, actor_id, tenant_id):
        ticket = dict(ticket)
        ticket["status"] = "revalidated"
        return ticket


class StubArgs:
    def verify_integrity(self, ticket):
        return True

    def load_args(self, ticket):
        return {"order_id": "1"}


class StubExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def tool_registered(self, tool_name: str) -> bool:
        return True

    def tool_scope(self, tool_name: str) -> str | None:
        return None

    def run(self, tool_name: str, args: dict, ctx: dict) -> str:
        self.calls += 1
        return "tool_ok"


def _actor() -> ActorContext:
    return ActorContext(
        user_id="admin",
        tenant_id="demo",
        perms=frozenset({"ticket:risk:approve"}),
    )


def test_tool_success_then_persist_failure_leaves_executing_and_side_effect():
    """Window: tool ran, DB failed to write succeeded — status stays executing."""
    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "approved",
                "applicant_id": "u1",
                "tool_name": "refund_order",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )
    store.fail_on_save_status = "succeeded"
    executor = StubExecutor()
    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=executor,
    )
    with pytest.raises(RuntimeError, match="injected_persist_failure"):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert executor.calls == 1
    assert store.tickets["t1"]["status"] == "executing"


def test_recovery_marks_reconciliation_not_failed(monkeypatch):
    platform = MagicMock()
    platform.users = {"admin": {"id": "admin", "tenant_id": "demo"}}
    platform.tickets = {
        "t1": {
            "id": "t1",
            "tenant_id": "demo",
            "status": "executing",
            "tool_name": "refund_order",
            "applicant_id": "u1",
        }
    }
    monkeypatch.setattr("app.agents.recovery.persistence_enabled", lambda: True)
    monkeypatch.setattr("app.agents.recovery.load_all_into", lambda p: 1)
    monkeypatch.setattr("app.agents.recovery.list_paused_for_recovery", lambda: [])
    monkeypatch.setattr("app.infra.entity_repo.load_users_into", lambda p: 1)
    monkeypatch.setattr("app.infra.entity_repo.load_kb_meta_into", lambda p: 0)

    def fake_upsert(ticket):
        platform.tickets[ticket["id"]] = dict(ticket)

    monkeypatch.setattr("app.infra.ticket_repo.upsert_ticket", fake_upsert)

    report = recover_on_startup(platform)
    assert "t1" in report["interrupted_executing"]
    assert platform.tickets["t1"]["status"] == "reconciliation_required"
    assert platform.tickets["t1"]["reason"] == "outcome_uncertain_after_restart"
    result = platform.tickets["t1"].get("execution_result") or ""
    assert "unknown" in result
    assert "decision=reconcile_only" in result


def test_execute_uncertain_on_refund_marks_reconciliation_not_failed():
    """Adapter-classified uncertain outcome → reconciliation_required (not failed)."""
    from app.modules.approvals.domain.exceptions import OutcomeUncertainError

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "approved",
                "applicant_id": "u1",
                "tool_name": "refund_order",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )

    class UncertainExecutor(StubExecutor):
        def run(self, tool_name: str, args: dict, ctx: dict) -> str:
            self.calls += 1
            raise OutcomeUncertainError(
                "tool_outcome_unknown: upstream timed out;decision=reconcile_only"
            )

    executor = UncertainExecutor()
    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=executor,
    )
    with pytest.raises(OutcomeUncertainError):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert executor.calls == 1
    assert store.tickets["t1"]["status"] == "reconciliation_required"
    assert store.tickets["t1"]["executed"] is False
    assert "decision=reconcile_only" in (store.tickets["t1"].get("execution_result") or "")


def test_platform_executor_timeout_raises_outcome_uncertain():
    """Infrastructure adapter maps refund timeout → OutcomeUncertainError."""
    from app.modules.approvals.domain.exceptions import OutcomeUncertainError
    from app.modules.approvals.infrastructure.adapters import PlatformToolExecutor

    exe = PlatformToolExecutor()
    real = __import__("app.tools.registry", fromlist=["get_tool"]).get_tool("refund_order")
    assert real is not None
    original = real["handler"]

    def boom(_args, _ctx):
        raise TimeoutError("upstream timed out")

    real["handler"] = boom
    try:
        with pytest.raises(OutcomeUncertainError) as caught:
            exe.run("refund_order", {"order_id": "1"}, {"owner_user_id": "u1"})
        assert "decision=reconcile_only" in caught.value.message
    finally:
        real["handler"] = original


def test_execute_business_error_on_refund_still_failed():
    """Confirmed tool error (non-transport) stays failed."""
    from app.modules.approvals.domain.exceptions import ExecutionFailedError

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "approved",
                "applicant_id": "u1",
                "tool_name": "refund_order",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )

    class BoomExecutor(StubExecutor):
        def run(self, tool_name: str, args: dict, ctx: dict) -> str:
            self.calls += 1
            raise RuntimeError("business_rejected")

    executor = BoomExecutor()
    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=executor,
    )
    with pytest.raises(ExecutionFailedError):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert store.tickets["t1"]["status"] == "failed"


def test_recovery_idempotent_on_already_reconciling(monkeypatch):
    """Second recovery scan must not rewrite reconciling tickets or auto-execute."""
    platform = MagicMock()
    platform.users = {"admin": {"id": "admin", "tenant_id": "demo"}}
    platform.tickets = {
        "t1": {
            "id": "t1",
            "tenant_id": "demo",
            "status": "reconciliation_required",
            "tool_name": "refund_order",
            "applicant_id": "u1",
            "reason": "outcome_uncertain_after_restart",
            "execution_result": "side_effect_unknown;decision=reconcile_only",
        }
    }
    monkeypatch.setattr("app.agents.recovery.persistence_enabled", lambda: True)
    monkeypatch.setattr("app.agents.recovery.load_all_into", lambda p: 1)
    monkeypatch.setattr("app.agents.recovery.list_paused_for_recovery", lambda: [])
    monkeypatch.setattr("app.infra.entity_repo.load_users_into", lambda p: 1)
    monkeypatch.setattr("app.infra.entity_repo.load_kb_meta_into", lambda p: 0)
    upserts: list[str] = []

    def track_upsert(ticket):
        upserts.append(ticket["id"])
        platform.tickets[ticket["id"]] = dict(ticket)

    monkeypatch.setattr("app.infra.ticket_repo.upsert_ticket", track_upsert)

    report = recover_on_startup(platform)
    assert "t1" in report["reconciliation_required"]
    assert report["interrupted_executing"] == []
    assert upserts == []
    assert platform.tickets["t1"]["status"] == "reconciliation_required"


def test_late_worker_cannot_regress_hard_terminal():
    """Upsert mode: succeeded stays immutable against late executing/complete writes."""
    from app.infra.ticket_repo import _upsert_write_mode

    assert _upsert_write_mode("succeeded", "executing") == "noop"
    assert _upsert_write_mode("succeeded", "failed") == "noop"
    assert _upsert_write_mode("succeeded", "reconciliation_required") == "noop"
    assert _upsert_write_mode("failed", "succeeded") == "noop"


def test_reconcile_success_does_not_call_executor():
    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "applicant_id": "u1",
                "tool_name": "refund_order",
                "idempotency_key": "k1",
                "executed": False,
                "execution_result": "side_effect_unknown_manual_reconcile_required",
            }
        }
    )
    executor = StubExecutor()
    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=executor,
    )
    with pytest.raises(TicketNotExecutableError, match="reconciliation"):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert executor.calls == 0

    out = svc.reconcile(
        ReconcileCommand(
            ticket_id="t1",
            actor=_actor(),
            outcome="succeeded",
            note="verified_external",
        )
    )
    assert out.ticket["status"] == TicketStatus.SUCCEEDED.value
    assert out.ticket["executed"] is True
    assert executor.calls == 0

    # Idempotent same-outcome reconcile
    again = svc.reconcile(
        ReconcileCommand(ticket_id="t1", actor=_actor(), outcome="succeeded")
    )
    assert again.ticket["status"] == "succeeded"
    with pytest.raises(TicketConflictError, match="terminal"):
        svc.reconcile(
            ReconcileCommand(ticket_id="t1", actor=_actor(), outcome="failed")
        )


def test_reconcile_failed_blocks_execute():
    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "applicant_id": "u1",
                "tool_name": "refund_order",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )
    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=StubExecutor(),
    )
    svc.reconcile(
        ReconcileCommand(ticket_id="t1", actor=_actor(), outcome="failed", note="no_fx")
    )
    assert store.tickets["t1"]["status"] == "failed"
    with pytest.raises(TicketNotExecutableError):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))


class _CountingLookup:
    def __init__(self, outcome):
        self.calls = 0
        self._outcome = outcome

    def lookup(self, ref):
        self.calls += 1
        return self._outcome


def test_lookup_resolve_success_never_calls_executor(monkeypatch):
    from app.modules.approvals.application.dto import LookupResolveCommand
    from app.tools import reliability as rel
    from app.tools.reliability import ToolCapability, ToolOutcome, ToolOutcomeKind

    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _name: ToolCapability(
            name="pay_ext",
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
                "applicant_id": "u1",
                "tool_name": "pay_ext",
                "idempotency_key": "k1",
                "tool_request_id": "ext-9",
                "executed": False,
            }
        }
    )
    executor = StubExecutor()
    lookup = _CountingLookup(
        ToolOutcome(kind=ToolOutcomeKind.SUCCEEDED, evidence="provider=paid")
    )
    from app.modules.approvals.infrastructure.adapters import ReliabilityOutcomePolicy

    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=StubArgs(),
        executor=executor,
        lookup=lookup,
        outcome_policy=ReliabilityOutcomePolicy(),
    )
    out = svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=_actor()))
    assert out.ticket["status"] == "succeeded"
    assert out.ticket["executed"] is True
    assert lookup.calls == 1
    assert executor.calls == 0


def test_lookup_resolve_transport_error_keeps_reconciling_no_execute():
    """Lookup TimeoutError must not become failed / must not re-run tool."""
    import app.tools.reliability as rel
    from app.modules.approvals.application.dto import LookupResolveCommand
    from app.modules.approvals.infrastructure.adapters import ReliabilityOutcomePolicy
    from app.tools.reliability import ToolCapability

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "applicant_id": "u1",
                "tool_name": "pay_ext",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )
    executor = StubExecutor()

    class BoomLookup:
        def lookup(self, ref):  # noqa: ANN001
            raise TimeoutError("lookup timed out")

    orig = rel.capability_for

    def _cap(name: str):
        if name == "pay_ext":
            return ToolCapability(
                name="pay_ext",
                has_side_effects=True,
                supports_idempotency_key=True,
                supports_result_lookup=True,
                high_risk=True,
            )
        return orig(name)

    rel.capability_for = _cap  # type: ignore[assignment]
    try:
        svc = ApprovalApplicationService(
            store,
            executor=executor,
            lookup=BoomLookup(),
            outcome_policy=ReliabilityOutcomePolicy(),
        )
        out = svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=_actor()))
        assert out.ticket["status"] == "reconciliation_required"
        assert out.ticket.get("reason") == "lookup_transport_uncertain"
        assert "lookup_failed" in str(out.ticket.get("execution_result") or "")
        assert executor.calls == 0
    finally:
        rel.capability_for = orig  # type: ignore[assignment]


def test_lookup_resolve_unknown_keeps_reconciling_no_execute():
    import app.tools.reliability as rel
    from app.modules.approvals.application.dto import LookupResolveCommand
    from app.tools.reliability import ToolCapability, ToolOutcome, ToolOutcomeKind

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "applicant_id": "u1",
                "tool_name": "pay_ext",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )
    executor = StubExecutor()
    lookup = _CountingLookup(
        ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="pending")
    )

    # Patch capability_for on the module used by the service
    orig = rel.capability_for

    def _cap(name):
        if name == "pay_ext":
            return ToolCapability(
                name="pay_ext",
                has_side_effects=True,
                supports_idempotency_key=True,
                supports_result_lookup=True,
                high_risk=True,
            )
        return orig(name)

    rel.capability_for = _cap  # type: ignore[assignment]
    try:
        from app.modules.approvals.infrastructure.adapters import ReliabilityOutcomePolicy

        svc = ApprovalApplicationService(
            store,
            executor=executor,
            lookup=lookup,
            outcome_policy=ReliabilityOutcomePolicy(),
        )
        out = svc.lookup_resolve(LookupResolveCommand(ticket_id="t1", actor=_actor()))
        assert out.ticket["status"] == "reconciliation_required"
        assert executor.calls == 0
        assert lookup.calls == 1
    finally:
        rel.capability_for = orig  # type: ignore[assignment]


def test_crash_before_tool_run_zero_side_effects():
    """CAS claimed then args decrypt fails — executor never runs."""

    class BadArgs:
        def verify_integrity(self, ticket):
            return True

        def load_args(self, ticket):
            raise ValueError("decrypt_boom")

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "approved",
                "applicant_id": "u1",
                "tool_name": "refund_order",
                "idempotency_key": "k1",
                "executed": False,
            }
        }
    )
    executor = StubExecutor()
    svc = ApprovalApplicationService(
        store,
        revalidation=StubRevalidation(),
        args_port=BadArgs(),
        executor=executor,
    )
    with pytest.raises(TicketNotExecutableError, match="decrypt"):
        svc.execute(ExecuteCommand(ticket_id="t1", actor=_actor()))
    assert executor.calls == 0
    assert store.tickets["t1"]["status"] == "failed"


def test_double_recovery_concurrent_idempotent(monkeypatch):
    """Two recovery scans on same reconciling ticket must not re-execute."""
    platform = MagicMock()
    platform.tickets = {
        "t1": {
            "id": "t1",
            "tenant_id": "demo",
            "status": "reconciliation_required",
            "tool_name": "refund_order",
            "executed": False,
        }
    }
    platform.users = {}
    monkeypatch.setattr("app.agents.recovery.persistence_enabled", lambda: True)
    monkeypatch.setattr("app.agents.recovery.load_all_into", lambda p: 1)
    monkeypatch.setattr("app.agents.recovery.list_paused_for_recovery", lambda: [])
    upserts: list[dict] = []
    monkeypatch.setattr(
        "app.infra.ticket_repo.upsert_ticket",
        lambda t: upserts.append(dict(t)),
    )
    r1 = recover_on_startup(platform)
    r2 = recover_on_startup(platform)
    assert r1["interrupted_executing"] == []
    assert r2["interrupted_executing"] == []
    assert platform.tickets["t1"]["status"] == "reconciliation_required"
    assert upserts == []


def test_recovery_lookup_first_marks_succeeded_without_execute(monkeypatch):
    import app.tools.reliability as rel
    from app.tools import lookup_registry
    from app.tools.reliability import (
        ToolCapability,
        ToolOutcome,
        ToolOutcomeKind,
    )

    lookup_registry.clear_lookups()
    lookup_registry.register_lookup(
        "pay_ext",
        lambda _ref: ToolOutcome(kind=ToolOutcomeKind.SUCCEEDED, evidence="ext_ok"),
    )
    orig = rel.capability_for

    def _cap(name):
        if name == "pay_ext":
            return ToolCapability(
                name="pay_ext",
                has_side_effects=True,
                supports_idempotency_key=True,
                supports_result_lookup=True,
                high_risk=True,
            )
        return orig(name)

    rel.capability_for = _cap  # type: ignore[assignment]
    platform = MagicMock()
    platform.tickets = {
        "t1": {
            "id": "t1",
            "tenant_id": "demo",
            "status": "executing",
            "tool_name": "pay_ext",
            "idempotency_key": "k1",
            "tool_request_id": "ext-1",
            "executed": False,
        }
    }
    platform.users = {}
    monkeypatch.setattr("app.agents.recovery.persistence_enabled", lambda: True)
    monkeypatch.setattr("app.agents.recovery.load_all_into", lambda p: 1)
    monkeypatch.setattr("app.agents.recovery.list_paused_for_recovery", lambda: [])
    monkeypatch.setattr("app.infra.ticket_repo.upsert_ticket", lambda t: None)
    try:
        report = recover_on_startup(platform)
        assert "t1" in report["interrupted_executing"]
        assert platform.tickets["t1"]["status"] == "succeeded"
        assert platform.tickets["t1"]["executed"] is True
    finally:
        rel.capability_for = orig  # type: ignore[assignment]
        lookup_registry.clear_lookups()
