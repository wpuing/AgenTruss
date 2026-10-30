"""Redis connection helper. Production must not silently skip when REDIS_URL is set."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_client: Any | None = None
_failed = False


def get_redis():
    """Return a redis client or None when URL unset (dev memory mode)."""
    global _client, _failed
    if _client is not None:
        return _client
    if _failed:
        return None
    settings = get_settings()
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _client = client
        logger.info("redis connected")
        return _client
    except Exception as exc:  # noqa: BLE001
        logger.error("redis connect failed: %s", exc)
        if settings.is_production or settings.redis_required:
            raise
        _failed = True
        return None


def close_redis() -> None:
    global _client, _failed
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
    _client = None
    _failed = False


def redis_rate_limit(key: str, limit: int, window_seconds: int = 60) -> bool:
    """Return True if allowed. Uses INCR+EXPIRE.

    When Redis is configured and required (production), a missing client is
    fail-closed (deny) rather than silently allowing unlimited traffic.
    Dev/memory mode returns True so the caller can use an in-process bucket.
    """
    settings = get_settings()
    client = get_redis()
    if client is None:
        url = (settings.redis_url or "").strip()
        if url and (settings.is_production or settings.redis_required):
            logger.error("redis rate limit unavailable; denying key=%s", key)
            return False
        return True  # caller uses in-process bucket
    full = f"agentruss:rl:{key}"
    count = client.incr(full)
    if count == 1:
        client.expire(full, window_seconds)
    return int(count) <= limit


def redis_healthy() -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception:  # noqa: BLE001
        return False
