"""Durable list/get helpers against sqlite stand-in (not a multi-instance PG claim)."""

from __future__ import annotations

import pytest

from app.infra.db import create_all, dispose_db, init_db
from app.infra import entity_repo
from app.infra.ticket_repo import claim_ticket_executing
from app.infra.models import TicketRow
from app.infra.db import session_scope


@pytest.fixture
def durable_db(tmp_path, monkeypatch):
    db_path = tmp_path / "entity.db"
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("APP_ENV", "test")
    dispose_db()
    init_db()
    create_all()
    yield
    dispose_db()


def test_kb_document_audit_roundtrip(durable_db):
    entity_repo.upsert_kb(
        {
            "id": "kb1",
            "tenant_id": "demo",
            "name": "Help",
            "code": "help",
            "status": "active",
            "default_chunk_strategy_id": "cs1",
            "default_retrieve_strategy_id": "rs1",
        }
    )
    entity_repo.upsert_document_meta(
        {
            "id": "doc1",
            "tenant_id": "demo",
            "kb_id": "kb1",
            "filename": "a.md",
            "status": "uploaded",
            "text": "preview",
            "storage": "memory",
        }
    )
    entity_repo.append_audit(
        {
            "audit_id": "aud1",
            "tenant_id": "demo",
            "user_id": "u1",
            "session_id": "s1",
            "blocked": False,
        }
    )
    kbs = entity_repo.list_kbs("demo")
    assert len(kbs) == 1 and kbs[0]["id"] == "kb1"
    assert entity_repo.get_kb("demo", "kb1")["code"] == "help"
    assert entity_repo.get_kb("other", "kb1") is None
    docs = entity_repo.list_documents("demo", "kb1")
    assert len(docs) == 1
    audits = entity_repo.list_audits("demo")
    assert any(row.get("audit_id") == "aud1" for row in audits)
    counts = entity_repo.dashboard_counts("demo")
    assert counts["kb_count"] == 1 and counts["doc_count"] == 1 and counts["chat_count"] == 1
    entity_repo.delete_document_meta("demo", "doc1")
    assert entity_repo.list_documents("demo", "kb1") == []


def test_ticket_get_list_authoritative(durable_db):
    from app.infra import ticket_repo

    with session_scope() as session:
        session.add(
            TicketRow(
                id="t1",
                tenant_id="demo",
                status="pending",
                applicant_id="u1",
                tool_name="demo_refund",
                args_masked_json="{}",
                idempotency_key="idem1",
                type="risk",
            )
        )
    rows = ticket_repo.list_tickets("demo")
    assert len(rows) == 1
    assert ticket_repo.get_ticket_row("t1", "demo")["status"] == "pending"
    assert ticket_repo.count_pending("demo") == 1
    assert claim_ticket_executing("t1", "demo") is False  # pending not claimable
