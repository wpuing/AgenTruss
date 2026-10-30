"""TicketStore adapter over existing tickets hot-mirror + ticket_repo CAS."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.modules.approvals.application.dto import ActorContext
from app.modules.approvals.domain.enums import CLAIMABLE_STATUSES


class PlatformTicketStore:
    """Bridges ApprovalApplicationService to platform.tickets + ticket_repo.

    PostgreSQL remains authoritative when ``persistence_enabled()``.
    """

    def __init__(self, platform: Any, *, auth: Any | None = None) -> None:
        self.platform = platform
        self._auth = auth

    def auth_for(self, actor: ActorContext) -> Any:
        """Return bound auth object for legacy execute_ticket."""
        if self._auth is not None:
            return self._auth
        # Minimal duck-typed auth for tests
        return type(
            "Auth",
            (),
            {
                "user_id": actor.user_id,
                "tenant_id": actor.tenant_id,
                "perms": set(actor.perms),
            },
        )()

    def get(self, tenant_id: str, ticket_id: str) -> dict[str, Any]:
        from app.tools.tickets import get_ticket

        return get_ticket(self.platform, tenant_id, ticket_id)

    def save(self, ticket: dict[str, Any]) -> None:
        from app.infra.ticket_repo import get_ticket_row, persistence_enabled
        from app.tools.tickets import _persist

        _persist(ticket)
        # Prefer DB authority after persist so concurrent reconcile losers cannot
        # clobber the hot mirror with a rejected upsert snapshot.
        if persistence_enabled():
            authoritative = get_ticket_row(ticket["id"], ticket["tenant_id"])
            if authoritative is not None:
                self.platform.tickets[ticket["id"]] = authoritative
                return
        # Memory path: do not regress local cache below durable busy/done status.
        hot = self.platform.tickets.get(ticket["id"])
        hard = {"succeeded", "failed", "rejected", "cancelled", "expired"}
        if hot and hot.get("status") in hard and ticket.get("status") != hot.get("status"):
            self.platform.tickets[ticket["id"]] = hot
            return
        if hot and hot.get("status") in {
            "executing",
            "succeeded",
            "failed",
            "reconciliation_required",
        }:
            if ticket.get("status") in {s.value for s in CLAIMABLE_STATUSES} | {"pending"}:
                ticket = {**ticket, "status": hot["status"]}
        self.platform.tickets[ticket["id"]] = ticket

    def persist_claimable_status(self, ticket: dict[str, Any]) -> None:
        """CAS-safe flush of approved/revalidated before claim_executing."""
        self.save(ticket)

    def claim_executing(self, ticket_id: str, tenant_id: str) -> bool:
        from app.infra.ticket_repo import claim_ticket_executing, persistence_enabled

        if persistence_enabled():
            return claim_ticket_executing(ticket_id, tenant_id)
        ticket = self.platform.tickets.get(ticket_id)
        if not ticket or ticket.get("tenant_id") != tenant_id:
            return False
        if ticket.get("status") not in {s.value for s in CLAIMABLE_STATUSES}:
            return False
        ticket["status"] = "executing"
        self.save(ticket)
        return True

    def find_succeeded_idempotent(
        self,
        *,
        tenant_id: str,
        idempotency_key: str | None,
        exclude_ticket_id: str,
    ) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        for other in self.platform.tickets.values():
            if (
                other.get("id") != exclude_ticket_id
                and other.get("tenant_id") == tenant_id
                and other.get("idempotency_key") == idempotency_key
                and other.get("status") == "succeeded"
                and other.get("executed")
            ):
                return other
        return None


def raise_http_from_domain(exc: Exception) -> None:
    """Convert domain / permission errors to HTTPException (API boundary)."""
    from app.modules.approvals.application.services import map_domain_to_http
    from app.modules.approvals.domain.exceptions import ApprovalDomainError

    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc) or "forbidden") from exc
    if isinstance(exc, ApprovalDomainError):
        code, detail = map_domain_to_http(exc)
        raise HTTPException(status_code=code, detail=detail) from exc
    raise exc
