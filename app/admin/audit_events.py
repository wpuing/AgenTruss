"""Shared admin-side audit append (memory + optional postgres).

Keeps component / channel / IAM writers from duplicating the same append path.
Does not replace chat/runtime audit shapes used by agents.
"""

from __future__ import annotations

import logging
from typing import Any

from app.admin.meta import utc_now_iso
from app.core.platform import Platform, new_id
from app.core.security import AuthContext
from app.security.redaction import redact_for_api

logger = logging.getLogger(__name__)


def record_admin_action(
    platform: Platform,
    auth: AuthContext,
    action: str,
    detail: dict[str, Any] | None = None,
    *,
    redact_detail: bool = True,
) -> dict[str, Any]:
    """Append an admin action audit row; persist when STORAGE_BACKEND=postgres."""
    safe_detail: dict[str, Any] = dict(detail or {})
    if redact_detail:
        redacted = redact_for_api(safe_detail)
        safe_detail = redacted if isinstance(redacted, dict) else safe_detail
    row: dict[str, Any] = {
        "audit_id": new_id("aud"),
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
        "action": action,
        "detail": safe_detail,
        "created_at": utc_now_iso(),
    }
    platform.audits.append(row)
    try:
        from app.infra.entity_repo import append_audit

        append_audit(row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("admin audit persist skipped action=%s err=%s", action, exc)
    return row
