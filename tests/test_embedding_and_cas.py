"""Embedding stub path + database-level ticket claim CAS (sqlite stand-in)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.infra.db import create_all, dispose_db, init_db, session_scope
from app.infra.models import TicketRow
from app.infra.ticket_repo import claim_ticket_executing
from app.rag.embedding import StubEmbedding, get_embedding_provider
from tests.conftest import auth_header, login


def test_stub_embedding_deterministic_and_dim():
    provider = StubEmbedding(dim=8)
    a = provider.embed(["退货政策"])[0]
    b = provider.embed(["退货政策"])[0]
    c = provider.embed(["开户理财"])[0]
    assert len(a) == 8
    assert a == b
    assert a != c
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6


def test_get_embedding_provider_default_stub(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODE", "stub")
    monkeypatch.setenv("RAG_BACKEND", "memory")
    monkeypatch.delenv("APP_ENV", raising=False)
    provider = get_embedding_provider()
    assert provider.dim >= 2
    assert len(provider.embed(["x"])[0]) == provider.dim


def test_vectorize_stores_embedding(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases", headers=headers, json={"name": "e", "code": "emb"}
    ).json()
    files = {"file": ("p.md", "退货政策：七天无理由。".encode(), "text/markdown")}
    doc = client.post(f"/api/admin/kb/{kb['id']}/documents", headers=headers, files=files).json()
    doc_id = doc["id"]
    assert client.post(f"/api/admin/documents/{doc_id}/parse", headers=headers).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/chunk", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/vectorize", headers=headers).json()["status"] == "vectorized"
    hits = client.post(
        "/api/admin/kb/retrieve-test",
        headers=headers,
        json={"kb_id": kb["id"], "query": "退货政策"},
    ).json()["hits"]
    assert hits
    assert hits[0]["score"] >= 0.5


@pytest.fixture
def cas_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cas.db"
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOW_DEMO_USERS", "true")
    dispose_db()
    init_db()
    create_all()
    yield
    dispose_db()


def test_claim_ticket_cas_only_one_winner(cas_db):
    with session_scope() as session:
        session.add(
            TicketRow(
                id="tkt_cas_1",
                tenant_id="demo",
                status="approved",
                applicant_id="u1",
                tool_name="demo_refund",
                args_masked_json="{}",
                idempotency_key="idem_cas_1",
            )
        )

    results = []

    def attempt():
        results.append(claim_ticket_executing("tkt_cas_1", "demo"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: attempt(), range(8)))

    assert sum(1 for ok in results if ok) == 1
    assert sum(1 for ok in results if not ok) == 7
    with session_scope() as session:
        row = session.get(TicketRow, "tkt_cas_1")
        assert row is not None
        assert row.status == "executing"


def test_claim_rejects_wrong_status(cas_db):
    with session_scope() as session:
        session.add(
            TicketRow(
                id="tkt_cas_2",
                tenant_id="demo",
                status="pending",
                applicant_id="u1",
                tool_name="demo_refund",
                args_masked_json="{}",
                idempotency_key="idem_cas_2",
            )
        )
    assert claim_ticket_executing("tkt_cas_2", "demo") is False


@pytest.mark.skipif(not os.getenv("RUN_PG_INTEGRATION"), reason="set RUN_PG_INTEGRATION=1 with real DATABASE_URL")
def test_pg_integration_gate_placeholder():
    """Real PostgreSQL multi-process CAS lives behind RUN_PG_INTEGRATION — not marked passed here."""
    assert os.getenv("DATABASE_URL")
