"""PG smoke: agent KB binding + mock vector rehydrate across app restart.

Gate: RUN_PG_INTEGRATION=1 + postgresql DATABASE_URL (test DB only).
skip ≠ PASS.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.infra.db import create_all, dispose_db, init_db
from app.main import create_app
from app.rag.vector_store import get_mock_vector_store, reset_mock_vector_store
from tests.conftest import auth_header, login
_RUN = os.getenv("RUN_PG_INTEGRATION") == "1"
_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set RUN_PG_INTEGRATION=1 and postgresql DATABASE_URL (test DB only)",
)


def _ingest_unique(client, token: str, suffix: str) -> tuple[str, str]:
    """Ingest with unique KB code to tolerate a dirty shared *_test database."""
    headers = auth_header(token)
    code = f"help-{suffix}"[:48]
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=headers,
        json={"name": f"帮助中心-{suffix}", "code": code},
    )
    assert kb.status_code == 200, kb.text
    kb_id = kb.json()["id"]
    files = {"file": ("policy.md", "退货政策：七天无理由退货。".encode(), "text/markdown")}
    doc = client.post(f"/api/admin/kb/{kb_id}/documents", headers=headers, files=files)
    assert doc.status_code == 200, doc.text
    doc_id = doc.json()["id"]
    assert client.post(f"/api/admin/documents/{doc_id}/parse", headers=headers).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/chunk", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/vectorize", headers=headers).json()["status"] == "vectorized"
    return kb_id, doc_id


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
    # PG body_text path must not require live MinIO even if .env has MINIO_*.
    monkeypatch.setenv("MINIO_ENDPOINT", "")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "")
    monkeypatch.setenv("MINIO_SECRET_KEY", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "120")
    dispose_db()
    init_db()
    create_all()
    app = create_app()
    with TestClient(app) as client:
        yield client
    dispose_db()
    reset_mock_vector_store()


def test_agent_kb_binding_survives_restart(pg_client, monkeypatch):
    token = login(pg_client, "admin", "demo")
    headers = auth_header(token)
    suffix = os.urandom(4).hex()
    kb_id, _doc_id = _ingest_unique(pg_client, token, suffix)
    agent_id = pg_client.get("/api/admin/agents", headers=headers).json()["items"][0]["agent_id"]
    assert agent_id == "agt-demo-default"
    patched = pg_client.patch(
        f"/api/admin/agents/{agent_id}",
        headers=headers,
        json={"kb_id": kb_id, "rag_enabled": True},
    ).json()
    assert patched["kb_binding"]["kb_id"] == kb_id
    assert patched["kb_binding"]["rag_enabled"] is True

    # Simulate process restart: new app + recover_on_startup hydrate.
    dispose_db()
    init_db()
    reset_mock_vector_store()
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("MINIO_ENDPOINT", "")
    app2 = create_app()
    with TestClient(app2) as client2:
        token2 = login(client2, "admin", "demo")
        headers2 = auth_header(token2)
        agents = client2.get("/api/admin/agents", headers=headers2).json()["items"]
        row = next(a for a in agents if a["agent_id"] == "agt-demo-default")
        assert row["kb_binding"]["kb_id"] == kb_id
        assert row["kb_binding"]["rag_enabled"] is True
        # Mock store was cleared; recovery should have rehydrated indexed children.
        assert get_mock_vector_store()._by_id
        resp = client2.post(
            "/api/admin/kb/retrieve-test",
            headers=headers2,
            json={"kb_id": kb_id, "query": "退货政策"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["hits"]
        chat = client2.post(
            "/api/chat",
            headers=headers2,
            json={"message": "退货政策", "agent_id": agent_id},
        ).json()
        assert chat.get("sources")


def test_execution_detail_api(pg_client):
    token = login(pg_client, "admin", "demo")
    headers = auth_header(token)
    pg_client.post("/api/chat", headers=headers, json={"message": "查询订单 ORD-9"})
    items = pg_client.get("/api/admin/executions", headers=headers).json()["items"]
    assert items
    run_id = items[0]["run_id"]
    detail = pg_client.get(f"/api/admin/executions/{run_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["run_id"] == run_id
    assert body.get("_meta")
