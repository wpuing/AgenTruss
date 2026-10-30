"""Facade used by ``app.api.http`` so routes stay thin."""

from __future__ import annotations

from typing import Any

from app.modules.approvals.application.dto import (
    ApproveCommand,
    CancelCommand,
    ExecuteCommand,
    LookupResolveCommand,
    ReconcileCommand,
    RejectCommand,
)
from app.modules.approvals.application.services import ApprovalApplicationService, actor_from_auth
from app.modules.approvals.infrastructure.adapters import (
    PlatformArgsAdapter,
    PlatformRevalidationAdapter,
    PlatformToolExecutor,
    ProcessExecutionLock,
    ReliabilityOutcomePolicy,
)
from app.modules.approvals.infrastructure.store import PlatformTicketStore, raise_http_from_domain
from app.tools.lookup_registry import RegistryResultLookup


def _service(platform: Any, auth: Any) -> ApprovalApplicationService:
    store = PlatformTicketStore(platform, auth=auth)
    return ApprovalApplicationService(
        store,
        revalidation=PlatformRevalidationAdapter(platform),
        args_port=PlatformArgsAdapter(),
        executor=PlatformToolExecutor(),
        lookup=RegistryResultLookup(),
        outcome_policy=ReliabilityOutcomePolicy(),
        lock=ProcessExecutionLock(),
    )


def approve(
    platform: Any,
    auth: Any,
    ticket_id: str,
    *,
    execute: bool = False,
    reason: str | None = None,
) -> dict:
    try:
        result = _service(platform, auth).approve(
            ApproveCommand(
                ticket_id=ticket_id,
                actor=actor_from_auth(auth),
                reason=reason,
                execute_after=execute,
            )
        )
        return result.ticket
    except Exception as exc:  # noqa: BLE001 — mapped or re-raised
        raise_http_from_domain(exc)
        raise  # pragma: no cover


def reject(platform: Any, auth: Any, ticket_id: str, reason: str | None = None) -> dict:
    try:
        return _service(platform, auth).reject(
            RejectCommand(ticket_id=ticket_id, actor=actor_from_auth(auth), reason=reason)
        ).ticket
    except Exception as exc:  # noqa: BLE001
        raise_http_from_domain(exc)
        raise  # pragma: no cover


def cancel(platform: Any, auth: Any, ticket_id: str, reason: str | None = None) -> dict:
    try:
        return _service(platform, auth).cancel(
            CancelCommand(ticket_id=ticket_id, actor=actor_from_auth(auth), reason=reason)
        ).ticket
    except Exception as exc:  # noqa: BLE001
        raise_http_from_domain(exc)
        raise  # pragma: no cover


def execute(platform: Any, auth: Any, ticket_id: str) -> dict:
    try:
        return _service(platform, auth).execute(
            ExecuteCommand(ticket_id=ticket_id, actor=actor_from_auth(auth))
        ).ticket
    except Exception as exc:  # noqa: BLE001
        raise_http_from_domain(exc)
        raise  # pragma: no cover


def reconcile(
    platform: Any,
    auth: Any,
    ticket_id: str,
    *,
    outcome: str,
    note: str | None = None,
) -> dict:
    """Confirm uncertain execution outcome without re-invoking the tool."""
    try:
        return _service(platform, auth).reconcile(
            ReconcileCommand(
                ticket_id=ticket_id,
                actor=actor_from_auth(auth),
                outcome=outcome,
                note=note,
            )
        ).ticket
    except Exception as exc:  # noqa: BLE001
        raise_http_from_domain(exc)
        raise  # pragma: no cover


def lookup_resolve(platform: Any, auth: Any, ticket_id: str) -> dict:
    """Attempt external result lookup for reconciliation_required — never re-runs tool."""
    try:
        return _service(platform, auth).lookup_resolve(
            LookupResolveCommand(ticket_id=ticket_id, actor=actor_from_auth(auth))
        ).ticket
    except Exception as exc:  # noqa: BLE001
        raise_http_from_domain(exc)
        raise  # pragma: no cover
