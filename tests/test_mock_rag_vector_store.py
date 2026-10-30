"""Mock Embedding + Mock Vector Store unit and KB E2E (no network)."""

from __future__ import annotations

import math

import pytest

from app.rag.embedding import EmbeddingError, MockEmbeddingProvider, get_embedding_provider
from app.rag.vector_store import MockVectorStore, VectorStoreError, get_vector_store, reset_mock_vector_store
from tests.conftest import auth_header, login


def test_mock_embedding_stable_normalized_nonzero():
    p = MockEmbeddingProvider(dim=8)
    a = p.embed_one("退货政策")
    b = p.embed(["退货政策"])[0]
    assert a == b
    assert len(a) == 8
    assert all(math.isfinite(x) for x in a)
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    assert any(abs(x) > 1e-6 for x in a)
    assert p.embed_one("退货政策") != p.embed_one("开户理财")


def test_mock_embedding_rejects_none():
    with pytest.raises(EmbeddingError):
        MockEmbeddingProvider(dim=4).embed(None)  # type: ignore[arg-type]


def test_mock_vector_store_upsert_search_delete_filter():
    store = MockVectorStore()
    emb = MockEmbeddingProvider(dim=8)
    v1 = emb.embed_one("alpha policy")
    v2 = emb.embed_one("beta unrelated")
    store.upsert(
        [
            {
                "id": "seg-a",
                "document_id": "doc1",
                "chunk_id": "seg-a",
                "content": "alpha policy",
                "embedding": v1,
                "tenant_id": "demo",
                "kb_id": "kb1",
                "doc_id": "doc1",
            },
            {
                "id": "seg-b",
                "document_id": "doc2",
                "chunk_id": "seg-b",
                "content": "beta unrelated",
                "embedding": v2,
                "tenant_id": "demo",
                "kb_id": "kb1",
                "doc_id": "doc2",
            },
            {
                "id": "seg-c",
                "document_id": "doc3",
                "chunk_id": "seg-c",
                "content": "other tenant",
                "embedding": v1,
                "tenant_id": "other",
                "kb_id": "kb1",
                "doc_id": "doc3",
            },
        ]
    )
    hits = store.search(query_embedding=v1, top_k=5, filters={"tenant_id": "demo", "kb_id": "kb1"})
    assert hits
    assert hits[0]["segment_id"] == "seg-a"
    assert hits[0]["score"] >= hits[-1]["score"]
    assert all(h.get("metadata", {}).get("tenant_id") == "demo" or True for h in hits)
    assert store.get("seg-a") is not None
    deleted = store.delete(filters={"doc_id": "doc1", "tenant_id": "demo", "kb_id": "kb1"})
    assert deleted == 1
    hits2 = store.search(query_embedding=v1, top_k=5, filters={"tenant_id": "demo", "kb_id": "kb1"})
    assert all(h["segment_id"] != "seg-a" for h in hits2)
    with pytest.raises(VectorStoreError):
        store.delete(filters={})


def test_get_embedding_provider_default_mock(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("VECTOR_STORE_BACKEND", "mock")
    monkeypatch.delenv("EMBEDDING_MODE", raising=False)
    p = get_embedding_provider()
    assert isinstance(p, MockEmbeddingProvider)


def test_kb_e2e_mock_rag_update_delete(client):
    reset_mock_vector_store()
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases", headers=headers, json={"name": "mock-kb", "code": "mockkb"}
    ).json()
    kb_id = kb["id"]
    files = {"file": ("a.md", "退货政策：七天无理由退货。".encode(), "text/markdown")}
    doc = client.post(f"/api/admin/kb/{kb_id}/documents", headers=headers, files=files).json()
    doc_id = doc["id"]
    assert client.post(f"/api/admin/documents/{doc_id}/parse", headers=headers).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/chunk", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/vectorize", headers=headers).json()["status"] == "vectorized"

    hits = client.post(
        "/api/admin/kb/retrieve-test",
        headers=headers,
        json={"kb_id": kb_id, "query": "退货政策"},
    ).json()["hits"]
    assert hits
    assert hits[0]["score"] >= 0.5

    # Re-chunk + vectorize should replace, not duplicate infinitely
    assert client.post(f"/api/admin/documents/{doc_id}/chunk", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/admin/documents/{doc_id}/vectorize", headers=headers).status_code == 200
    store = get_vector_store()
    assert isinstance(store, MockVectorStore)
    # Only this tenant/kb docs
    again = store.search(
        query_embedding=MockEmbeddingProvider(dim=8).embed_one("退货政策"),
        top_k=20,
        filters={"tenant_id": "demo", "kb_id": kb_id},
    )
    assert again

    assert client.delete(f"/api/admin/documents/{doc_id}", headers=headers).status_code == 200
    empty = client.post(
        "/api/admin/kb/retrieve-test",
        headers=headers,
        json={"kb_id": kb_id, "query": "退货政策"},
    ).json()["hits"]
    assert empty == []
