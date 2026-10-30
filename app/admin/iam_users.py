"""IAM user write path: create / update / disable / assign roles (backend-enforced)."""

from __future__ import annotations

from typing import Any

from app.admin.audit_events import record_admin_action
from app.core.passwords import hash_password
from app.core.platform import Platform, new_id
from app.core.rbac import perms_for_roles
from app.core.security import AuthContext

PROTECTED_USERNAMES = frozenset({"admin", "finadmin"})


def _find_user(platform: Platform, user_id: str) -> dict | None:
    for row in platform.users.values():
        if row.get("id") == user_id:
            return row
    return None


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "tenant_id": row["tenant_id"],
        "roles": list(row.get("roles") or []),
        "perms": sorted(row.get("perms") or []),
        "status": row.get("status") or "active",
    }


def get_user(platform: Platform, auth: AuthContext, user_id: str) -> dict:
    row = _find_user(platform, user_id)
    if not row or row.get("tenant_id") != auth.tenant_id:
        raise KeyError(user_id)
    return _public_user(row)


def create_user(platform: Platform, auth: AuthContext, body: dict[str, Any]) -> dict:
    """Create user in caller's tenant. Roles must be subset of caller's authority."""
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    roles = list(body.get("roles") or [])
    if not username or len(username) > 64 or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid username")
    if len(password) < 8 or len(password) > 128:
        raise ValueError("password length must be 8..128")
    if not roles:
        raise ValueError("roles required")
    if any(u["username"] == username and u["tenant_id"] == auth.tenant_id for u in platform.users.values()):
        raise ValueError("username already exists in tenant")

    new_perms = perms_for_roles(roles)
    if not new_perms.issubset(auth.perms):
        raise PermissionError("cannot grant permissions you do not hold")

    uid = new_id("usr")
    row = {
        "id": uid,
        "username": username,
        "password": hash_password(password),
        "tenant_id": auth.tenant_id,
        "roles": roles,
        "perms": new_perms,
        "status": "active",
    }
    platform.users[username] = row
    _persist_user(row)
    record_admin_action(
        platform,
        auth,
        "iam_user_create",
        {"target_user_id": uid, "username": username, "roles": roles},
    )
    return _public_user(row)


def update_user(platform: Platform, auth: AuthContext, user_id: str, body: dict[str, Any]) -> dict:
    """Update status / roles / password. Blocks privilege escalation and cross-tenant."""
    row = _find_user(platform, user_id)
    if not row or row.get("tenant_id") != auth.tenant_id:
        raise KeyError(user_id)

    if "roles" in body and body["roles"] is not None:
        roles = list(body["roles"])
        new_perms = perms_for_roles(roles)
        if not new_perms.issubset(auth.perms):
            raise PermissionError("cannot grant permissions you do not hold")
        if row["id"] == auth.user_id and not new_perms.issubset(set(row.get("perms") or [])):
            raise PermissionError("cannot escalate own permissions")
        if row.get("username") in PROTECTED_USERNAMES and "tenant_admin" not in roles:
            raise ValueError("protected demo admin must keep tenant_admin role")
        row["roles"] = roles
        row["perms"] = new_perms

    if "status" in body and body["status"] is not None:
        status = str(body["status"])
        if status not in {"active", "disabled"}:
            raise ValueError("status must be active|disabled")
        if row["id"] == auth.user_id and status == "disabled":
            raise ValueError("cannot disable yourself")
        row["status"] = status

    if body.get("password"):
        password = str(body["password"])
        if len(password) < 8 or len(password) > 128:
            raise ValueError("password length must be 8..128")
        row["password"] = hash_password(password)

    platform.users[row["username"]] = row
    _persist_user(row)
    record_admin_action(
        platform,
        auth,
        "iam_user_update",
        {
            "target_user_id": user_id,
            "roles": row.get("roles"),
            "status": row.get("status"),
            "password_reset": bool(body.get("password")),
        },
    )
    return _public_user(row)


def _persist_user(row: dict) -> None:
    from app.infra.ticket_repo import persistence_enabled

    if not persistence_enabled():
        return
    from app.infra.entity_repo import upsert_user

    upsert_user(row)
