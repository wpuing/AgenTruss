"""Document full-body durability via postgres body_text (not preview-only)."""

from __future__ import annotations

import pytest

from app.infra.db import create_all, dispose_db, init_db
from app.infra.entity_repo import get_document_row, upsert_document_meta, upsert_kb
from app.rag.document_body import hydrate_document_text


@pytest.fixture
def durable_db(tmp_path, monkeypatch):
    db_path = tmp_path / "body.db"
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOW_DEMO_USERS", "true")
    dispose_db()
    init_db()
    create_all()
    yield
    dispose_db()


def test_upsert_stores_full_body_beyond_preview(durable_db):
    long_text = "A" * 1200 + " UNIQUE_TAIL"
    upsert_kb(
        {
            "id": "kb1",
            "tenant_id": "demo",
            "name": "n",
            "code": "c",
            "status": "active",
        }
    )
    upsert_document_meta(
        {
            "id": "doc_long",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "long.md",
            "text": long_text,
            "status": "uploaded",
            "storage": "postgres",
            "uploader_id": "u1",
        }
    )
    row = get_document_row("demo", "doc_long")
    assert row is not None
    assert len(row["text"]) == len(long_text)
    assert row["text"].endswith("UNIQUE_TAIL")
    assert row["text_preview"] == long_text[:500]
    assert row["body_durable"] is True


def test_hydrate_prefers_body_over_truncated_memory(durable_db):
    upsert_kb({"id": "kb1", "tenant_id": "demo", "name": "n", "code": "c", "status": "active"})
    full = "B" * 800 + " END"
    upsert_document_meta(
        {
            "id": "doc2",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "b.md",
            "text": full,
            "status": "uploaded",
            "storage": "postgres",
        }
    )
    # Simulate hot mirror that only kept preview after crash/restart.
    stub = {
        "id": "doc2",
        "tenant_id": "demo",
        "kb_id": "kb1",
        "filename": "b.md",
        "text": full[:500],
        "text_preview": full[:500],
        "status": "uploaded",
        "storage": "postgres",
        "object_key": None,
    }
    hydrated = hydrate_document_text(stub)
    assert hydrated["text"].endswith(" END")
    assert len(hydrated["text"]) == len(full)


def test_minio_storage_clears_body_text_column(durable_db):
    upsert_kb({"id": "kb1", "tenant_id": "demo", "name": "n", "code": "c", "status": "active"})
    upsert_document_meta(
        {
            "id": "doc_m",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "m.md",
            "text": "secret-full-body-should-not-duplicate",
            "status": "uploaded",
            "storage": "minio",
            "object_key": "demo/kb1/doc_m/m.md",
        }
    )
    row = get_document_row("demo", "doc_m")
    assert row is not None
    # Authoritative object path: body_text cleared; hydrate would need MinIO.
    assert row["text"] == ""
    assert row["object_key"]
    assert row["body_durable"] is True


def test_restart_recovery_via_rag_doc_loader(durable_db):
    """Simulate process restart: empty hot map, load from PG body_text."""
    from app.core.platform import Platform
    from app.rag import service as rag

    long_text = ("退货政策段落。" * 80) + " RESTART_MARK"
    upsert_kb({"id": "kb1", "tenant_id": "demo", "name": "n", "code": "c", "status": "active"})
    upsert_document_meta(
        {
            "id": "doc_r",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "r.md",
            "text": long_text,
            "status": "uploaded",
            "storage": "postgres",
            "uploader_id": "u1",
        }
    )
    platform = Platform.seed()
    platform.documents.clear()
    loaded = rag._doc(platform, "demo", "doc_r")
    assert loaded["text"].endswith("RESTART_MARK")
    assert len(loaded["text"]) == len(long_text)


def test_delete_clears_meta_row(durable_db):
    from app.infra.entity_repo import delete_document_meta

    upsert_kb({"id": "kb1", "tenant_id": "demo", "name": "n", "code": "c", "status": "active"})
    upsert_document_meta(
        {
            "id": "doc_d",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "d.md",
            "text": "to-delete",
            "status": "uploaded",
            "storage": "postgres",
        }
    )
    delete_document_meta("demo", "doc_d")
    assert get_document_row("demo", "doc_d") is None


def test_parse_status_persists_via_application(durable_db):
    """Parse through Application must upsert status (not memory-only)."""
    from app.core.platform import Platform
    from app.modules.knowledge.application.dto import ParseDocumentCommand, TenantActor
    from app.modules.knowledge.application.services import KnowledgePipelineService
    from app.modules.knowledge.infrastructure.adapters import PlatformDocumentStore

    long_text = "C" * 600
    upsert_kb({"id": "kb1", "tenant_id": "demo", "name": "n", "code": "c", "status": "active"})
    upsert_document_meta(
        {
            "id": "doc_p",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "p.md",
            "text": long_text,
            "status": "uploaded",
            "storage": "postgres",
        }
    )
    platform = Platform.seed()
    platform.documents.clear()
    svc = KnowledgePipelineService(PlatformDocumentStore(platform))
    svc.parse(ParseDocumentCommand(doc_id="doc_p", actor=TenantActor(tenant_id="demo", user_id="u")))
    row = get_document_row("demo", "doc_p")
    assert row is not None
    assert row["status"] == "parsed"
    assert len(row["text"]) == len(long_text)
