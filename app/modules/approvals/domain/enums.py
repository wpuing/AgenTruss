"""Ticket status values as used by the running codebase."""

from __future__ import annotations

from enum import StrEnum


class TicketStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REVALIDATED = "revalidated"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    # Outcome unknown after crash / lost write — not a confirmed tool failure.
    RECONCILIATION_REQUIRED = "reconciliation_required"


class TicketAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    REVALIDATE = "revalidate"
    CLAIM_EXECUTE = "claim_execute"
    MARK_SUCCEEDED = "mark_succeeded"
    MARK_FAILED = "mark_failed"
    MARK_RECONCILIATION = "mark_reconciliation"
    RECONCILE_SUCCEEDED = "reconcile_succeeded"
    RECONCILE_FAILED = "reconcile_failed"


TERMINAL_STATUSES = frozenset(
    {
        TicketStatus.SUCCEEDED,
        TicketStatus.FAILED,
        TicketStatus.REJECTED,
        TicketStatus.CANCELLED,
        TicketStatus.EXPIRED,
        TicketStatus.RECONCILIATION_REQUIRED,
    }
)

# Hard outcomes that must never be overwritten by upsert / recovery.
HARD_TERMINAL_STATUSES = frozenset(
    {
        TicketStatus.SUCCEEDED,
        TicketStatus.FAILED,
        TicketStatus.REJECTED,
        TicketStatus.CANCELLED,
        TicketStatus.EXPIRED,
    }
)

CLAIMABLE_STATUSES = frozenset({TicketStatus.APPROVED, TicketStatus.REVALIDATED})
