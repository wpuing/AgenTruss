"""Unit tests for tool reliability contract (no external services)."""

from __future__ import annotations

import pytest
from app.tools.registry import (
    CapabilityDeclarationError,
    _idempotency_key,
    validate_capability_meta,
)
from app.tools.reliability import (
    ResolveAction,
    RetryDecision,
    ToolCapability,
    ToolExecutionRef,
    ToolOutcome,
    ToolOutcomeKind,
    capability_for,
    decide_after_timeout,
    decide_after_uncertain,
    is_uncertain_transport_error,
    lookup_not_implemented,
    resolve_after_uncertain,
    same_logical_idempotency_key,
)


def test_demo_refund_capabilities():
    cap = capability_for("refund_order")
    assert cap is not None
    assert cap.has_side_effects is True
    assert cap.supports_idempotency_key is False
    assert cap.supports_result_lookup is False
    assert cap.capability_tier == "local_demo"
    assert decide_after_uncertain(cap) == RetryDecision.RECONCILE_ONLY
    assert decide_after_timeout(cap) == RetryDecision.RECONCILE_ONLY
    # Name "query" must not imply durable external result lookup.
    q = capability_for("query_order")
    assert q is not None
    assert q.supports_result_lookup is False
    assert q.capability_tier == "local_demo"


def test_demo_query_safe_retry():
    cap = capability_for("query_order")
    assert cap is not None
    assert cap.has_side_effects is False
    assert decide_after_uncertain(cap) == RetryDecision.SAFE_RETRY


def test_same_logical_key_stable():
    args = {"order_id": "1001", "owner_user_id": "u1"}
    key = _idempotency_key("demo", "refund_order", args)
    assert same_logical_idempotency_key(
        tenant_id="demo",
        tool_name="refund_order",
        clean_args=args,
        previous_key=key,
    )
    assert not same_logical_idempotency_key(
        tenant_id="demo",
        tool_name="refund_order",
        clean_args={**args, "order_id": "1002"},
        previous_key=key,
    )


def test_lookup_unknown_without_adapter():
    cap = capability_for("refund_order")
    assert cap is not None
    out = lookup_not_implemented(
        cap,
        ToolExecutionRef(
            logical_execution_id="tck1",
            tenant_id="demo",
            tool_name="refund_order",
            idempotency_key="abc",
        ),
    )
    assert out.kind == ToolOutcomeKind.UNKNOWN


def test_catalog_exposes_capability_flags():
    from app.tools.registry import public_tool_catalog

    by_name = {row["name"]: row for row in public_tool_catalog()}
    assert by_name["refund_order"]["has_side_effects"] is True
    assert by_name["refund_order"]["supports_idempotency_key"] is False
    assert by_name["refund_order"]["supports_result_lookup"] is False
    assert by_name["refund_order"]["capability_tier"] == "local_demo"
    assert by_name["query_order"]["has_side_effects"] is False
    assert by_name["query_order"]["capability_tier"] == "local_demo"
    # Honesty: demo tools must not advertise durable/production lookup.
    assert by_name["refund_order"]["capability_tier"] not in {
        "durable_lookup",
        "production",
    }


def test_capability_declaration_rejects_demo_lookup_and_bare_production():
    with pytest.raises(CapabilityDeclarationError):
        validate_capability_meta(
            {
                "name": "refund_order",
                "capability_tier": "local_demo",
                "supports_result_lookup": True,
            }
        )
    with pytest.raises(CapabilityDeclarationError):
        validate_capability_meta(
            {
                "name": "evil_prod",
                "capability_tier": "production",
                "supports_result_lookup": True,
                # missing production_acceptance_id
            }
        )
    with pytest.raises(CapabilityDeclarationError):
        validate_capability_meta({"name": "x", "capability_tier": "super_prod"})
    # Valid durable claim shape (registration still needs a real adapter elsewhere).
    assert (
        validate_capability_meta(
            {
                "name": "future_lookup",
                "capability_tier": "durable_lookup",
                "supports_result_lookup": True,
            }
        )
        == "durable_lookup"
    )


def test_tier_alone_does_not_force_lookup_first():
    """Recovery must not escalate Demo via a forged tier on ToolCapability."""
    forged = ToolCapability(
        name="refund_order",
        has_side_effects=True,
        supports_idempotency_key=False,
        supports_result_lookup=False,
        high_risk=True,
        capability_tier="production",  # dishonest label
    )
    assert decide_after_uncertain(forged) == RetryDecision.RECONCILE_ONLY


def test_unknown_tool_has_no_capability():
    assert capability_for("not_a_real_tool") is None


def test_timeout_decision_matches_uncertain_for_demo_refund():
    cap = capability_for("refund_order")
    assert cap is not None
    assert decide_after_timeout(cap) == RetryDecision.RECONCILE_ONLY


def test_uncertain_transport_error_classifier():
    assert is_uncertain_transport_error(TimeoutError("x"))
    assert is_uncertain_transport_error(ConnectionError("reset"))
    assert is_uncertain_transport_error(RuntimeError("upstream timed out"))
    assert not is_uncertain_transport_error(RuntimeError("business_rejected"))


