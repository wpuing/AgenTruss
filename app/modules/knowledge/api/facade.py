"""Thin facade for knowledge document pipeline routes."""

from __future__ import annotations

from typing import Any

from app.modules.knowledge.application.dto import (
    ChunkDocumentCommand,
    CreateKnowledgeBaseCommand,
    DeleteDocumentCommand,
    DeleteKnowledgeBaseCommand,
    GetDocumentCommand,
    ParseDocumentCommand,
    RetrieveCommand,
    TenantActor,
    UploadDocumentCommand,
    VectorizeDocumentCommand,
)
from app.modules.knowledge.application.services import KnowledgePipelineService
from app.modules.knowledge.infrastructure.adapters import (
    PlatformChunkingAdapter,
    PlatformDocumentIngestAdapter,
    PlatformDocumentStore,
    PlatformKnowledgeBaseStore,
    PlatformRetrieveAdapter,
    PlatformVectorizeAdapter,
    raise_http_from_knowledge,
)


def _pipeline(platform: Any) -> KnowledgePipelineService:
    return KnowledgePipelineService(
        PlatformDocumentStore(platform),
        chunking=PlatformChunkingAdapter(platform),
        vectorize=PlatformVectorizeAdapter(platform),
        knowledge_bases=PlatformKnowledgeBaseStore(platform),
        ingest=PlatformDocumentIngestAdapter(platform),
        retrieve=PlatformRetrieveAdapter(platform),
    )


def _actor(auth: Any) -> TenantActor:
    return TenantActor(tenant_id=auth.tenant_id, user_id=auth.user_id)


def parse_document(platform: Any, auth: Any, doc_id: str) -> dict:
    try:
        result = _pipeline(platform).parse(
            ParseDocumentCommand(doc_id=doc_id, actor=_actor(auth))
        )
        from app.rag.service import _job

        _job(platform, "parse", doc_id, "succeeded")
        return result.document
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def chunk_document(platform: Any, auth: Any, doc_id: str, strategy_id: str | None) -> dict:
    try:
        return _pipeline(platform).chunk(
            ChunkDocumentCommand(doc_id=doc_id, actor=_actor(auth), strategy_id=strategy_id)
        ).document
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def vectorize_document(platform: Any, auth: Any, doc_id: str) -> dict:
    try:
        return _pipeline(platform).vectorize(
            VectorizeDocumentCommand(doc_id=doc_id, actor=_actor(auth))
        ).document
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def delete_knowledge_base(platform: Any, auth: Any, kb_id: str) -> None:
    try:
        _pipeline(platform).delete_knowledge_base(
            DeleteKnowledgeBaseCommand(kb_id=kb_id, actor=_actor(auth))
        )
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def create_knowledge_base(platform: Any, auth: Any, name: str, code: str) -> dict:
    try:
        return _pipeline(platform).create_knowledge_base(
            CreateKnowledgeBaseCommand(name=name, code=code, actor=_actor(auth))
        )
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def upload_document(
    platform: Any,
    auth: Any,
    kb_id: str,
    filename: str,
    text: str,
) -> dict:
    try:
        return _pipeline(platform).upload_document(
            UploadDocumentCommand(
                kb_id=kb_id,
                filename=filename,
                text=text,
                actor=_actor(auth),
            )
        ).document
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def get_document(platform: Any, auth: Any, doc_id: str) -> dict:
    try:
        return _pipeline(platform).get_document(
            GetDocumentCommand(doc_id=doc_id, actor=_actor(auth))
        ).document
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def delete_document(platform: Any, auth: Any, doc_id: str) -> None:
    try:
        _pipeline(platform).delete_document(
            DeleteDocumentCommand(doc_id=doc_id, actor=_actor(auth))
        )
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover


def retrieve(
    platform: Any,
    auth: Any,
    kb_id: str,
    query: str,
    strategy_id: str | None = None,
) -> dict:
    """Return hits + provider provenance (mock vs milvus label)."""
    try:
        result = _pipeline(platform).retrieve(
            RetrieveCommand(
                kb_id=kb_id,
                query=query,
                actor=_actor(auth),
                strategy_id=strategy_id,
            )
        )
        return {"hits": result.hits, "provider_mode": result.provider_mode}
    except Exception as exc:  # noqa: BLE001
        raise_http_from_knowledge(exc)
        raise  # pragma: no cover
