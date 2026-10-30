"""In-memory RAG. Only child segments are indexed."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from app.core.platform import Platform, new_id

logger = logging.getLogger(__name__)


def _owned(row: dict, tenant_id: str) -> None:
    if row["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail="not found")


def create_kb(platform: Platform, tenant_id: str, name: str, code: str) -> dict:
    """Create a knowledge base on the default loose retrieve strategy."""
    kb_id = new_id("kb")
    row = {
        "id": kb_id,
        "tenant_id": tenant_id,
        "name": name,
        "code": code,
        "status": "active",
        "default_chunk_strategy_id": platform.default_chunk_id,
        "default_retrieve_strategy_id": platform.default_retrieve_id,
    }
    platform.kbs[kb_id] = row
    try:
        from app.infra.entity_repo import upsert_kb

        upsert_kb(row)
    except Exception:  # noqa: BLE001 — memory mode / optional durable
        from app.core.config import get_settings
        from app.infra.ticket_repo import persistence_enabled

        if get_settings().is_production or persistence_enabled():
            raise
    return row


def get_kb(platform: Platform, tenant_id: str, kb_id: str) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import get_kb as get_kb_db

        row = get_kb_db(tenant_id, kb_id)
        if not row:
            raise HTTPException(status_code=404, detail="kb not found")
        platform.kbs[kb_id] = row
        return row
    row = platform.kbs.get(kb_id)
    if not row:
        raise HTTPException(status_code=404, detail="kb not found")
    _owned(row, tenant_id)
    return row


def delete_kb(platform: Platform, tenant_id: str, kb_id: str) -> None:
    """Refuse deletion when documents still exist."""
    get_kb(platform, tenant_id, kb_id)
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_documents

        if list_documents(tenant_id, kb_id):
            raise HTTPException(status_code=409, detail="kb not empty")
        from app.infra.entity_repo import delete_kb_meta

        delete_kb_meta(tenant_id, kb_id)
    elif any(doc["kb_id"] == kb_id for doc in platform.documents.values()):
        raise HTTPException(status_code=409, detail="kb not empty")
    platform.kbs.pop(kb_id, None)


def delete_document(platform: Platform, tenant_id: str, doc_id: str) -> None:
    """Remove document metadata, segments, vectors, and optional MinIO object.

    Cross-store deletes are best-effort ordered; production raises on vector/meta
    failure. Object-store delete failures in production/postgres raise (no silent OK).
    """
    from app.core.config import get_settings
    from app.infra.ticket_repo import persistence_enabled
    from app.security.redaction import sanitize_exception

    doc = _doc(platform, tenant_id, doc_id)
    kb_id = doc["kb_id"]
    settings = get_settings()
    try:
        from app.rag.vector_store import get_vector_store

        get_vector_store().delete(filters={"tenant_id": tenant_id, "kb_id": kb_id, "doc_id": doc_id})
    except Exception as exc:  # noqa: BLE001
        if settings.is_production:
            raise
        logger = __import__("logging").getLogger(__name__)
        logger.warning("vector delete soft-fail: %s", sanitize_exception(exc))
    if doc.get("object_key") and doc.get("storage") == "minio":
        try:
            from app.storage.minio_store import delete_object

            delete_object(doc["object_key"])
        except Exception as exc:  # noqa: BLE001
            if settings.is_production or persistence_enabled():
                raise HTTPException(
                    status_code=503, detail=f"object storage delete failed: {sanitize_exception(exc)}"
                ) from exc
    _drop_doc_segments(platform, doc_id)
    platform.vectors = [row for row in platform.vectors if row["doc_id"] != doc_id]
    platform.documents.pop(doc_id, None)
    try:
        from app.infra.entity_repo import delete_document_meta

        delete_document_meta(tenant_id, doc_id)
    except Exception:
        if settings.is_production or persistence_enabled():
            raise
    _job(platform, "delete", doc_id, "succeeded")


def add_document(platform: Platform, tenant_id: str, kb_id: str, filename: str, text: str, user_id: str) -> dict:
    """Store file text; optionally mirror bytes to MinIO when configured.

    Production with MinIO configured fails the upload if object put fails (no silent
    memory-only success). Without MinIO config, memory metadata remains the MVP path.
    Object keys always use a sanitized basename — never client path segments.
    """
    from app.core.config import get_settings
    from app.core.files import sanitize_filename

    get_kb(platform, tenant_id, kb_id)
    safe_name = sanitize_filename(filename)
    doc_id = new_id("doc")
    object_key = None
    storage = "memory"
    settings = get_settings()
    if settings.minio_endpoint and settings.minio_access_key:
        from app.storage.minio_store import MinioNotConfiguredError, put_bytes

        object_key = f"{tenant_id}/{kb_id}/{doc_id}/{safe_name}"
        try:
            put_bytes(object_key, text.encode("utf-8"), content_type="text/plain; charset=utf-8")
            storage = "minio"
        except MinioNotConfiguredError as exc:
            if settings.is_production:
                raise HTTPException(status_code=503, detail="object storage unavailable") from exc
        except Exception as exc:  # noqa: BLE001
            from app.security.redaction import sanitize_exception

            if settings.is_production or settings.storage_backend == "postgres":
                raise HTTPException(
                    status_code=503, detail=f"object storage failed: {sanitize_exception(exc)}"
                ) from exc
    # Without MinIO, postgres mode still persists full body via body_text (not preview-only).
    if storage == "memory" and settings.storage_backend == "postgres":
        storage = "postgres"
    row = {
        "id": doc_id,
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "filename": safe_name,
        "text": text,
        "status": "uploaded",
        "uploader_id": user_id,
        "error_message": None,
        "object_key": object_key,
        "storage": storage,
        "body_durable": True,
    }
    platform.documents[doc_id] = row
    try:
        from app.infra.entity_repo import upsert_document_meta

        upsert_document_meta(row)
    except Exception:  # noqa: BLE001
        from app.infra.ticket_repo import persistence_enabled

        if get_settings().is_production or persistence_enabled():
            raise
    _job(platform, "upload", doc_id, "succeeded")
    return row


def parse_document(platform: Platform, tenant_id: str, doc_id: str) -> dict:
    """Prefer ``knowledge.api.facade.parse_document``; kept for compatibility."""
    from app.modules.knowledge.domain.exceptions import KnowledgeDomainError
    from app.modules.knowledge.domain.policies import assert_can_parse

    doc = _doc(platform, tenant_id, doc_id)
    try:
        assert_can_parse(doc["status"])
    except KnowledgeDomainError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    doc["status"] = "parsed"
    doc["preview"] = doc["text"][:500]
    platform.documents[doc_id] = doc
    try:
        from app.infra.entity_repo import upsert_document_meta

        upsert_document_meta(doc)
    except Exception:  # noqa: BLE001 — memory mode / optional durable
        from app.core.config import get_settings
        from app.infra.ticket_repo import persistence_enabled

        if get_settings().is_production or persistence_enabled():
            raise
    _job(platform, "parse", doc_id, "succeeded")
    return doc


def chunk_document(platform: Platform, tenant_id: str, doc_id: str, strategy_id: str | None) -> dict:
    """Split into parent and child segments. Children stay pending."""
    from app.modules.knowledge.domain.exceptions import KnowledgeDomainError
    from app.modules.knowledge.domain.policies import assert_can_chunk

    doc = _doc(platform, tenant_id, doc_id)
    try:
        assert_can_chunk(doc["status"])
    except KnowledgeDomainError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    strategy = platform.chunk_strategies.get(strategy_id or platform.default_chunk_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="strategy not found")
    _drop_doc_segments(platform, doc_id)
    parents = _split(doc["text"], int(strategy["chunk_size"]))
    for ordinal, parent_text in enumerate(parents):
        parent_id = new_id("seg")
        platform.segments[parent_id] = _segment(doc, parent_id, None, "parent", parent_text, ordinal, "skipped")
        for child_ordinal, child_text in enumerate(_split(parent_text, int(strategy["child_size"]))):
            child_id = new_id("seg")
            platform.segments[child_id] = _segment(doc, child_id, parent_id, "child", child_text, child_ordinal, "pending")
    doc["status"] = "chunked"
    try:
        from app.infra.entity_repo import replace_segments, upsert_document_meta

        segs = [row for row in platform.segments.values() if row["doc_id"] == doc_id]
        replace_segments(doc_id, tenant_id, segs)
        upsert_document_meta(doc)
    except Exception:  # noqa: BLE001
        from app.core.config import get_settings
        from app.infra.ticket_repo import persistence_enabled

        if get_settings().is_production or persistence_enabled():
            raise
    _job(platform, "chunk", doc_id, "succeeded")
    return doc


def vectorize_document(platform: Platform, tenant_id: str, doc_id: str) -> dict:
    """Index child segments via EmbeddingProvider + VectorStore (default Mock)."""
    from app.core.config import get_settings
    from app.modules.knowledge.domain.exceptions import KnowledgeDomainError
    from app.modules.knowledge.domain.policies import assert_can_vectorize
    from app.rag.embedding import EmbeddingError, get_embedding_provider
    from app.rag.vector_store import VectorStoreError, get_vector_store

    doc = _doc(platform, tenant_id, doc_id)
    try:
        assert_can_vectorize(doc["status"])
    except KnowledgeDomainError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc

    children = [
        segment
        for segment in platform.segments.values()
        if segment["doc_id"] == doc_id and segment["seg_type"] == "child"
    ]
    texts = [segment["content"] for segment in children]
    try:
        provider = get_embedding_provider()
        vectors = provider.embed(texts) if texts else []
        from app.rag.embedding import assert_vectors_match_configured_dim

        # Guard write path: provider + configured dim must agree before vector store.
        assert_vectors_match_configured_dim(vectors, provider.dim)
        store = get_vector_store()
        # Replace prior vectors for this document (idempotent re-index).
        store.delete(filters={"tenant_id": tenant_id, "kb_id": doc["kb_id"], "doc_id": doc_id})
        records = []
        for segment, vector in zip(children, vectors, strict=True):
            records.append(
                {
                    "id": segment["id"],
                    "document_id": doc_id,
                    "chunk_id": segment["id"],
                    "content": segment["content"],
                    "embedding": vector,
                    "tenant_id": tenant_id,
                    "kb_id": doc["kb_id"],
                    "doc_id": doc_id,
                    "segment_id": segment["id"],
                    "metadata": {
                        "tenant_id": tenant_id,
                        "kb_id": doc["kb_id"],
                        "doc_id": doc_id,
                        "filename": doc.get("filename"),
                        "ordinal": segment.get("ordinal"),
                    },
                }
            )
            segment["vector_status"] = "indexed"
        if records:
            store.upsert(records)
    except (EmbeddingError, VectorStoreError) as exc:
        for segment in children:
            segment["vector_status"] = "failed"
        doc["status"] = "chunked"
        doc["error_message"] = "vector_index_failed"
        _job(platform, "vectorize", doc_id, "failed")
        raise HTTPException(status_code=503, detail=f"vectorize failed: {exc}") from exc

    # Keep platform.vectors as a thin mirror for older callers / debugging.
    platform.vectors = [row for row in platform.vectors if row["doc_id"] != doc_id]
    for segment, vector in zip(children, vectors, strict=True):
        platform.vectors.append(
            {
                "tenant_id": tenant_id,
                "kb_id": doc["kb_id"],
                "doc_id": doc_id,
                "segment_id": segment["id"],
                "text": segment["content"],
                "embedding": vector,
            }
        )

    doc["status"] = "vectorized"
    doc["error_message"] = None
    try:
        from app.infra.entity_repo import replace_segments, upsert_document_meta

        segs = [row for row in platform.segments.values() if row["doc_id"] == doc_id]
        replace_segments(doc_id, tenant_id, segs)
        upsert_document_meta(doc)
    except Exception:  # noqa: BLE001
        from app.infra.ticket_repo import persistence_enabled

        if get_settings().is_production or persistence_enabled():
            raise
    _job(platform, "vectorize", doc_id, "succeeded")
    return doc


def rehydrate_mock_vectors(platform: Platform) -> int:
    """Rebuild MockVectorStore from durable child segments after process restart.

    When VECTOR_STORE_BACKEND=mock, vectors live only in-process. Document meta may
    still say ``vectorized`` after restart — without this rebuild, retrieve/chat RAG
    returns empty while UI claims indexed. Milvus path is durable; skip rebuild.
    Returns number of upserted vectors. On failure demotes affected docs to chunked.
    """
    from app.core.config import get_settings
    from app.rag.embedding import EmbeddingError, assert_vectors_match_configured_dim, get_embedding_provider
    from app.rag.vector_store import VectorStoreError, get_vector_store

    settings = get_settings()
    if settings.effective_vector_store_backend() != "mock":
        return 0
    store = get_vector_store()
    # Already warm (same process / tests) — do not wipe.
    try:
        probe = getattr(store, "_by_id", None)
        if isinstance(probe, dict) and probe:
            return 0
    except Exception:  # noqa: BLE001
        pass

    provider = get_embedding_provider()
    upserted = 0
    by_doc: dict[str, list[dict]] = {}
    for segment in platform.segments.values():
        if segment.get("seg_type") != "child":
            continue
        if segment.get("vector_status") != "indexed":
            continue
        by_doc.setdefault(str(segment["doc_id"]), []).append(segment)

    for doc_id, children in by_doc.items():
        doc = platform.documents.get(doc_id)
        if not doc:
            continue
        tenant_id = str(doc.get("tenant_id") or "")
        kb_id = str(doc.get("kb_id") or "")
        texts = [str(seg.get("content") or "") for seg in children]
        try:
            vectors = provider.embed(texts) if texts else []
            assert_vectors_match_configured_dim(vectors, provider.dim)
            records = []
            for segment, vector in zip(children, vectors, strict=True):
                records.append(
                    {
                        "id": segment["id"],
                        "document_id": doc_id,
                        "chunk_id": segment["id"],
                        "content": segment["content"],
                        "embedding": vector,
                        "tenant_id": tenant_id,
                        "kb_id": kb_id,
                        "doc_id": doc_id,
                        "segment_id": segment["id"],
                        "metadata": {
                            "tenant_id": tenant_id,
                            "kb_id": kb_id,
                            "doc_id": doc_id,
                            "filename": doc.get("filename"),
                            "ordinal": segment.get("ordinal"),
                        },
                    }
                )
            if records:
                store.upsert(records)
                upserted += len(records)
            # Mirror for older callers.
            platform.vectors = [row for row in platform.vectors if row.get("doc_id") != doc_id]
            for segment, vector in zip(children, vectors, strict=True):
                platform.vectors.append(
                    {
                        "tenant_id": tenant_id,
                        "kb_id": kb_id,
                        "doc_id": doc_id,
                        "segment_id": segment["id"],
                        "text": segment["content"],
                        "embedding": vector,
                    }
                )
        except (EmbeddingError, VectorStoreError, ValueError) as exc:
            logger.warning("rehydrate failed doc=%s err=%s; demote to chunked", doc_id, exc)
            doc["status"] = "chunked"
            for segment in children:
                segment["vector_status"] = "pending"
            try:
                from app.infra.entity_repo import replace_segments, upsert_document_meta

                upsert_document_meta(doc)
                replace_segments(doc_id, tenant_id, [s for s in platform.segments.values() if s["doc_id"] == doc_id])
            except Exception:  # noqa: BLE001
                pass
    return upserted


def list_segments(platform: Platform, tenant_id: str, doc_id: str) -> list[dict]:
    _doc(platform, tenant_id, doc_id)
    rows = [row for row in platform.segments.values() if row["doc_id"] == doc_id]
    return sorted(rows, key=lambda row: (row["parent_id"] or "", row["ordinal"]))


def retrieve(platform: Platform, tenant_id: str, kb_id: str, query: str, strategy_id: str | None) -> list[dict]:
    """Embed query → VectorStore.search → optional lexical boost → threshold."""
    from app.rag.embedding import EmbeddingError, get_embedding_provider
    from app.rag.vector_store import VectorStoreError, get_vector_store

    kb = get_kb(platform, tenant_id, kb_id)
    wanted = strategy_id or kb.get("default_retrieve_strategy_id")
    strategy = platform.retrieve_strategies.get(wanted)
    if not strategy:
        # After restart, durable KB may still point at a previous process's seed id.
        strategy = platform.retrieve_strategies.get(platform.default_retrieve_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="retrieve strategy not found")

    top_k = int(strategy["top_k"])
    try:
        provider = get_embedding_provider()
        query_vec = provider.embed_one(query or "")
        from app.rag.embedding import assert_vectors_match_configured_dim

        assert_vectors_match_configured_dim([query_vec], provider.dim)
        store = get_vector_store()
        hits = store.search(
            query_embedding=query_vec,
            top_k=max(top_k * 2, top_k),
            filters={"tenant_id": tenant_id, "kb_id": kb_id},
        )
    except (EmbeddingError, VectorStoreError) as exc:
        raise HTTPException(status_code=503, detail=f"retrieve failed: {exc}") from exc

    # Lexical boost: Mock embeddings are not semantic — exact phrase still surfaces in tests.
    if not hits or max(hit["score"] for hit in hits) < float(strategy["vector_min_score"]):
        for hit in hits:
            if query and query in (hit.get("text") or ""):
                hit["score"] = max(float(hit["score"]), 0.5)
                hit["source"] = "bm25"
    floor = float(strategy["rerank_min_score"])
    kept = [hit for hit in hits if float(hit["score"]) >= floor]
    kept.sort(key=lambda hit: float(hit["score"]), reverse=True)
    return kept[:top_k]


def _lexical_score(query: str, text: str) -> float:
    """Containment score in (0, 1). Unrelated text stays below the loose threshold."""
    if query and query in text:
        return 0.8
    return 0.05


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


def _doc(platform: Platform, tenant_id: str, doc_id: str) -> dict:
    """Load document; prefer postgres authority + hydrate full body."""
    from app.infra.ticket_repo import persistence_enabled
    from app.rag.document_body import hydrate_document_text

    row = None
    if persistence_enabled():
        from app.infra.entity_repo import get_document_row

        row = get_document_row(tenant_id, doc_id)
        if row:
            platform.documents[doc_id] = row
    if row is None:
        row = platform.documents.get(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="document not found")
    _owned(row, tenant_id)
    return hydrate_document_text(row)


def _segment(doc, seg_id, parent_id, seg_type, content, ordinal, vector_status) -> dict:
    return {
        "id": seg_id,
        "tenant_id": doc["tenant_id"],
        "kb_id": doc["kb_id"],
        "doc_id": doc["id"],
        "parent_id": parent_id,
        "seg_type": seg_type,
        "content": content,
        "ordinal": ordinal,
        "vector_status": vector_status,
    }


def _split(text: str, size: int) -> list[str]:
    size = max(1, size)
    parts = [text[index:index + size] for index in range(0, len(text), size)]
    return parts or [text]


def _drop_doc_segments(platform: Platform, doc_id: str) -> None:
    for seg_id in [seg_id for seg_id, row in platform.segments.items() if row["doc_id"] == doc_id]:
        del platform.segments[seg_id]


def _job(platform: Platform, kind: str, doc_id: str, status: str) -> None:
    job_id = new_id("job")
    doc = platform.documents.get(doc_id) or {}
    platform.jobs[job_id] = {
        "id": job_id,
        "type": kind,
        "doc_id": doc_id,
        "tenant_id": doc.get("tenant_id"),
        "status": status,
        "progress": 100,
    }
