"""Thin admin catalog / IAM-read / human-ticket routes (delegate to admin.*)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import need, platform_dep
from app.core.platform import Platform
from app.core.security import AuthContext

router = APIRouter()


class FeedbackIn(BaseModel):
    kb_id: str | None = None
    query: str = Field(default="", max_length=500)
    rating: str = Field(default="neutral", max_length=32)
    comment: str = Field(default="", max_length=1000)


class HumanTicketIn(BaseModel):
    note: str = Field(default="", max_length=500)
    reason: str = Field(default="manual", max_length=64)
    session_id: str | None = None
    audit_id: str | None = None


class HumanResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get("/api/admin/industries")
def admin_industries(
    auth: AuthContext = Depends(need("agent:template:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import list_industries

    del auth
    return list_industries(platform)


@router.get("/api/admin/backlog")
def admin_backlog(auth: AuthContext = Depends(need("dashboard:view"))) -> dict:
    del auth
    from app.admin.backlog import list_backlog

    return list_backlog()


@router.get("/api/admin/tenants")
def admin_tenants(
    auth: AuthContext = Depends(need("iam:tenant:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import list_tenants

    del auth
    return list_tenants(platform)


@router.get("/api/admin/roles")
def admin_roles(auth: AuthContext = Depends(need("iam:role:view"))) -> dict:
    from app.admin.catalog import list_roles

    del auth
    return list_roles()


@router.get("/api/admin/permissions")
def admin_permissions(auth: AuthContext = Depends(need("iam:role:view"))) -> dict:
    from app.admin.catalog import permissions_tree

    del auth
    return permissions_tree()


@router.get("/api/admin/agents/{agent_id}/industry-config")
def admin_agent_industry_config(
    agent_id: str,
    auth: AuthContext = Depends(need("agent:instance:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import agent_industry_config

    try:
        return agent_industry_config(platform, auth, agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc


@router.get("/api/admin/kb/feedback")
def admin_kb_feedback_list(
    auth: AuthContext = Depends(need("kb:feedback:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import list_feedback

    return list_feedback(platform, auth)


@router.post("/api/admin/kb/feedback")
def admin_kb_feedback_create(
    body: FeedbackIn,
    auth: AuthContext = Depends(need("kb:feedback:write")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import create_feedback

    return create_feedback(platform, auth, body.model_dump())


@router.get("/api/admin/tickets/human")
def admin_human_tickets(
    auth: AuthContext = Depends(need("ticket:human:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import list_human_tickets

    return list_human_tickets(platform, auth)


@router.post("/api/admin/tickets/human")
def admin_human_ticket_create(
    body: HumanTicketIn,
    auth: AuthContext = Depends(need("ticket:human:handle")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import create_human_ticket

    return create_human_ticket(platform, auth, body.model_dump())


@router.post("/api/admin/tickets/human/{ticket_id}/resolve")
def admin_human_ticket_resolve(
    ticket_id: str,
    body: HumanResolveIn,
    auth: AuthContext = Depends(need("ticket:human:handle")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import resolve_human_ticket

    try:
        return resolve_human_ticket(platform, auth, ticket_id, note=body.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="human ticket not found") from exc
