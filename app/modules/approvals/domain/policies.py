"""Centralized ticket status transition rules (single source of truth)."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.approvals.domain.enums import (
    CLAIMABLE_STATUSES,
    TERMINAL_STATUSES,
    TicketAction,
    TicketStatus,
)
from app.modules.approvals.domain.exceptions import (
    InvalidTransitionError,
    TicketNotExecutableError,
    UnknownStatusError,
)

# Explicit allow-list: (current_status, action) → target_status (None = no status change yet)
_TRANSITIONS: dict[tuple[TicketStatus, TicketAction], TicketStatus | None] = {
    (TicketStatus.PENDING, TicketAction.APPROVE): TicketStatus.APPROVED,
    (TicketStatus.PENDING, TicketAction.REJECT): TicketStatus.REJECTED,
    (TicketStatus.PENDING, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.APPROVED, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.REVALIDATED, TicketAction.CANCEL): TicketStatus.CANCELLED,
    (TicketStatus.APPROVED, TicketAction.REVALIDATE): TicketStatus.REVALIDATED,
    (TicketStatus.APPROVED, TicketAction.CLAIM_EXECUTE): TicketStatus.EXECUTING,
    (TicketStatus.REVALIDATED, TicketAction.CLAIM_EXECUTE): TicketStatus.EXECUTING,
    (TicketStatus.EXECUTING, TicketAction.MARK_SUCCEEDED): TicketStatus.SUCCEEDED,
    (TicketStatus.EXECUTING, TicketAction.MARK_FAILED): TicketStatus.FAILED,
    (TicketStatus.EXECUTING, TicketAction.MARK_RECONCILIATION): TicketStatus.RECONCILIATION_REQUIRED,
    (
        TicketStatus.RECONCILIATION_REQUIRED,
        TicketAction.RECONCILE_SUCCEEDED,
    ): TicketStatus.SUCCEEDED,
    (
        TicketStatus.RECONCILIATION_REQUIRED,
        TicketAction.RECONCILE_FAILED,
    ): TicketStatus.FAILED,
    # Revalidation failure paths mark failed from approved (before claim)
    (TicketStatus.APPROVED, TicketAction.MARK_FAILED): TicketStatus.FAILED,
    (TicketStatus.REVALIDATED, TicketAction.MARK_FAILED): TicketStatus.FAILED,
}


def parse_status(raw: str | None) -> TicketStatus:
    """Parse persisted status; refuse unknown values (do not silently allow)."""
    if raw is None or raw == "":
        raise UnknownStatusError(str(raw))
    try:
        return TicketStatus(raw)
    except ValueError as exc:
        raise UnknownStatusError(str(raw)) from exc


def can_transition(current: TicketStatus | str, action: TicketAction | str) -> bool:
    status = parse_status(current) if isinstance(current, str) else current
    act = TicketAction(action) if isinstance(action, str) else action
    return (status, act) in _TRANSITIONS


def target_status(current: TicketStatus | str, action: TicketAction | str) -> TicketStatus:
    status = parse_status(current) if isinstance(current, str) else current
    act = TicketAction(action) if isinstance(action, str) else action
    key = (status, act)
    if key not in _TRANSITIONS:
        raise InvalidTransitionError(
            f"cannot {act.value} status={status.value}",
            current=status.value,
            action=act.value,
        )
    target = _TRANSITIONS[key]
    if target is None:
        raise InvalidTransitionError(
            f"cannot {act.value} status={status.value}",
            current=status.value,
            action=act.value,
        )
    return target


def assert_can_approve(status: str) -> TicketStatus:
    current = parse_status(status)
    if not can_transition(current, TicketAction.APPROVE):
        raise InvalidTransitionError(
            f"cannot approve status={current.value}",
            current=current.value,
            action=TicketAction.APPROVE.value,
        )
    return current


def assert_can_reject(status: str) -> TicketStatus:
    current = parse_status(status)
    if not can_transition(current, TicketAction.REJECT):
        raise InvalidTransitionError(
            f"cannot reject status={current.value}",
            current=current.value,
            action=TicketAction.REJECT.value,
        )
    return current


def assert_can_cancel(status: str) -> TicketStatus:
    current = parse_status(status)
    if not can_transition(current, TicketAction.CANCEL):
        raise InvalidTransitionError(
            f"cannot cancel status={current.value}",
            current=current.value,
            action=TicketAction.CANCEL.value,
        )
    return current


@dataclass(frozen=True)
class ExecuteGateResult:
    """Outcome of execute pre-checks before CAS claim."""

    status: TicketStatus
    already_done: bool = False
    needs_revalidate: bool = False


def evaluate_execute_gate(status: str, *, executed: bool = False) -> ExecuteGateResult:
    """Mirror legacy execute_ticket preconditions without side effects.

    - succeeded + executed → idempotent success (already_done)
    - other terminal → not executable
    - pending → not approved
    - executing → conflict
    - approved → needs revalidate then claim
    - revalidated → claim
    """
    current = parse_status(status)

    if current == TicketStatus.SUCCEEDED and executed:
        return ExecuteGateResult(status=current, already_done=True)

    if current == TicketStatus.RECONCILIATION_REQUIRED:
        raise TicketNotExecutableError(
            "ticket awaits manual reconciliation (outcome uncertain; do not re-run tool)"
        )

    if current in TERMINAL_STATUSES - {TicketStatus.SUCCEEDED}:
        raise TicketNotExecutableError(f"cannot execute status={current.value}")

    if current == TicketStatus.PENDING:
        raise TicketNotExecutableError("ticket not approved")

    if current == TicketStatus.EXECUTING:
        raise TicketNotExecutableError("ticket already executing")

    if current == TicketStatus.APPROVED:
        return ExecuteGateResult(status=current, needs_revalidate=True)

    if current == TicketStatus.REVALIDATED:
        return ExecuteGateResult(status=current, needs_revalidate=False)

    # Unknown non-enum already raised; any leftover known enum without path
    raise TicketNotExecutableError(f"cannot execute status={current.value}")


def is_claimable(status: str) -> bool:
    try:
        return parse_status(status) in CLAIMABLE_STATUSES
    except UnknownStatusError:
        return False


def transition_table() -> list[tuple[str, str, str]]:
    """For docs / architecture lint: (current, action, target)."""
    rows: list[tuple[str, str, str]] = []
    for (cur, act), tgt in sorted(_TRANSITIONS.items(), key=lambda x: (x[0][0].value, x[0][1].value)):
        if tgt is not None:
            rows.append((cur.value, act.value, tgt.value))
    return rows
