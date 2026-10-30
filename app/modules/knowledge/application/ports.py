"""Minimal ports for knowledge document pipeline."""

from __future__ import annotations

from typing import Any, Protocol


class DocumentStore(Protocol):
    def get_document(self, tenant_id: str, doc_id: str) -> dict[str, Any]:
        ...

    def save_document(self, doc: dict[str, Any]) -> None:
        ...

    def delete_document(self, tenant_id: str, doc_id: str) -> None:
        ...


class ChunkingPort(Protocol):
    """Split document text into segments; mutates platform/store as needed."""

    def chunk(self, doc: dict[str, Any], strategy_id: str | None) -> dict[str, Any]:
        ...


class VectorizePort(Protocol):
    """Embedding + vector upsert; must surface failures (no silent mock success)."""

    def vectorize(self, doc: dict[str, Any]) -> dict[str, Any]:
        ...


class KnowledgeBaseStore(Protocol):
    def get_kb(self, tenant_id: str, kb_id: str) -> dict[str, Any]:
        ...

    def has_documents(self, tenant_id: str, kb_id: str) -> bool:
        ...

    def delete_kb(self, tenant_id: str, kb_id: str) -> None:
        ...

    def create_kb(self, tenant_id: str, name: str, code: str) -> dict[str, Any]:
        ...


class DocumentIngestPort(Protocol):
    """Persist uploaded text (+ optional object store). Failures must surface."""

    def ingest(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        filename: str,
        text: str,
        user_id: str,
    ) -> dict[str, Any]:
        ...


class RetrievePort(Protocol):
    """Vector/lexical retrieve; must not silently swap real backends for Mock."""

    def search(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        query: str,
        strategy_id: str | None,
    ) -> list[dict[str, Any]]:
        ...

    def provider_label(self) -> str:
        """e.g. mock | milvus — for API provenance, not secrets."""
        ...
