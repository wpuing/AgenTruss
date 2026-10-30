"""Shared FastAPI dependencies and admin write error mapping."""

from __future__ import annotations

import jwt
from fastapi import Depends, Header, HTTPException, Request

from app.core.platform import Platform
from app.core.security import AuthContext, parse_token


def platform_dep(request: Request) -> Platform:
    return request.app.state.platform


def current_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthContext:
    """Trust bearer claims, then re-check live user status/perms from platform."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    try:
        ctx = parse_token(authorization.split(" ", 1)[1])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    platform = getattr(request.app.state, "platform", None)
    if platform is not None:
        user = next((u for u in platform.users.values() if u.get("id") == ctx.user_id), None)
        if user is not None:
            if user.get("status") != "active":
                raise HTTPException(status_code=401, detail="account disabled")
            if user.get("tenant_id") != ctx.tenant_id:
                raise HTTPException(status_code=401, detail="invalid token")
            # Role/perm changes take effect immediately (JWT alone is not authority).
            ctx.roles = list(user.get("roles") or [])
            ctx.perms = set(user.get("perms") or [])
    return ctx


def need(perm: str):
    """Build a dependency that checks one permission code."""

    def checker(auth: AuthContext = Depends(current_auth)) -> AuthContext:
        if perm not in auth.perms:
            raise HTTPException(status_code=403, detail="forbidden")
        return auth

    return checker


def need_any(*perms: str):
    """Accept any one of the given permission codes (menu view vs action)."""

    def checker(auth: AuthContext = Depends(current_auth)) -> AuthContext:
        if not any(perm in auth.perms for perm in perms):
            raise HTTPException(status_code=403, detail="forbidden")
        return auth

    return checker


def map_admin_write_error(exc: BaseException, *, not_found: str) -> HTTPException:
    """Map admin Application KeyError/ValueError/PermissionError/ConflictError → HTTP."""
    from app.infra.entity_repo import ConflictError

    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=not_found)
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc  # type: ignore[misc]
