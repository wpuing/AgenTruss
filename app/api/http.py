"""HTTP API. Routes validate, authorize, and delegate."""

from __future__ import annotations

import logging

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.api.admin_catalog_routes import router as admin_catalog_router
from app.api.admin_config_routes import router as admin_config_router
from app.api.admin_kb_routes import router as admin_kb_router
from app.api.admin_ops_routes import router as admin_ops_router
from app.api.admin_runtime_routes import router as admin_runtime_router
from app.api.admin_ticket_routes import router as admin_ticket_router
from app.api.deps import current_auth, need_any, platform_dep
from app.api.readiness import collect_ready_checks
from app.core.config import ROOT, get_settings
from app.core.passwords import verify_password
from app.core.platform import Platform
from app.core.security import AuthContext, issue_token, revoke_token

# Re-export deps for callers that historically imported from http.
_need_any = need_any

logger = logging.getLogger(__name__)
router = APIRouter()
router.include_router(admin_config_router)
router.include_router(admin_catalog_router)
router.include_router(admin_kb_router)
router.include_router(admin_ticket_router)
router.include_router(admin_ops_router)
router.include_router(admin_runtime_router)


class LoginIn(BaseModel):
    username: str
    password: str
    tenant_code: str


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready():
    """Readiness reflects configured backends; non-ready returns HTTP 503."""
    ok, checks = collect_ready_checks(include_minio=False)
    body = {"status": "ready" if ok else "not_ready", "checks": checks}
    if not ok:
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/admin")
@router.get("/admin/")
def admin_page() -> FileResponse:
    """Admin SPA entry. Uses real JWT login, never a fake local bypass."""
    return FileResponse(
        ROOT / "app" / "static" / "admin" / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/static/{asset_path:path}")
def admin_asset(asset_path: str) -> FileResponse:
    """Serve admin CSS/JS from the package static directory."""
    base = (ROOT / "app" / "static" / "admin").resolve()
    target = (base / asset_path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(target, headers={"Cache-Control": "public, max-age=60"})


@router.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
    """Revoke the current bearer token (jti blacklist until natural expiry)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    token = authorization.split(" ", 1)[1]
    try:
        revoke_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    return {"ok": True, "revoked": True}


def _client_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For unless a trusted gateway is configured later.
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _login_allowed(platform: Platform, *, username: str, ip: str, limit: int) -> bool:
    """Shared login throttle across workers when Redis is configured."""
    from app.infra.redis_client import get_redis, redis_rate_limit

    key = f"login:{username.lower()}:{ip}"
    if get_redis() is not None:
        return redis_rate_limit(key, limit, 60)
    import time

    now = time.time()
    bucket = [stamp for stamp in platform.rate_buckets.get(key, []) if now - stamp < 60]
    if len(bucket) >= limit:
        platform.rate_buckets[key] = bucket
        return False
    bucket.append(now)
    platform.rate_buckets[key] = bucket
    return True


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, platform: Platform = Depends(platform_dep)) -> dict:
    settings = get_settings()
    if not settings.allow_demo_users and not platform.users:
        raise HTTPException(status_code=503, detail="demo login disabled")
    if not _login_allowed(
        platform,
        username=body.username,
        ip=_client_ip(request),
        limit=settings.login_rate_limit_per_minute,
    ):
        raise HTTPException(status_code=429, detail="too many login attempts")
    from app.infra.ticket_repo import persistence_enabled

    user = None
    if persistence_enabled():
        from app.infra.entity_repo import find_user_by_username

        user = find_user_by_username(body.username, body.tenant_code)
        if user:
            platform.users[user["username"]] = user
    if user is None:
        user = platform.user_by_name(body.username)
    if (
        not user
        or not verify_password(body.password, user["password"])
        or user["tenant_id"] != body.tenant_code
        or user["status"] != "active"
    ):
        raise HTTPException(status_code=401, detail="login failed")
    token = issue_token(platform.auth_for(user))
    logger.info("login ok user=%s tenant=%s", user["username"], user["tenant_id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "tenant_id": user["tenant_id"],
        "roles": user["roles"],
        "expires_in_minutes": settings.jwt_expire_minutes,
    }


@router.get("/api/admin/me")
def me(auth: AuthContext = Depends(current_auth), platform: Platform = Depends(platform_dep)) -> dict:
    user = next((row for row in platform.users.values() if row["id"] == auth.user_id), None)
    return {
        "user_id": auth.user_id,
        "username": user["username"] if user else auth.user_id,
        "tenant_id": auth.tenant_id,
        "roles": auth.roles,
        "perms": sorted(auth.perms),
    }


@router.get("/api/admin/me/menus")
def menus(phase: str = "MVP", auth: AuthContext = Depends(current_auth), platform: Platform = Depends(platform_dep)) -> dict:
    return {"menus": platform.visible_menus(auth.perms, phase)}
