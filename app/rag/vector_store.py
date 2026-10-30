"""Vector store abstraction + in-process Mock (not Milvus).

MockVectorStore keeps real upserted records and ranks by cosine similarity.
It is for single-process tests/dev only — not durable, not multi-instance.
MilvusVectorStore is a thin adapter over ``app.storage.milvus_store``; selecting
it without a working URI/client fails loudly (no silent Mock fallback).
"""

from __future__ import annotations

import math
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol

from app.core.config import get_settings


class VectorStoreError(RuntimeError):
    pass


class VectorRecord(dict):
    """Typed-ish dict for store rows."""


class VectorStore(Protocol):
    def upsert(self, records: list[dict[str, Any]]) -> int: ...

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def delete(self, *, filters: dict[str, Any]) -> int: ...

    def get(self, record_id: str) -> dict[str, Any] | None: ...

    def health_check(self) -> bool: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        raise VectorStoreError("embedding dimension mismatch or empty")
    if any(not math.isfinite(x) for x in a) or any(not math.isfinite(x) for x in b):
        raise VectorStoreError("non-finite embedding value")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(dot / (na * nb))


def _match_filters(row: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    meta = row.get("metadata") or {}
    for key, expected in filters.items():
        if key in row and row[key] != expected:
            return False
        if key not in row and meta.get(key) != expected:
            return False
    return True


class MockVectorStore:
    """Process-local vector index. Deterministic cosine search over stored rows.

    NOT equivalent to Milvus durability or distribution. Restart clears data
    unless the caller also persists elsewhere.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[str, dict[str, Any]] = {}

    def upsert(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        with self._lock:
            count = 0
            for raw in records:
                rid = str(raw.get("id") or "").strip()
                if not rid:
                    raise VectorStoreError("record id required")
                emb = list(raw.get("embedding") or [])
                if not emb:
                    raise VectorStoreError("embedding required")
                if any(not math.isfinite(x) for x in emb):
                    raise VectorStoreError("non-finite embedding")
                now = _now()
                existing = self._by_id.get(rid)
                meta_raw = raw.get("metadata")
                metadata: dict[str, Any] = dict(meta_raw) if isinstance(meta_raw, dict) else {}
                row: dict[str, Any] = {
                    "id": rid,
                    "document_id": raw.get("document_id") or raw.get("doc_id"),
                    "chunk_id": raw.get("chunk_id") or raw.get("segment_id") or rid,
                    "content": str(raw.get("content") or raw.get("text") or ""),
                    "embedding": emb,
                    "metadata": metadata,
                    "created_at": (existing or {}).get("created_at") or now,
                    "updated_at": now,
                }
                # Flatten common filter keys onto metadata for search filters.
                for key in ("tenant_id", "kb_id", "doc_id", "segment_id"):
                    if raw.get(key) is not None:
                        metadata.setdefault(key, raw[key])
                        row[key] = raw[key]
                self._by_id[rid] = row
                count += 1
            return count

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            return []
        with self._lock:
            scored: list[tuple[float, dict[str, Any]]] = []
            for row in self._by_id.values():
                if not _match_filters(row, filters):
                    continue
                try:
                    score = _cosine(query_embedding, row["embedding"])
                except VectorStoreError:
                    continue
                hit = {
                    "id": row["id"],
                    "segment_id": row.get("chunk_id") or row["id"],
                    "doc_id": row.get("document_id"),
                    "text": row.get("content") or "",
                    "score": score,
                    "source": "mock_vector",
                    "metadata": deepcopy(row.get("metadata") or {}),
                }
                scored.append((score, hit))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [hit for _, hit in scored[:top_k]]

    def delete(self, *, filters: dict[str, Any]) -> int:
        if not filters:
            raise VectorStoreError("delete requires filters to avoid wiping entire store")
        with self._lock:
            remove = [rid for rid, row in self._by_id.items() if _match_filters(row, filters)]
            for rid in remove:
                del self._by_id[rid]
            return len(remove)

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._by_id.get(record_id)
            return deepcopy(row) if row else None

    def health_check(self) -> bool:
        return True

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()


class MilvusVectorStore:
    """Adapter over pymilvus path — fails if Milvus is unavailable (no Mock fallback)."""

    def upsert(self, records: list[dict[str, Any]]) -> int:
        from app.storage.milvus_store import MilvusError, MilvusNotConfiguredError, upsert_segments

        if not records:
            return 0
        # Group by doc for replace semantics.
        by_doc: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in records:
            tenant = str(row.get("tenant_id") or (row.get("metadata") or {}).get("tenant_id") or "")
            kb = str(row.get("kb_id") or (row.get("metadata") or {}).get("kb_id") or "")
            doc = str(row.get("document_id") or row.get("doc_id") or "")
            by_doc.setdefault((tenant, kb, doc), []).append(row)
        total = 0
        try:
            for (tenant, kb, doc), group in by_doc.items():
                upsert_segments(
                    tenant_id=tenant,
                    kb_id=kb,
                    doc_id=doc,
                    rows=[
                        {
                            "segment_id": r.get("chunk_id") or r.get("segment_id") or r["id"],
                            "text": r.get("content") or r.get("text") or "",
                        }
                        for r in group
                    ],
                    vectors=[list(r["embedding"]) for r in group],
                )
                total += len(group)
        except (MilvusNotConfiguredError, MilvusError) as exc:
            raise VectorStoreError(str(exc)) from exc
        return total

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        from app.storage.milvus_store import MilvusError, MilvusNotConfiguredError
        from app.storage.milvus_store import search as milvus_search

        filters = filters or {}
        tenant = str(filters.get("tenant_id") or "")
        kb = str(filters.get("kb_id") or "")
        if not tenant or not kb:
            raise VectorStoreError("milvus search requires tenant_id and kb_id filters")
        try:
            return milvus_search(
                tenant_id=tenant,
                kb_id=kb,
                query_vector=query_embedding,
                top_k=top_k,
            )
        except (MilvusNotConfiguredError, MilvusError) as exc:
            raise VectorStoreError(str(exc)) from exc

    def delete(self, *, filters: dict[str, Any]) -> int:
        from app.storage.milvus_store import MilvusError, MilvusNotConfiguredError, delete_by_doc

        tenant = str(filters.get("tenant_id") or "")
        kb = str(filters.get("kb_id") or "")
        doc = str(filters.get("doc_id") or filters.get("document_id") or "")
        if not (tenant and kb and doc):
            raise VectorStoreError("milvus delete requires tenant_id, kb_id, doc_id")
        try:
            delete_by_doc(tenant, kb, doc)
        except (MilvusNotConfiguredError, MilvusError) as exc:
            raise VectorStoreError(str(exc)) from exc
        return 0  # milvus API does not always return count

    def get(self, record_id: str) -> dict[str, Any] | None:
        raise VectorStoreError("milvus get-by-id not implemented in this scaffold")

    def health_check(self) -> bool:
        from app.storage.milvus_store import milvus_healthy

        return milvus_healthy()


_MOCK_SINGLETON: MockVectorStore | None = None


def get_mock_vector_store() -> MockVectorStore:
    global _MOCK_SINGLETON
    if _MOCK_SINGLETON is None:
        _MOCK_SINGLETON = MockVectorStore()
    return _MOCK_SINGLETON


def reset_mock_vector_store() -> None:
    """Test helper — clear singleton contents."""
    store = get_mock_vector_store()
    store.clear()


def get_vector_store() -> VectorStore:
    """Factory. Production milvus misconfig must not silently use Mock."""
    settings = get_settings()
    backend = settings.effective_vector_store_backend()
    if backend == "mock":
        return get_mock_vector_store()
    if backend == "milvus":
        if not (settings.milvus_uri or "").strip():
            raise VectorStoreError("VECTOR_STORE_BACKEND=milvus requires MILVUS_URI (no Mock fallback)")
        return MilvusVectorStore()
    raise VectorStoreError(f"unsupported VECTOR_STORE_BACKEND={backend}")
