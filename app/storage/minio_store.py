"""MinIO object storage adapter.

Status: adapter skeleton — real put/get require STORAGE_BACKEND extras and MINIO_*.
Knowledge upload still uses process memory until phase-5 wiring is completed in rag/service.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MinioNotConfiguredError(RuntimeError):
    pass


def client_or_raise(*, connect_timeout: float = 3.0, read_timeout: float = 5.0):
    settings = get_settings()
    if not settings.minio_endpoint or not settings.minio_access_key:
        raise MinioNotConfiguredError("MINIO_ENDPOINT / credentials not configured")
    try:
        from minio import Minio
        import urllib3
    except ImportError as exc:
        raise MinioNotConfiguredError('install with: pip install -e ".[storage]"') from exc
    http = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=connect_timeout, read=read_timeout),
        retries=False,
    )
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
        http_client=http,
    )


def minio_healthy(*, timeout_sec: float = 5.0) -> bool:
    """Side-effect-light readiness: bucket_exists (no put/delete).

    Unconfigured / missing client → False. Bounded by connect/read timeouts.
    """
    settings = get_settings()
    if not settings.minio_endpoint or not settings.minio_access_key:
        return False
    bucket = (settings.minio_bucket or "").strip()
    if not bucket:
        return False
    try:
        client = client_or_raise(
            connect_timeout=min(3.0, timeout_sec),
            read_timeout=timeout_sec,
        )
        return bool(client.bucket_exists(bucket))
    except Exception as exc:  # noqa: BLE001
        logger.warning("minio health probe failed: %s", exc)
        return False


def put_bytes(object_key: str, data: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
    """Upload bytes; returns metadata. Raises when MinIO is not configured."""
    settings = get_settings()
    client = client_or_raise()
    import io

    client.put_object(
        settings.minio_bucket,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return {"bucket": settings.minio_bucket, "object_key": object_key, "size": len(data)}


def get_bytes(object_key: str) -> bytes:
    """Download object bytes. Raises when missing or MinIO not configured."""
    settings = get_settings()
    client = client_or_raise()
    response = client.get_object(settings.minio_bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_object(object_key: str) -> None:
    settings = get_settings()
    client = client_or_raise()
    client.remove_object(settings.minio_bucket, object_key)
