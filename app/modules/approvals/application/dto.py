"""Command / result DTOs for approval use-cases (no FastAPI types)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActorContext:
    user_id: str
    tenant_id: str
    perms: frozenset[str]


@dataclass(frozen=True)
class ApproveCommand:
    ticket_id: str
    actor: ActorContext
    reason: str | None = None
    execute_after: bool = False


@dataclass(frozen=True)
class RejectCommand:
    ticket_id: str
    actor: ActorContext
    reason: str | None = None


@dataclass(frozen=True)
class CancelCommand:
    ticket_id: str
    actor: ActorContext
    reason: str | None = None


@dataclass(frozen=True)
class ExecuteCommand:
    ticket_id: str
    actor: ActorContext


@dataclass(frozen=True)
class ReconcileCommand:
    """Operator confirmation when tool outcome was uncertain after crash."""

    ticket_id: str
    actor: ActorContext
    outcome: str  # "succeeded" | "failed"
    note: str | None = None


@dataclass(frozen=True)
class LookupResolveCommand:
    """Attempt external result lookup for an uncertain ticket — never re-executes."""

    ticket_id: str
    actor: ActorContext


@dataclass(frozen=True)
class TicketResult:
    """Public-ish ticket snapshot; callers must still redact for HTTP."""

    ticket: dict[str, Any]
