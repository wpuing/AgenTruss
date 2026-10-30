"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.agents.checkpoint import close_checkpointer, init_checkpointer
from app.agents.recovery import recover_on_startup
from app.api.http import router
from app.core.config import get_settings
from app.core.platform import Platform
from app.core.token_store import reset_token_store
from app.infra.db import create_all, dispose_db, init_db
from app.infra.redis_client import close_redis
from app.security.redaction import install_logging_redaction, sanitize_exception

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
install_logging_redaction()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_runtime()
    install_logging_redaction()
    reset_token_store()
    if settings.storage_backend == "postgres":
        init_db()
        # Production schema must come from Alembic — never treat create_all as migration.
        if not settings.is_production:
            create_all()
        else:
            logger.info("production postgres: skip create_all; run alembic upgrade head before start")
        init_checkpointer()
    platform: Platform = app.state.platform
    recovery = recover_on_startup(platform)
    app.state.recovery = recovery
    logger.info("startup recovery: %s", recovery.get("note"))
    yield
    close_checkpointer()
    close_redis()
    dispose_db()


def create_app() -> FastAPI:
    """Create an app with platform state and optional durable backends."""
    settings = get_settings()
    settings.validate_runtime()
    app = FastAPI(title="AgenTruss", version="0.2.0", lifespan=lifespan)
    app.state.platform = Platform.seed()
    app.state.recovery = {"note": "pending_startup"}
    app.include_router(router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        # Admin SPA assets may override Cache-Control; API stays no-store by default.
        if request.url.path.startswith("/admin/static/"):
            response.headers["Cache-Control"] = "public, max-age=60"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, str):
            detail = sanitize_exception(detail)
        return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": detail})

    return app


app = create_app()
