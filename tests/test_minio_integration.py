"""Real MinIO object-store roundtrip for Knowledge document bodies.

Gate: ``RUN_MINIO_INTEGRATION=1`` + MinIO env + postgres ``DATABASE_URL``.
Fake MinIO clients are not used. Skip ≠ pass for the dedicated CI job.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.infra.db import create_all, dispose_db, init_db
from app.main import create_app
from tests.conftest import auth_header, login
from tests.pg_safety import assert_pg_reset_allowed

_RUN = os.getenv("RUN_MINIO_INTEGRATION") == "1"
_URL = (os.getenv("DATABASE_URL") or "").strip()
_MINIO = (os.getenv("MINIO_ENDPOINT") or "").strip()

pytestmark = pytest.mark.skipif(
    not _RUN or not _URL.startswith("postgresql") or not _MINIO,
    reason="set RUN_MINIO_INTEGRATION=1, postgresql DATABASE_URL, MINIO_ENDPOINT",
)


@pytest.fixture
def minio_app(monkeypatch):
    assert_pg_reset_allowed(_URL)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOW_DEMO_USERS", "true")
    monkeypatch.setenv("DATABASE_URL", _URL)
    from sqlalchemy import create_engine, text

    engine = create_engine(_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
    engine.dispose()
    dispose_db()
    init_db()
    create_all()
    app = create_app()
    with TestClient(app) as client:
        yield client
    dispose_db()


def test_minio_upload_read_delete_roundtrip(minio_app):
    client = minio_app
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=headers,
        json={"name": "minio-kb", "code": "miniokb"},
    ).json()
    body = ("MinIO全文持久化验证。" * 80) + "\nMINIO_MARK"
    doc = client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=headers,
        files={"file": ("m.md", body.encode("utf-8"), "text/markdown")},
    ).json()
    assert doc.get("storage") == "minio"
    assert doc.get("object_key")
    detail = client.get(f"/api/admin/documents/{doc['id']}", headers=headers).json()
    assert detail["text"].endswith("MINIO_MARK")
    assert len(detail["text"]) == len(body)
    assert detail.get("storage") == "minio"
    # Clear hot memory then reread via hydrate
    platform = client.app.state.platform
    platform.documents.pop(doc["id"], None)
    again = client.get(f"/api/admin/documents/{doc['id']}", headers=headers).json()
    assert again["text"].endswith("MINIO_MARK")
    assert client.delete(f"/api/admin/documents/{doc['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/admin/documents/{doc['id']}", headers=headers).status_code == 404
