"""JSONPlaceholder HTTPS integration tests (demo API, not production adapter).

Proves: real HTTPS POST/GET, status mapping (404→UNKNOWN), lookup_resolve does
not re-execute tools. Does **not** prove durable business side effects or
production result lookup. Gate: ``RUN_JSONPLACEHOLDER_TOOL=1``. Skip ≠ pass.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.modules.approvals.application.dto import ActorContext, LookupResolveCommand
from app.modules.approvals.application.services import ApprovalApplicationService
from app.modules.approvals.infrastructure.adapters import ReliabilityOutcomePolicy
from app.tools import lookup_registry
from app.tools.jsonplaceholder_adapter import create_post, lookup_post, lookup_ref
from app.tools.reliability import ToolCapability, ToolExecutionRef, ToolOutcomeKind
from tests.test_execution_reliability import FakeStore, StubExecutor

_RUN = os.getenv("RUN_JSONPLACEHOLDER_TOOL") == "1"

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set RUN_JSONPLACEHOLDER_TOOL=1 for real third-party HTTPS",
)


def test_jsonplaceholder_create_then_lookup_success():
    mark = f"agentruss-{uuid.uuid4().hex[:8]}"
    created = create_post(title=mark, body="phase519")
    oid = created["id"]
    out = lookup_post(oid)
    # Fake API: created id may still GET successfully for known range.
    assert out.kind in {ToolOutcomeKind.SUCCEEDED, ToolOutcomeKind.UNKNOWN}
    if out.kind == ToolOutcomeKind.SUCCEEDED:
        assert str(oid) in out.evidence or out.raw_result is not None


def test_jsonplaceholder_lookup_missing_is_unknown_not_failed():
    out = lookup_post(999_999_999)
    assert out.kind == ToolOutcomeKind.UNKNOWN


def test_lookup_resolve_with_jsonplaceholder(monkeypatch):
    import app.tools.reliability as rel

    mark = f"agentruss-{uuid.uuid4().hex[:8]}"
    created = create_post(title=mark, body="lookup")
    oid = str(created["id"])
    lookup_registry.clear_lookups()
    lookup_registry.register_lookup("jp_post", lookup_ref)
    monkeypatch.setattr(
        rel,
        "capability_for",
        lambda _n: ToolCapability(
            name="jp_post",
            has_side_effects=True,
            supports_idempotency_key=True,
            supports_result_lookup=True,
            high_risk=True,
        ),
    )

    class _Lookup:
        def lookup(self, ref):
            return lookup_ref(ref)

    store = FakeStore(
        {
            "t1": {
                "id": "t1",
                "tenant_id": "demo",
                "status": "reconciliation_required",
                "tool_name": "jp_post",
                "tool_request_id": oid,
                "idempotency_key": oid,
                "executed": False,
                "applicant_id": "u1",
            }
        }
    )
    executor = StubExecutor()
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
    assert executor.calls == 0
    # Succeeded if GET returned the post; else stay reconciling (UNKNOWN).
    assert out.ticket["status"] in {"succeeded", "reconciliation_required"}
    lookup_registry.clear_lookups()
