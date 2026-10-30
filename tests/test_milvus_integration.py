"""Real Milvus vector store gate.

Enable with ``RUN_MILVUS_INTEGRATION=1`` and ``MILVUS_URI``.
Never silently falls back to Mock. Skip ≠ pass for the milvus CI job.

Delete pass/fail is judged by **search before/after**, not ``delete()`` return
(current adapter always returns 0).
"""

from __future__ import annotations

import os
import uuid

import pytest

_RUN = os.getenv("RUN_MILVUS_INTEGRATION") == "1"
_URI = (os.getenv("MILVUS_URI") or "").strip()

pytestmark = pytest.mark.skipif(
    not _RUN or not _URI,
    reason="set RUN_MILVUS_INTEGRATION=1 and MILVUS_URI",
)


def test_milvus_upsert_search_delete(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_BACKEND", "milvus")
    monkeypatch.setenv("MILVUS_URI", _URI)
    from app.rag.vector_store import MilvusVectorStore, get_vector_store

    # Isolated ids — never wipe shared collections; fit short schema fields.
    suffix = uuid.uuid4().hex[:8]
    tenant = f"t{suffix}"
    kb = f"k{suffix}"
    doc = f"d{suffix}"
    seg = f"s{suffix}"
    mark = f"MILVUS_MARK_{suffix}"

    store = get_vector_store()
    assert isinstance(store, MilvusVectorStore)
    emb = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    filters = {"tenant_id": tenant, "kb_id": kb}

    try:
        n = store.upsert(
            [
                {
                    "id": seg,
                    "segment_id": seg,
                    "chunk_id": seg,
                    "embedding": emb,
                    "content": f"hello milvus {mark}",
                    "tenant_id": tenant,
                    "kb_id": kb,
                    "doc_id": doc,
                }
            ]
        )
        assert n >= 1
        hits = store.search(query_embedding=emb, top_k=5, filters=filters)
        assert hits, "expected at least one milvus hit after upsert"
        joined = " ".join(str(h.get("text") or h.get("content") or "") for h in hits)
        assert mark in joined, f"upserted mark not in search hits: {hits!r}"
        assert any((h.get("doc_id") or "") == doc for h in hits), hits

        # Return value of delete is not authoritative (adapter returns 0).
        store.delete(filters={"tenant_id": tenant, "kb_id": kb, "doc_id": doc})
        after = store.search(query_embedding=emb, top_k=5, filters=filters)
        after_text = " ".join(str(h.get("text") or h.get("content") or "") for h in after)
        assert mark not in after_text, f"record still searchable after delete: {after!r}"
        assert not any((h.get("doc_id") or "") == doc for h in after), after
    finally:
        try:
            store.delete(filters={"tenant_id": tenant, "kb_id": kb, "doc_id": doc})
        except Exception:  # noqa: BLE001
            pass
