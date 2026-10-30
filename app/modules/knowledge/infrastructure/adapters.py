"""Adapters bridging KnowledgePipelineService to ``app.rag.service`` / platform."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.modules.knowledge.application.services import map_knowledge_to_http
from app.modules.knowledge.domain.exceptions import KnowledgeDomainError


class PlatformDocumentStore:
    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def get_document(self, tenant_id: str, doc_id: str) -> dict[str, Any]:
        from app.rag import service as rag

        # Uses postgres authority + MinIO/body_text hydration.
        return rag._doc(self.platform, tenant_id, doc_id)

    def delete_document(self, tenant_id: str, doc_id: str) -> None:
        from app.rag import service as rag

        rag.delete_document(self.platform, tenant_id, doc_id)

    def save_document(self, doc: dict[str, Any]) -> None:
        self.platform.documents[doc["id"]] = doc
        try:
            from app.infra.entity_repo import upsert_document_meta

            upsert_document_meta(doc)
        except Exception:  # noqa: BLE001
            from app.core.config import get_settings
            from app.infra.ticket_repo import persistence_enabled

            if get_settings().is_production or persistence_enabled():
                raise


class PlatformChunkingAdapter:
    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def chunk(self, doc: dict[str, Any], strategy_id: str | None) -> dict[str, Any]:
        from app.rag import service as rag

        # Domain already gated; call legacy body via internal path that skips re-check
        # by temporarily trusting status — use full chunk_document after status OK.
        return rag.chunk_document(
            self.platform, doc["tenant_id"], doc["id"], strategy_id
        )


class PlatformVectorizeAdapter:
    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def vectorize(self, doc: dict[str, Any]) -> dict[str, Any]:
        from app.rag import service as rag

        return rag.vectorize_document(self.platform, doc["tenant_id"], doc["id"])


class PlatformKnowledgeBaseStore:
    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def get_kb(self, tenant_id: str, kb_id: str) -> dict[str, Any]:
        from app.rag import service as rag

        return rag.get_kb(self.platform, tenant_id, kb_id)

    def has_documents(self, tenant_id: str, kb_id: str) -> bool:
        from app.infra.ticket_repo import persistence_enabled

        if persistence_enabled():
            from app.infra.entity_repo import list_documents

            return bool(list_documents(tenant_id, kb_id))
        return any(
            doc["kb_id"] == kb_id and doc["tenant_id"] == tenant_id
            for doc in self.platform.documents.values()
        )

    def delete_kb(self, tenant_id: str, kb_id: str) -> None:
        from app.infra.ticket_repo import persistence_enabled

        if persistence_enabled():
            from app.infra.entity_repo import delete_kb_meta

            delete_kb_meta(tenant_id, kb_id)
        self.platform.kbs.pop(kb_id, None)

    def create_kb(self, tenant_id: str, name: str, code: str) -> dict[str, Any]:
        from app.rag import service as rag

        return rag.create_kb(self.platform, tenant_id, name, code)


class PlatformDocumentIngestAdapter:
    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def ingest(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        filename: str,
        text: str,
        user_id: str,
    ) -> dict[str, Any]:
        from app.rag import service as rag

        return rag.add_document(self.platform, tenant_id, kb_id, filename, text, user_id)


class PlatformRetrieveAdapter:
    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def search(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        query: str,
        strategy_id: str | None,
    ) -> list[dict[str, Any]]:
        from app.rag import service as rag

        return rag.retrieve(self.platform, tenant_id, kb_id, query, strategy_id)

    def provider_label(self) -> str:
        from app.core.config import get_settings

        settings = get_settings()
        emb = (settings.embedding_provider or settings.embedding_mode or "mock").lower()
        vs = settings.effective_vector_store_backend()
        return f"embedding={emb};vector={vs}"


def raise_http_from_knowledge(exc: Exception) -> None:
    if isinstance(exc, KnowledgeDomainError):
        code, detail = map_knowledge_to_http(exc)
        raise HTTPException(status_code=code, detail=detail) from exc
    raise exc
