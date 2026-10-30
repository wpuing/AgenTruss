"""High-risk ticket HTTP routes (list/detail/approve/reject/execute/reconcile/lookup)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import need, platform_dep
from app.api.ticket_views import public_ticket
from app.core.platform import Platform
from app.core.security import AuthContext
from app.modules.approvals.api import facade as approvals_facade
from app.tools import tickets as ticket_svc

router = APIRouter()


class TicketDecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    execute: bool = False


class TicketReconcileIn(BaseModel):
    """Operator confirmation when crash left tool outcome uncertain."""

    outcome: str = Field(..., pattern="^(succeeded|failed)$")
    note: str | None = Field(default=None, max_length=500)


@router.get("/api/admin/tickets/risk")
def list_risk_tickets(
    auth: AuthContext = Depends(need("ticket:risk:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.infra.ticket_repo import list_tickets, persistence_enabled

    if persistence_enabled():
        rows = list_tickets(auth.tenant_id, ticket_type="risk")
        for row in rows:
            platform.tickets[row["id"]] = row
    else:
        rows = [
            row
            for row in platform.tickets.values()
            if row["tenant_id"] == auth.tenant_id and row["type"] == "risk"
        ]
    return {"items": [public_ticket(row) for row in rows]}


@router.get("/api/admin/tickets/risk/{ticket_id}")
def get_risk_ticket(
    ticket_id: str,
    auth: AuthContext = Depends(need("ticket:risk:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return public_ticket(ticket_svc.get_ticket(platform, auth.tenant_id, ticket_id))


@router.post("/api/admin/tickets/{ticket_id}/approve")
def approve(
    ticket_id: str,
    body: TicketDecisionIn | None = None,
    auth: AuthContext = Depends(need("ticket:risk:approve")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    payload = body or TicketDecisionIn()
    return public_ticket(
        approvals_facade.approve(
            platform, auth, ticket_id, execute=payload.execute, reason=payload.reason
        )
    )


@router.post("/api/admin/tickets/{ticket_id}/reject")
def reject_ticket(
    ticket_id: str,
    body: TicketDecisionIn | None = None,
    auth: AuthContext = Depends(need("ticket:risk:reject")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    payload = body or TicketDecisionIn()
    return public_ticket(approvals_facade.reject(platform, auth, ticket_id, reason=payload.reason))


@router.post("/api/admin/tickets/{ticket_id}/execute")
def execute_ticket(
    ticket_id: str,
    auth: AuthContext = Depends(need("ticket:risk:approve")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return public_ticket(approvals_facade.execute(platform, auth, ticket_id))


@router.post("/api/admin/tickets/{ticket_id}/reconcile")
def reconcile_ticket(
    ticket_id: str,
    body: TicketReconcileIn,
    auth: AuthContext = Depends(need("ticket:risk:approve")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return public_ticket(
        approvals_facade.reconcile(
            platform, auth, ticket_id, outcome=body.outcome, note=body.note
        )
    )


@router.post("/api/admin/tickets/{ticket_id}/lookup-resolve")
def lookup_resolve_ticket(
    ticket_id: str,
    auth: AuthContext = Depends(need("ticket:risk:approve")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return public_ticket(approvals_facade.lookup_resolve(platform, auth, ticket_id))
