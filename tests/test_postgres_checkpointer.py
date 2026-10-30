"""Real PostgresSaver init + checkpoint round-trip.

Gate: ``RUN_PG_INTEGRATION=1`` + postgresql ``DATABASE_URL``.
Requires ``CHECKPOINTER_BACKEND=postgres`` (set by fixture).
URL-string unit tests are NOT a substitute for this file.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from app.agents.checkpoint import (
    close_checkpointer,
    get_postgres_checkpointer,
    init_checkpointer,
    to_psycopg_conninfo,
)
from app.infra.db import dispose_db

_RUN = os.getenv("RUN_PG_INTEGRATION") == "1"
_URL = (os.getenv("DATABASE_URL") or "").strip()

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set RUN_PG_INTEGRATION=1 and postgresql DATABASE_URL",
)


@pytest.fixture
def pg_checkpointer_env(monkeypatch):
    if not _URL.startswith("postgresql"):
        pytest.fail(
            "DATABASE_URL must start with postgresql when RUN_PG_INTEGRATION=1 "
            f"(got {(_URL[:40] + '…') if len(_URL) > 40 else (_URL or '<empty>')!r})"
        )
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "postgres")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", _URL)
    close_checkpointer()
    dispose_db()
    yield
    close_checkpointer()
    dispose_db()


def test_memory_backend_skips_postgres_saver(monkeypatch):
    """Default memory checkpointer must not open PostgresSaver."""
    if not _URL.startswith("postgresql"):
        pytest.fail("DATABASE_URL required")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setenv("DATABASE_URL", _URL)
    monkeypatch.setenv("APP_ENV", "test")
    close_checkpointer()
    init_checkpointer()
    assert get_postgres_checkpointer() is None
    close_checkpointer()


def test_postgres_saver_round_trip(pg_checkpointer_env):
    conninfo = to_psycopg_conninfo(_URL)
    assert conninfo.startswith("postgresql://")
    assert "+psycopg" not in conninfo.split("://", 1)[0]

    init_checkpointer()
    saver = get_postgres_checkpointer()
    assert saver is not None, "PostgresSaver failed to initialize"

    thread_id = f"cp_{uuid.uuid4().hex[:16]}"
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    # Minimal empty checkpoint via LangGraph serializer helpers.
    from langgraph.checkpoint.base import empty_checkpoint

    checkpoint = empty_checkpoint()
    metadata = {"source": "phase58", "step": 0}
    # put returns a new config with checkpoint_id
    next_config = saver.put(config, checkpoint, metadata, {})
    assert next_config.get("configurable", {}).get("checkpoint_id")

    tup = saver.get_tuple(next_config)
    assert tup is not None, "expected checkpoint readable after put"
    assert tup.checkpoint is not None
    assert tup.checkpoint.get("id") == checkpoint.get("id")

    close_checkpointer()
    assert get_postgres_checkpointer() is None
