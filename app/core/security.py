"""JWT issue and parse. Identity comes only from claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from app.core.config import get_settings
from app.core.token_store import get_token_store


@dataclass
class AuthContext:
    """Authenticated caller."""

    user_id: str
    tenant_id: str
    roles: list[str]
    perms: set[str]
    jti: str | None = None


def issue_token(ctx: AuthContext) -> str:
    """Sign a bearer token with iat/exp/jti claims."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    jti = ctx.jti or str(uuid4())
    payload = {
        "user_id": ctx.user_id,
        "tenant_id": ctx.tenant_id,
        "roles": ctx.roles,
        "perms": sorted(ctx.perms),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "jti": jti,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def parse_token(token: str) -> AuthContext:
    """Decode a bearer token. Raises jwt.PyJWTError when invalid/expired/revoked."""
    settings = get_settings()
    data = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"require": ["exp", "iat", "jti"]},
    )
    jti = str(data["jti"])
    if get_token_store(settings).is_revoked(jti):
        raise jwt.InvalidTokenError("token revoked")
    return AuthContext(
        user_id=str(data["user_id"]),
        tenant_id=str(data["tenant_id"]),
        roles=list(data.get("roles") or []),
        perms=set(data.get("perms") or []),
        jti=jti,
    )


def revoke_token(token: str) -> None:
    """Revoke a bearer token until its natural expiry."""
    settings = get_settings()
    data = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"require": ["exp", "jti"], "verify_exp": False},
    )
    jti = str(data["jti"])
    exp = data["exp"]
    if isinstance(exp, datetime):
        expires_at = exp.timestamp()
    else:
        expires_at = float(exp)
    get_token_store(settings).revoke(jti, expires_at=expires_at)
