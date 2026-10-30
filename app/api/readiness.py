"""Shared readiness / dependency probes for /ready and system status."""

from __future__ import annotations

from app.core.config import get_settings


def collect_ready_checks(*, include_minio: bool = False) -> tuple[bool, dict]:
    """Return (ok, checks) for configured backends. Does not claim production readiness."""
    settings = get_settings()
    checks: dict = {
        "storage_backend": settings.storage_backend,
        "workflow_engine": settings.workflow_engine,
    }
    ok = True
    if settings.storage_backend == "postgres":
        from app.infra.db import db_healthy

        checks["postgres"] = db_healthy()
        ok = ok and bool(checks["postgres"])
    else:
        checks["platform"] = "memory"
    if settings.redis_url:
        from app.infra.redis_client import redis_healthy

        checks["redis"] = redis_healthy()
        if settings.is_production or settings.redis_required:
            ok = ok and bool(checks["redis"])
    elif include_minio:
        checks["redis"] = "not_configured"
    vector_backend = settings.effective_vector_store_backend()
    if vector_backend == "milvus":
        from app.storage.milvus_store import milvus_healthy

        checks["vector_store"] = "milvus"
        checks["milvus"] = milvus_healthy()
        ok = ok and bool(checks["milvus"])
    else:
        checks["vector_store"] = "mock"
        if include_minio:
            checks["mock_rag"] = True
    if include_minio:
        checks["minio"] = "configured" if settings.minio_endpoint else "not_configured"
    return ok, checks
