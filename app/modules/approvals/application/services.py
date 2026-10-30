"""Approval use-cases: orchestration only; rules live in domain."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.approvals.application.dto import (
    ActorContext,
    ApproveCommand,
    CancelCommand,
    ExecuteCommand,
    LookupResolveCommand,
    ReconcileCommand,
    RejectCommand,
    TicketResult,
)
from app.modules.approvals.application.ports import (
    AuditPort,
    ExecutionLockPort,
    ResultLookupPort,
    TicketArgsPort,
    TicketExecutorPort,
    TicketRevalidationPort,
    TicketStore,
    UncertainOutcomePolicyPort,
)
from app.modules.approvals.domain.enums import TicketAction, TicketStatus
from app.modules.approvals.domain.exceptions import (
    ApprovalDomainError,
    ExecutionFailedError,
    InvalidTransitionError,
    OutcomeUncertainError,
    RevalidationFailedError,
    TicketConflictError,
    TicketNotExecutableError,
    UnknownStatusError,
)
from app.modules.approvals.domain.policies import (
    assert_can_approve,
    assert_can_cancel,
    assert_can_reject,
    evaluate_execute_gate,
    target_status,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_from_auth(auth: Any) -> ActorContext:
    """Build ActorContext from JWT/auth object (API adapter helper)."""
    perms = frozenset(getattr(auth, "perms", None) or [])
    return ActorContext(user_id=auth.user_id, tenant_id=auth.tenant_id, perms=perms)


class NullExecutionLock:
    """No-op lock for unit tests that inject their own concurrency control."""

    def hold(self, ticket_id: str):
        from contextlib import nullcontext

        return nullcontext()


class ApprovalApplicationService:
    """Approve / reject / cancel / execute use-cases.

    Approve ≠ execute. Execute only invokes the tool after a successful CAS claim.
    """

    def __init__(
        self,
        store: TicketStore,
        *,
        audit: AuditPort | None = None,
        revalidation: TicketRevalidationPort | None = None,
        args_port: TicketArgsPort | None = None,
        executor: TicketExecutorPort | None = None,
        lookup: ResultLookupPort | None = None,
        outcome_policy: UncertainOutcomePolicyPort | None = None,
        lock: ExecutionLockPort | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._revalidation = revalidation
        self._args = args_port
        self._executor = executor
        self._lookup = lookup
        self._outcome_policy = outcome_policy
        self._lock = lock or NullExecutionLock()

    def _persist_claimable_before_cas(self, ticket: dict[str, Any]) -> None:
        """Write claimable status without regressing a concurrent ``executing`` claim.

        Prefer store ``persist_claimable_status`` when available; otherwise save and
        rely on durable adapters that refuse claimable←executing overwrites.
        """
        persist = getattr(self._store, "persist_claimable_status", None)
        if callable(persist):
            persist(ticket)
            return
        self._store.save(ticket)

    def approve(self, command: ApproveCommand) -> TicketResult:
        ticket = self._store.get(command.actor.tenant_id, command.ticket_id)
        assert_can_approve(ticket["status"])
        ticket["status"] = target_status(ticket["status"], TicketAction.APPROVE).value
        ticket["decision"] = TicketStatus.APPROVED.value
        ticket["approver_id"] = command.actor.user_id
        ticket["decided_at"] = _now()
        ticket["reason"] = (command.reason or "approved")[:500]
        ticket["executed"] = False
        self._store.save(ticket)
        self._audit_safe(command.actor, command.ticket_id, "approve", ticket["status"])
        if command.execute_after:
            return self.execute(ExecuteCommand(ticket_id=command.ticket_id, actor=command.actor))
        return TicketResult(ticket=ticket)

    def reject(self, command: RejectCommand) -> TicketResult:
        ticket = self._store.get(command.actor.tenant_id, command.ticket_id)
        assert_can_reject(ticket["status"])
        ticket["status"] = target_status(ticket["status"], TicketAction.REJECT).value
        ticket["decision"] = TicketStatus.REJECTED.value
        ticket["approver_id"] = command.actor.user_id
        ticket["decided_at"] = _now()
        ticket["reason"] = (command.reason or "rejected")[:500]
        ticket["executed"] = False
        self._store.save(ticket)
        self._audit_safe(command.actor, command.ticket_id, "reject", ticket["status"])
        return TicketResult(ticket=ticket)

    def cancel(self, command: CancelCommand) -> TicketResult:
        ticket = self._store.get(command.actor.tenant_id, command.ticket_id)
        assert_can_cancel(ticket["status"])
        if ticket["applicant_id"] != command.actor.user_id and "ticket:risk:approve" not in command.actor.perms:
            raise PermissionError("forbidden")
        ticket["status"] = target_status(ticket["status"], TicketAction.CANCEL).value
        ticket["decision"] = TicketStatus.CANCELLED.value
        ticket["approver_id"] = command.actor.user_id
        ticket["decided_at"] = _now()
        ticket["reason"] = (command.reason or "cancelled")[:500]
        self._store.save(ticket)
        self._audit_safe(command.actor, command.ticket_id, "cancel", ticket["status"])
        return TicketResult(ticket=ticket)

    def execute(self, command: ExecuteCommand) -> TicketResult:
        """Application-owned execute orchestration (gate → revalidate → CAS → tool)."""
        if self._revalidation is None or self._args is None or self._executor is None:
            raise RuntimeError("execute requires revalidation, args_port, and executor")

        with self._lock.hold(command.ticket_id):
            ticket = self._store.get(command.actor.tenant_id, command.ticket_id)
            gate = evaluate_execute_gate(ticket["status"], executed=bool(ticket.get("executed")))
            if gate.already_done:
                return TicketResult(ticket=ticket)

            if gate.needs_revalidate:
                ticket = self._revalidation.revalidate(
                    ticket,
                    actor_id=command.actor.user_id,
                    tenant_id=command.actor.tenant_id,
                )
            elif ticket["status"] != TicketStatus.REVALIDATED.value:
                raise TicketNotExecutableError(f"cannot execute status={ticket['status']}")

            sibling = self._store.find_succeeded_idempotent(
                tenant_id=ticket["tenant_id"],
                idempotency_key=ticket.get("idempotency_key"),
                exclude_ticket_id=ticket["id"],
            )
            if sibling is not None:
                ticket["status"] = TicketStatus.SUCCEEDED.value
                ticket["executed"] = True
                ticket["execution_result"] = sibling.get("execution_result")
                ticket["reason"] = "idempotent_replay"
                self._store.save(ticket)
                return TicketResult(ticket=ticket)

            tool_name = ticket["tool_name"]
            if not self._executor.tool_registered(tool_name):
                ticket["status"] = TicketStatus.FAILED.value
                ticket["execution_result"] = "tool missing at execute"
                self._store.save(ticket)
                raise TicketNotExecutableError("execute failed: tool missing")

            if not self._args.verify_integrity(ticket):
                ticket["status"] = TicketStatus.FAILED.value
                ticket["reason"] = "args_integrity_failed"
                ticket["execution_result"] = "args_tampered_or_missing_digest"
                self._store.save(ticket)
                raise TicketNotExecutableError("ticket args integrity check failed")

            # Persist claimable status so durable CAS sees approved|revalidated.
            # CRITICAL: must not clobber a concurrent winner's ``executing`` with a
            # stale in-memory claimable status (that would allow a second CAS win).
            self._persist_claimable_before_cas(ticket)

            if not self._store.claim_executing(ticket["id"], ticket["tenant_id"]):
                # Lost race — must not invoke executor.
                raise TicketConflictError("ticket already executing")

            ticket["status"] = TicketStatus.EXECUTING.value
            ticket["execution_started_at"] = _now()
            self._store.save(ticket)

            try:
                args = self._args.load_args(ticket)
            except ValueError as exc:
                ticket["status"] = TicketStatus.FAILED.value
                ticket["execution_result"] = "args_decrypt_failed"
                self._store.save(ticket)
                raise TicketNotExecutableError("cannot decrypt tool args") from exc

            if self._executor.tool_scope(tool_name) == "self_only":
                args["owner_user_id"] = ticket["applicant_id"]
                args.pop("user_id", None)

            try:
                result = self._executor.run(
                    tool_name,
                    args,
                    {"owner_user_id": ticket["applicant_id"]},
                )
            except OutcomeUncertainError as exc:
                # Adapter classified timeout/transport on side-effect tool.
                ticket["status"] = TicketStatus.RECONCILIATION_REQUIRED.value
                ticket["executed"] = False
                ticket["execution_result"] = (exc.message or "tool_outcome_unknown")[:500]
                ticket["reason"] = "outcome_uncertain_after_tool_call"
                self._store.save(ticket)
                self._audit_safe(
                    command.actor,
                    command.ticket_id,
                    "execute_uncertain",
                    ticket["status"],
                    detail="outcome_uncertain",
                )
                raise
            except Exception as exc:  # noqa: BLE001
                ticket["status"] = TicketStatus.FAILED.value
                ticket["executed"] = False
                ticket["execution_result"] = f"tool_error: {exc}"[:500]
                ticket["failed_at"] = _now()
                self._store.save(ticket)
                self._audit_safe(
                    command.actor,
                    command.ticket_id,
                    "execute_failed",
                    ticket["status"],
                    detail="tool_error",
                )
                raise ExecutionFailedError("tool execution failed") from exc

            ticket["status"] = TicketStatus.SUCCEEDED.value
            ticket["executed"] = True
            ticket["execution_result"] = result
            ticket["executed_at"] = _now()
            ticket["executor_id"] = command.actor.user_id
            self._store.save(ticket)
            self._audit_safe(command.actor, command.ticket_id, "execute", ticket["status"])
            return TicketResult(ticket=ticket)

    def reconcile(self, command: ReconcileCommand) -> TicketResult:
        """Confirm outcome for ``reconciliation_required`` without re-running the tool.

        Requires approve permission. Does not invoke the executor — operators must
        verify external side effects before calling this.
        """
        if "ticket:risk:approve" not in command.actor.perms:
            raise PermissionError("forbidden")
        outcome = (command.outcome or "").strip().lower()
        if outcome not in {"succeeded", "failed"}:
            raise TicketNotExecutableError("reconcile outcome must be succeeded or failed")

        ticket = self._store.get(command.actor.tenant_id, command.ticket_id)
        current = str(ticket.get("status") or "")
        # Idempotent only when the requested outcome already matches a hard terminal.
        if current == TicketStatus.SUCCEEDED.value and outcome == "succeeded":
            return TicketResult(ticket=ticket)
        if current == TicketStatus.FAILED.value and outcome == "failed":
            return TicketResult(ticket=ticket)
        if current in {
            TicketStatus.SUCCEEDED.value,
            TicketStatus.FAILED.value,
            TicketStatus.REJECTED.value,
            TicketStatus.CANCELLED.value,
            TicketStatus.EXPIRED.value,
        }:
            raise TicketConflictError("ticket already in terminal state")

        action = (
            TicketAction.RECONCILE_SUCCEEDED
            if outcome == "succeeded"
            else TicketAction.RECONCILE_FAILED
        )
        ticket["status"] = target_status(current, action).value
        ticket["approver_id"] = command.actor.user_id
        ticket["decided_at"] = _now()
        note = (command.note or "manual_reconcile")[:500]
        if outcome == "succeeded":
            ticket["executed"] = True
            ticket["reason"] = f"reconcile_confirmed_success:{note}"[:500]
            if not ticket.get("execution_result"):
                ticket["execution_result"] = "reconciled_success_external_verified"
        else:
            ticket["executed"] = False
            ticket["reason"] = f"reconcile_confirmed_failure:{note}"[:500]
            ticket["execution_result"] = ticket.get("execution_result") or (
                "reconciled_failure_or_no_side_effect"
            )
        self._store.save(ticket)
        self._audit_safe(
            command.actor,
            command.ticket_id,
            "reconcile",
            ticket["status"],
            detail=outcome,
        )
        return TicketResult(ticket=ticket)

    def lookup_resolve(self, command: LookupResolveCommand) -> TicketResult:
        """Resolve uncertain tickets via external result lookup — never re-executes.

        Allowed statuses: ``reconciliation_required`` (and already-terminal noops).
        Requires approve permission. Without a lookup port / adapter, stays reconciling.
        """
        if "ticket:risk:approve" not in command.actor.perms:
            raise PermissionError("forbidden")
        if self._lookup is None or self._outcome_policy is None:
            raise TicketNotExecutableError("result lookup / outcome policy not configured")

        ticket = self._store.get(command.actor.tenant_id, command.ticket_id)
        current = str(ticket.get("status") or "")
        if current in {
            TicketStatus.SUCCEEDED.value,
            TicketStatus.FAILED.value,
            TicketStatus.REJECTED.value,
            TicketStatus.CANCELLED.value,
            TicketStatus.EXPIRED.value,
        }:
            raise TicketConflictError("ticket already in terminal state")
        if current != TicketStatus.RECONCILIATION_REQUIRED.value:
            raise TicketNotExecutableError(
                f"lookup_resolve requires reconciliation_required, got {current}"
            )

        tool_name = str(ticket.get("tool_name") or "")
        try:
            decision, evidence = self._outcome_policy.resolve(
                ticket_id=str(ticket["id"]),
                tenant_id=str(ticket.get("tenant_id") or command.actor.tenant_id),
                tool_name=tool_name,
                idempotency_key=ticket.get("idempotency_key"),
                tool_request_id=ticket.get("tool_request_id"),
                lookup=self._lookup,
            )
        except LookupError as exc:
            raise TicketNotExecutableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — transport on lookup stays uncertain
            ticket["execution_result"] = f"lookup_failed:{exc}"[:500]
            ticket["reason"] = "lookup_transport_uncertain"
            self._store.save(ticket)
            self._audit_safe(
                command.actor,
                command.ticket_id,
                "lookup_failed",
                ticket["status"],
                detail="transport",
            )
            return TicketResult(ticket=ticket)

        if decision == "succeeded":
            ticket["status"] = target_status(
                current, TicketAction.RECONCILE_SUCCEEDED
            ).value
            ticket["executed"] = True
            ticket["execution_result"] = evidence[:500]
            ticket["reason"] = "resolved_via_result_lookup"
            ticket["approver_id"] = command.actor.user_id
            ticket["decided_at"] = _now()
        elif decision == "failed":
            ticket["status"] = target_status(
                current, TicketAction.RECONCILE_FAILED
            ).value
            ticket["executed"] = False
            ticket["execution_result"] = evidence[:500]
            ticket["reason"] = "resolved_via_result_lookup"
            ticket["approver_id"] = command.actor.user_id
            ticket["decided_at"] = _now()
        else:
            # Still unknown — keep reconciling; never call executor.
            ticket["execution_result"] = f"lookup_inconclusive:{evidence}"[:500]
            ticket["reason"] = "lookup_action=reconcile"

        self._store.save(ticket)
        self._audit_safe(
            command.actor,
            command.ticket_id,
            "lookup_resolve",
            ticket["status"],
            detail=decision,
        )
        return TicketResult(ticket=ticket)

    def _audit_safe(
        self,
        actor: ActorContext,
        ticket_id: str,
        action: str,
        status: str,
        *,
        detail: str | None = None,
    ) -> None:
        if self._audit is None:
            return
        self._audit.record_ticket_event(
            ticket_id=ticket_id,
            tenant_id=actor.tenant_id,
            actor_id=actor.user_id,
            action=action,
            status=status,
            detail=detail,
        )


def map_domain_to_http(exc: ApprovalDomainError) -> tuple[int, str]:
    """Map domain errors to (status_code, detail) for FastAPI adapters."""
    if isinstance(exc, InvalidTransitionError):
        return 409, exc.message
    if isinstance(exc, TicketConflictError):
        return 409, exc.message
    if isinstance(exc, TicketNotExecutableError):
        return 409, exc.message
    if isinstance(exc, RevalidationFailedError):
        return 409, exc.message
    if isinstance(exc, UnknownStatusError):
        return 409, exc.message
    if isinstance(exc, OutcomeUncertainError):
        return 409, exc.message
    if isinstance(exc, ExecutionFailedError):
        return 500, exc.message
    return 400, exc.message
