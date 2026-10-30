"""JWT revocation store. Memory for tests; Redis/Postgres for durable modes."""

from __future__ import annotations

import threading
import time
from typing import Protocol

from app.core.config import Settings, get_settings


class TokenRevocationStore(Protocol):
    def revoke(self, jti: str, *, expires_at: float) -> None: ...

    def is_revoked(self, jti: str) -> bool: ...


class InMemoryTokenRevocationStore:
    """Process-local blacklist with TTL sweep. Not multi-instance safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, float] = {}

    def revoke(self, jti: str, *, expires_at: float) -> None:
        with self._lock:
            self._items[jti] = expires_at
            self._sweep_unlocked()

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            self._sweep_unlocked()
            exp = self._items.get(jti)
            if exp is None:
                return False
            if exp <= time.time():
                self._items.pop(jti, None)
                return False
            return True

    def _sweep_unlocked(self) -> None:
        now = time.time()
        dead = [k for k, exp in self._items.items() if exp <= now]
        for key in dead:
            del self._items[key]


class RedisTokenRevocationStore:
    """Redis-backed blacklist. Keys: agenttruss:jwt:revoke:{jti}."""

    def __init__(self, client) -> None:
        self._client = client

    def _key(self, jti: str) -> str:
        return f"agentruss:jwt:revoke:{jti}"

    def revoke(self, jti: str, *, expires_at: float) -> None:
        ttl = max(1, int(expires_at - time.time()))
        self._client.setex(self._key(jti), ttl, "1")

    def is_revoked(self, jti: str) -> bool:
        return bool(self._client.exists(self._key(jti)))


_store: TokenRevocationStore | None = None


def get_token_store(settings: Settings | None = None) -> TokenRevocationStore:
    global _store
    if _store is not None:
        return _store
    cfg = settings or get_settings()
    if cfg.redis_url:
        try:
            from app.infra.redis_client import get_redis

            client = get_redis()
            if client is not None:
                _store = RedisTokenRevocationStore(client)
                return _store
        except Exception:  # noqa: BLE001
            if cfg.is_production or cfg.redis_required:
                raise
    _store = InMemoryTokenRevocationStore()
    return _store


def reset_token_store() -> None:
    """Test helper."""
    global _store
    _store = None
