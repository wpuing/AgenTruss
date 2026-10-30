"""Domain unit tests — no FastAPI / DB / LLM."""

from __future__ import annotations

import pytest

from app.modules.approvals.domain.enums import TicketAction, TicketStatus
from app.modules.approvals.domain.exceptions import (
    InvalidTransitionError,
    TicketNotExecutableError,
    UnknownStatusError,
)
from app.modules.approvals.domain.policies import (
    assert_can_approve,
    assert_can_cancel,
    assert_can_reject,
    can_transition,
    evaluate_execute_gate,
    parse_status,
    target_status,
    transition_table,
)


@pytest.mark.parametrize(
    "current,action,expected",
    [
        ("pending", TicketAction.APPROVE, TicketStatus.APPROVED),
        ("pending", TicketAction.REJECT, TicketStatus.REJECTED),
        ("pending", TicketAction.CANCEL, TicketStatus.CANCELLED),
        ("approved", TicketAction.CANCEL, TicketStatus.CANCELLED),
        ("revalidated", TicketAction.CANCEL, TicketStatus.CANCELLED),
        ("approved", TicketAction.REVALIDATE, TicketStatus.REVALIDATED),
        ("approved", TicketAction.CLAIM_EXECUTE, TicketStatus.EXECUTING),
        ("revalidated", TicketAction.CLAIM_EXECUTE, TicketStatus.EXECUTING),
        ("executing", TicketAction.MARK_SUCCEEDED, TicketStatus.SUCCEEDED),
        ("executing", TicketAction.MARK_FAILED, TicketStatus.FAILED),
        (
            "executing",
            TicketAction.MARK_RECONCILIATION,
            TicketStatus.RECONCILIATION_REQUIRED,
        ),
        (
            "reconciliation_required",
            TicketAction.RECONCILE_SUCCEEDED,
            TicketStatus.SUCCEEDED,
        ),
        (
            "reconciliation_required",
            TicketAction.RECONCILE_FAILED,
            TicketStatus.FAILED,
        ),
    ],
)
def test_legal_transitions(current, action, expected):
    assert can_transition(current, action)
    assert target_status(current, action) == expected


@pytest.mark.parametrize(
    "current,action",
    [
        ("approved", TicketAction.APPROVE),
        ("rejected", TicketAction.APPROVE),
        ("succeeded", TicketAction.REJECT),
        ("pending", TicketAction.CLAIM_EXECUTE),
        ("executing", TicketAction.APPROVE),
        ("failed", TicketAction.CANCEL),
        ("cancelled", TicketAction.CANCEL),
    ],
)
def test_illegal_transitions(current, action):
    assert not can_transition(current, action)
    with pytest.raises(InvalidTransitionError):
        target_status(current, action)


def test_unknown_status_refused():
    with pytest.raises(UnknownStatusError):
        parse_status("weird_state")
    with pytest.raises(UnknownStatusError):
        assert_can_approve("not_a_status")


def test_assert_helpers():
    assert_can_approve("pending")
    assert_can_reject("pending")
    assert_can_cancel("approved")
    with pytest.raises(InvalidTransitionError):
        assert_can_approve("approved")


def test_execute_gate_idempotent_succeeded():
    gate = evaluate_execute_gate("succeeded", executed=True)
    assert gate.already_done is True


def test_execute_gate_needs_revalidate():
    gate = evaluate_execute_gate("approved", executed=False)
    assert gate.needs_revalidate is True


def test_execute_gate_revalidated():
    gate = evaluate_execute_gate("revalidated")
    assert gate.needs_revalidate is False
    assert gate.already_done is False


@pytest.mark.parametrize(
    "status,msg",
    [
        ("pending", "ticket not approved"),
        ("executing", "ticket already executing"),
        ("rejected", "cannot execute"),
        ("failed", "cannot execute"),
        ("cancelled", "cannot execute"),
        ("expired", "cannot execute"),
        ("reconciliation_required", "reconciliation"),
    ],
)
def test_execute_gate_blocked(status, msg):
    with pytest.raises(TicketNotExecutableError) as ei:
        evaluate_execute_gate(status)
    assert msg in ei.value.message


def test_transition_table_nonempty():
    rows = transition_table()
    assert ("pending", "approve", "approved") in rows
    assert ("approved", "claim_execute", "executing") in rows
