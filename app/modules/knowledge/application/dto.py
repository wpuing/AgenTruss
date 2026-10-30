"""Commands for knowledge pipeline (no FastAPI types)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TenantActor:
    tenant_id: str
    user_id: str


@dataclass(frozen=True)
class ParseDocumentCommand:
    doc_id: str
    actor: TenantActor


@dataclass(frozen=True)
class ChunkDocumentCommand:
    doc_id: str
    actor: TenantActor
    strategy_id: str | None = None


@dataclass(frozen=True)
class VectorizeDocumentCommand:
    doc_id: str
    actor: TenantActor


@dataclass(frozen=True)
class DeleteKnowledgeBaseCommand:
    kb_id: str
    actor: TenantActor


@dataclass(frozen=True)
class CreateKnowledgeBaseCommand:
    name: str
    code: str
    actor: TenantActor


@dataclass(frozen=True)
class UploadDocumentCommand:
    kb_id: str
    filename: str
    text: str
    actor: TenantActor


@dataclass(frozen=True)
class DeleteDocumentCommand:
    doc_id: str
    actor: TenantActor


@dataclass(frozen=True)
class GetDocumentCommand:
    doc_id: str
    actor: TenantActor


@dataclass(frozen=True)
class RetrieveCommand:
    kb_id: str
    query: str
    actor: TenantActor
    strategy_id: str | None = None


@dataclass(frozen=True)
class DocumentResult:
    document: dict[str, Any]


@dataclass(frozen=True)
class RetrieveResult:
    hits: list[dict[str, Any]]
    provider_mode: str
