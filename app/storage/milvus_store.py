"""Milvus vector store — real upsert/search when RAG_BACKEND=milvus.

Collection schema stores segment_id primary key + tenant/kb/doc filters + float vector.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

COLLECTION = "agentruss_segments"
_INDEX_READY = False


class MilvusNotConfiguredError(RuntimeError):
    pass


class MilvusError(RuntimeError):
    pass


def milvus_enabled() -> bool:
    settings = get_settings()
    backend = settings.effective_vector_store_backend()
    return backend == "milvus" and bool((settings.milvus_uri or "").strip())


def connect_or_raise():
    settings = get_settings()
    if not settings.milvus_uri:
        raise MilvusNotConfiguredError("MILVUS_URI not configured")
    try:
        from pymilvus import connections
    except ImportError as exc:
        raise MilvusNotConfiguredError('install with: pip install -e ".[storage]"') from exc
    connections.connect(alias="default", uri=settings.milvus_uri)
    return connections


def ensure_collection(dim: int) -> Any:
    """Create collection + IVF_FLAT index if missing; validate dim when present."""
    global _INDEX_READY
    connect_or_raise()
    try:
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility
    except ImportError as exc:
        raise MilvusNotConfiguredError('install with: pip install -e ".[storage]"') from exc

    if utility.has_collection(COLLECTION):
        col = Collection(COLLECTION)
        # Find vector field dim
        for field in col.schema.fields:
            if field.dtype == DataType.FLOAT_VECTOR:
                existing = field.params.get("dim")
                if existing is not None and int(existing) != int(dim):
                    raise MilvusError(f"collection dim={existing} != embedding_dim={dim}")
                break
        col.load()
        _INDEX_READY = True
        return col

    fields = [
        FieldSchema(name="segment_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=4000),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    schema = CollectionSchema(fields, description="AgenTruss RAG child segments")
    col = Collection(COLLECTION, schema)
    col.create_index(
        field_name="embedding",
        index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}},
    )
    col.load()
    _INDEX_READY = True
    logger.info("milvus collection created name=%s dim=%s", COLLECTION, dim)
    return col


def delete_by_doc(tenant_id: str, kb_id: str, doc_id: str) -> None:
    if not milvus_enabled():
        return
    col = ensure_collection(get_settings().embedding_dim)
    expr = f'tenant_id == "{tenant_id}" and kb_id == "{kb_id}" and doc_id == "{doc_id}"'
    try:
        col.delete(expr)
        col.flush()
        # Ensure subsequent search does not serve stale loaded segments.
        col.release()
        col.load()
    except Exception as exc:  # noqa: BLE001
        logger.error("milvus delete failed: %s", type(exc).__name__)
        raise MilvusError("milvus delete failed") from exc


def upsert_segments(
    *,
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    rows: list[dict],
    vectors: list[list[float]],
) -> None:
    """Replace vectors for a document then insert. rows[i] needs segment_id + text."""
    if len(rows) != len(vectors):
        raise MilvusError("rows/vectors length mismatch")
    if not rows:
        return
    dim = len(vectors[0])
    col = ensure_collection(dim)
    delete_by_doc(tenant_id, kb_id, doc_id)
    entities = [
        [r["segment_id"] for r in rows],
        [tenant_id] * len(rows),
        [kb_id] * len(rows),
        [doc_id] * len(rows),
        [str(r.get("text") or "")[:4000] for r in rows],
        vectors,
    ]
    try:
        col.insert(entities)
        col.flush()
    except Exception as exc:  # noqa: BLE001
        logger.error("milvus insert failed: %s", type(exc).__name__)
        raise MilvusError("milvus insert failed") from exc


def search(
    *,
    tenant_id: str,
    kb_id: str,
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict]:
    col = ensure_collection(len(query_vector))
    expr = f'tenant_id == "{tenant_id}" and kb_id == "{kb_id}"'
    try:
        results = col.search(
            data=[query_vector],
            anns_field="embedding",
            param={"metric_type": "IP", "params": {"nprobe": 16}},
            limit=max(1, top_k),
            expr=expr,
            output_fields=["segment_id", "text", "doc_id", "tenant_id", "kb_id"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("milvus search failed: %s", type(exc).__name__)
        raise MilvusError("milvus search failed") from exc
    hits: list[dict] = []
    for hits_group in results or []:
        for hit in hits_group:
            entity = hit.entity
            hits.append(
                {
                    "segment_id": entity.get("segment_id") or hit.id,
                    "text": entity.get("text") or "",
                    "doc_id": entity.get("doc_id"),
                    "score": float(hit.score),
                    "source": "milvus",
                }
            )
    return hits


def milvus_healthy() -> bool:
    if not milvus_enabled():
        return True  # not required
    try:
        connect_or_raise()
        return True
    except Exception:  # noqa: BLE001
        return False