def test_resolve_demo_refund_reconcile_without_lookup_call():
    cap = capability_for("refund_order")
    assert cap is not None
    ref = ToolExecutionRef(
        logical_execution_id="tck1",
        tenant_id="demo",
        tool_name="refund_order",
        idempotency_key="abc",
    )
    calls: list[str] = []

    def boom(_ref):
        calls.append("lookup")
        raise AssertionError("lookup must not run for RECONCILE_ONLY tools")

    action, outcome = resolve_after_uncertain(cap, ref, lookup=boom)
    assert action == ResolveAction.RECONCILE
    assert outcome.kind == ToolOutcomeKind.UNKNOWN
    assert calls == []


def test_resolve_lookup_success_marks_succeeded():
    cap = ToolCapability(
        name="pay",
        has_side_effects=True,
        supports_idempotency_key=True,
        supports_result_lookup=True,
        high_risk=True,
    )
    ref = ToolExecutionRef(
        logical_execution_id="tck2",
        tenant_id="demo",
        tool_name="pay",
        tool_request_id="ext-1",
    )

    def lookup(_ref):
        return ToolOutcome(kind=ToolOutcomeKind.SUCCEEDED, evidence="provider_status=paid")

    action, outcome = resolve_after_uncertain(cap, ref, lookup=lookup)
    assert action == ResolveAction.MARK_SUCCEEDED
    assert outcome.evidence == "provider_status=paid"


def test_resolve_lookup_unknown_does_not_auto_execute():
    cap = ToolCapability(
        name="pay",
        has_side_effects=True,
        supports_idempotency_key=True,
        supports_result_lookup=True,
        high_risk=True,
    )
    ref = ToolExecutionRef(
        logical_execution_id="tck3",
        tenant_id="demo",
        tool_name="pay",
    )
    action, outcome = resolve_after_uncertain(
        cap,
        ref,
        lookup=lambda _r: ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="still_pending"),
    )
    assert action == ResolveAction.RECONCILE
    assert outcome.kind == ToolOutcomeKind.UNKNOWN


def test_resolve_lookup_failed_marks_failed():
    cap = ToolCapability(
        name="pay",
        has_side_effects=True,
        supports_idempotency_key=True,
        supports_result_lookup=True,
        high_risk=True,
    )
    ref = ToolExecutionRef(
        logical_execution_id="tck4",
        tenant_id="demo",
        tool_name="pay",
        tool_request_id="ext-fail",
    )
    calls = {"n": 0}

    def lookup(_ref):
        calls["n"] += 1
        return ToolOutcome(kind=ToolOutcomeKind.FAILED, evidence="provider_status=declined")

    action, outcome = resolve_after_uncertain(cap, ref, lookup=lookup)
    assert action == ResolveAction.MARK_FAILED
    assert outcome.evidence == "provider_status=declined"
    assert calls["n"] == 1


def test_resolve_lookup_not_found_stays_reconcile():
    """Provider has no record — treat as UNKNOWN, never auto-execute."""
    cap = ToolCapability(
        name="pay",
        has_side_effects=True,
        supports_idempotency_key=False,
        supports_result_lookup=True,
        high_risk=True,
    )
    ref = ToolExecutionRef(
        logical_execution_id="tck5",
        tenant_id="demo",
        tool_name="pay",
        tool_request_id="missing",
    )
    action, outcome = resolve_after_uncertain(
        cap,
        ref,
        lookup=lambda _r: ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="not_found"),
    )
    assert action == ResolveAction.RECONCILE
    assert outcome.evidence == "not_found"


def test_resolve_lookup_exception_propagates_without_execute_side_effect():
    """Lookup transport failure must not be mistaken for tool success/failure."""
    cap = ToolCapability(
        name="pay",
        has_side_effects=True,
        supports_idempotency_key=True,
        supports_result_lookup=True,
        high_risk=True,
    )
    ref = ToolExecutionRef(
        logical_execution_id="tck6",
        tenant_id="demo",
        tool_name="pay",
    )

    def lookup(_ref):
        raise TimeoutError("lookup timed out")

    try:
        resolve_after_uncertain(cap, ref, lookup=lookup)
        raise AssertionError("expected TimeoutError")
    except TimeoutError as exc:
        assert "timed out" in str(exc)


def test_resolve_idempotent_without_lookup_allows_resubmit_flag_only():
    """supports_idempotency_key alone → RESUBMIT_WITH_SAME_KEY decision; still no execute here."""
    cap = ToolCapability(
        name="pay",
        has_side_effects=True,
        supports_idempotency_key=True,
        supports_result_lookup=False,
        high_risk=True,
    )
    ref = ToolExecutionRef(
        logical_execution_id="tck7",
        tenant_id="demo",
        tool_name="pay",
        idempotency_key="same-key",
    )
    action, outcome = resolve_after_uncertain(cap, ref, lookup=None)
    assert action == ResolveAction.RESUBMIT_WITH_SAME_KEY
    assert outcome.kind == ToolOutcomeKind.UNKNOWN
