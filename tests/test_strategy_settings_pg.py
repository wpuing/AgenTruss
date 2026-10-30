"""PG: strategy PATCH + settings_kv survive app restart.

Gate: RUN_PG_INTEGRATION=1 + postgresql DATABASE_URL (test DB only).
skip ≠ PASS.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.infra.db import create_all, dispose_db, init_db
from app.main import create_app
from tests.conftest import auth_header, login

_RUN = os.getenv("RUN_PG_INTEGRATION") == "1"
_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set RUN_PG_INTEGRATION=1 and postgresql DATABASE_URL (test DB only)",
)


@pytest.fixture
def pg_client(monkeypatch):
    if not _URL.startswith("postgresql"):
        pytest.fail("DATABASE_URL must be postgresql when RUN_PG_INTEGRATION=1")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_DEMO_USERS", "true")
    monkeypatch.setenv("DATABASE_URL", _URL)
    monkeypatch.setenv("VECTOR_STORE_BACKEND", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    # Isolate login throttle from shared Redis when RUN_REDIS_INTEGRATION co-runs.
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "120")
    dispose_db()
    init_db()
    create_all()
    # Ensure alembic-aligned tables exist even if create_all misses newer models
    app = create_app()
    with TestClient(app) as client:
        yield client
    dispose_db()


def test_strategy_and_component_survive_restart(pg_client, monkeypatch):
    token = login(pg_client, "admin", "demo")
    headers = auth_header(token)
    rows = pg_client.get("/api/admin/chunk-strategies", headers=headers).json()["items"]
    row = next(r for r in rows if r["id"] == "cs-baseline")
    v = int(row.get("version") or 1)
    patched = pg_client.patch(
        f"/api/admin/chunk-strategies/{row['id']}",
        headers=headers,
        json={"chunk_size": 77, "expected_version": v},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["chunk_size"] == 77

    saved = pg_client.put(
        "/api/admin/components/embedding",
        headers=headers,
        json={"embedding_provider": "mock", "embedding_dim": 8},
    )
    assert saved.status_code == 200, saved.text

    dispose_db()
    init_db()
    create_all()
    app2 = create_app()
    with TestClient(app2) as client2:
        token2 = login(client2, "admin", "demo")
        headers2 = auth_header(token2)
        again = client2.get("/api/admin/chunk-strategies", headers=headers2).json()["items"]
        current = next(r for r in again if r["id"] == "cs-baseline")
        assert current["chunk_size"] == 77
        detail = client2.get("/api/admin/components/embedding", headers=headers2).json()
        assert detail["fields"]["embedding_provider"] == "mock"
