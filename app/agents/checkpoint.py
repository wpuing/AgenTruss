"""LangGraph checkpointer lifecycle (Memory vs Postgres)."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_pg_saver: Any | None = None
_pg_cm: Any | None = None


def to_psycopg_conninfo(url: str) -> str:
    """Convert SQLAlchemy-style URLs to libpq/psycopg conninfo.

    ``PostgresSaver`` / ``psycopg.Connection.connect`` reject
    ``postgresql+psycopg://`` (ProgrammingError: missing '=').
    """
    raw = (url or "").strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if raw.startswith(prefix):
            return "postgresql://" + raw[len(prefix) :]
    return raw


def get_postgres_checkpointer():
    return _pg_saver


def init_checkpointer() -> None:
    """Open PostgresSaver when durable storage + postgres checkpointer are enabled."""
    global _pg_saver, _pg_cm
    settings = get_settings()
    if settings.storage_backend != "postgres" or not settings.database_url:
        return
    # Default checkpointer_backend=memory: skip durable saver (MemorySaver at resolve).
    if settings.checkpointer_backend != "postgres":
        logger.info(
            "checkpointer_backend=%s; skip PostgresSaver",
            settings.checkpointer_backend,
        )
        return
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        logger.warning("langgraph-checkpoint-postgres not installed")
        if settings.is_production:
            raise RuntimeError("production requires langgraph-checkpoint-postgres") from exc
        return
    conninfo = to_psycopg_conninfo(settings.database_url)
    # Keep context manager open for process lifetime.
    _pg_cm = PostgresSaver.from_conn_string(conninfo)
    _pg_saver = _pg_cm.__enter__()
    _pg_saver.setup()
    logger.info("langgraph postgres checkpointer ready")


def close_checkpointer() -> None:
    global _pg_saver, _pg_cm
    if _pg_cm is not None:
        try:
            _pg_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
    _pg_saver = None
    _pg_cm = None
