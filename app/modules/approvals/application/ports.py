"""Minimal ports for approval use-cases (Protocol, not SDK wrappers)."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class TicketStore(Protocol):
    """Load / persist ticket dicts. Postgres mode must use DB as authority."""

    def get(self, tenant_id: str, ticket_id: str) -> dict[str, Any]:
        """Raise LookupError / HTTP 404-equivalent at adapter if missing."""
        ...

    def save(self, ticket: dict[str, Any]) -> None:
        ...

    def claim_executing(self, ticket_id: str, tenant_id: str) -> bool:
        """CAS claim; True only for winner. Must not call tool side effects."""
        ...

    def find_succeeded_idempotent(
        self,
        *,
        tenant_id: str,
        idempotency_key: str | None,
        exclude_ticket_id: str,
    ) -> dict[str, Any] | None:
        """Optional sibling succeeded ticket for idempotent replay."""
        ...


class AuditPort(Protocol):
    """Optional audit hook; implementations must not receive plaintext secrets."""

    def record_ticket_event(
        self,
        *,
        ticket_id: str,
        tenant_id: str,
        actor_id: str,
        action: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        ...


class TicketRevalidationPort(Protocol):
    """Re-check applicant / tool / agent before claim. Mutates ticket to revalidated or failed."""

    def revalidate(self, ticket: dict[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        ...


class TicketArgsPort(Protocol):
    """Integrity check + decrypt inside authorized execute path only."""

    def verify_integrity(self, ticket: dict[str, Any]) -> bool:
        ...

    def load_args(self, ticket: dict[str, Any]) -> dict[str, Any]:
        ...


class TicketExecutorPort(Protocol):
    """Run tool handler after successful CAS claim; side effects outside DB txn."""

    def tool_registered(self, tool_name: str) -> bool:
        ...

    def tool_scope(self, tool_name: str) -> str | None:
        ...

    def run(self, tool_name: str, args: dict[str, Any], context: dict[str, Any]) -> Any:
        ...


class ResultLookupPort(Protocol):
    """Query external execution outcome by stable ref — never performs side effects."""

    def lookup(self, ref: Any) -> Any:
        """Return ``ToolOutcome`` (or compatible). Must not call tool execute."""
        ...


class UncertainOutcomePolicyPort(Protocol):
    """Map tool capability + optional lookup into a closed decision for Application.

    Implementations live in infrastructure and may use the reliability contract;
    Application must not import tool infrastructure modules.
    Returns ``(decision, evidence)`` where decision is one of:
    ``succeeded`` | ``failed`` | ``unknown``.
    Never invokes tool execute / side effects.
    """

    def resolve(
        self,
        *,
        ticket_id: str,
        tenant_id: str,
        tool_name: str,
        idempotency_key: str | None,
        tool_request_id: str | None,
        lookup: ResultLookupPort,
    ) -> tuple[str, str]:
        ...


class ExecutionLockPort(Protocol):
    """Serialize execute per ticket_id within one process (memory-mode companion to CAS)."""

    def hold(self, ticket_id: str) -> AbstractContextManager[None]:
        ...
