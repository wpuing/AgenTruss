"""Tenant-scoped audit read helpers (memory + postgres).

Keeps list/detail/trace routes from re-implementing the same source selection.
"""

from __future__ import annotations

from typing import Any

from app.core.platform import Platform
from app.security.redaction import redact_for_api


def list_tenant_audits(platform: Platform, tenant_id: str, *, limit: int | None = None) -> tuple[list[dict[str, Any]], str]:
    """Return redacted audits for tenant and a source label (postgres|memory)."""
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_audits

        raw = list_audits(tenant_id, limit=limit) if limit is not None else list_audits(tenant_id)
        return [redact_for_api(row) for row in raw], "postgres"
    rows = [redact_for_api(row) for row in platform.audits if row.get("tenant_id") == tenant_id]
    if limit is not None:
        rows = rows[-limit:] if limit > 0 else rows
    return rows, "memory"


def get_tenant_audit(platform: Platform, tenant_id: str, audit_id: str) -> dict[str, Any] | None:
    """Find one audit by id within tenant; None if missing."""
    rows, _source = list_tenant_audits(platform, tenant_id, limit=500)
    for row in rows:
        if str(row.get("audit_id")) == audit_id:
            return row
    return None


def audits_for_session(platform: Platform, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
    """Audits linked to a session (prefer memory hits, else postgres scan)."""
    mem = [
        redact_for_api(dict(a))
        for a in platform.audits
        if a.get("tenant_id") == tenant_id and a.get("session_id") == session_id
    ]
    if mem:
        return mem
    from app.infra.ticket_repo import persistence_enabled

    if not persistence_enabled():
        return []
    rows, _ = list_tenant_audits(platform, tenant_id, limit=200)
    return [a for a in rows if a.get("session_id") == session_id]
