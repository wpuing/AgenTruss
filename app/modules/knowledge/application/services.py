"""Knowledge pipeline use-cases — status rules in domain; I/O via ports."""

from __future__ import annotations

from app.modules.knowledge.application.dto import (
    ChunkDocumentCommand,
    CreateKnowledgeBaseCommand,
    DeleteDocumentCommand,
    DeleteKnowledgeBaseCommand,
    DocumentResult,
    GetDocumentCommand,
    ParseDocumentCommand,
    RetrieveCommand,
    RetrieveResult,
    UploadDocumentCommand,
    VectorizeDocumentCommand,
)
from app.modules.knowledge.application.ports import (
    ChunkingPort,
    DocumentIngestPort,
    DocumentStore,
    KnowledgeBaseStore,
    RetrievePort,
    VectorizePort,
)
from app.modules.knowledge.domain.enums import DocumentStatus
from app.modules.knowledge.domain.exceptions import (
    KnowledgeBaseNotEmptyError,
    KnowledgeDomainError,
)
from app.modules.knowledge.domain.policies import (
    assert_can_chunk,
    assert_can_parse,
    assert_can_vectorize,
    assert_kb_usable,
    assert_upload_payload,
)


class KnowledgePipelineService:
    """Parse / chunk / vectorize / upload / retrieve orchestration."""

    def __init__(
        self,
        documents: DocumentStore,
        *,
        chunking: ChunkingPort | None = None,
        vectorize: VectorizePort | None = None,
        knowledge_bases: KnowledgeBaseStore | None = None,
        ingest: DocumentIngestPort | None = None,
        retrieve: RetrievePort | None = None,
    ) -> None:
        self._docs = documents
        self._chunking = chunking
        self._vectorize = vectorize
        self._kbs = knowledge_bases
        self._ingest = ingest
        self._retrieve = retrieve

    def parse(self, command: ParseDocumentCommand) -> DocumentResult:
        doc = self._docs.get_document(command.actor.tenant_id, command.doc_id)
        assert_can_parse(doc["status"])
        doc["status"] = DocumentStatus.PARSED.value
        doc["preview"] = (doc.get("text") or "")[:500]
        self._docs.save_document(doc)
        return DocumentResult(document=doc)

    def chunk(self, command: ChunkDocumentCommand) -> DocumentResult:
        if self._chunking is None:
            raise RuntimeError("chunking port required")
        doc = self._docs.get_document(command.actor.tenant_id, command.doc_id)
        assert_can_chunk(doc["status"])
        updated = self._chunking.chunk(doc, command.strategy_id)
        return DocumentResult(document=updated)

    def vectorize(self, command: VectorizeDocumentCommand) -> DocumentResult:
        if self._vectorize is None:
            raise RuntimeError("vectorize port required")
        doc = self._docs.get_document(command.actor.tenant_id, command.doc_id)
        assert_can_vectorize(doc["status"])
        updated = self._vectorize.vectorize(doc)
        return DocumentResult(document=updated)

    def delete_knowledge_base(self, command: DeleteKnowledgeBaseCommand) -> None:
        if self._kbs is None:
            raise RuntimeError("knowledge_bases port required")
        self._kbs.get_kb(command.actor.tenant_id, command.kb_id)
        if self._kbs.has_documents(command.actor.tenant_id, command.kb_id):
            raise KnowledgeBaseNotEmptyError()
        self._kbs.delete_kb(command.actor.tenant_id, command.kb_id)

    def create_knowledge_base(self, command: CreateKnowledgeBaseCommand) -> dict:
        if self._kbs is None:
            raise RuntimeError("knowledge_bases port required")
        return self._kbs.create_kb(command.actor.tenant_id, command.name, command.code)

    def upload_document(self, command: UploadDocumentCommand) -> DocumentResult:
        if self._kbs is None or self._ingest is None:
            raise RuntimeError("knowledge_bases and ingest ports required")
        assert_upload_payload(command.text)
        kb = self._kbs.get_kb(command.actor.tenant_id, command.kb_id)
        assert_kb_usable(kb.get("status"))
        doc = self._ingest.ingest(
            tenant_id=command.actor.tenant_id,
            kb_id=command.kb_id,
            filename=command.filename,
            text=command.text,
            user_id=command.actor.user_id,
        )
        return DocumentResult(document=doc)

    def get_document(self, command: GetDocumentCommand) -> DocumentResult:
        doc = self._docs.get_document(command.actor.tenant_id, command.doc_id)
        return DocumentResult(document=doc)

    def delete_document(self, command: DeleteDocumentCommand) -> None:
        # Existence + tenant check via get; then coordinated delete (meta/vectors/object).
        self._docs.get_document(command.actor.tenant_id, command.doc_id)
        self._docs.delete_document(command.actor.tenant_id, command.doc_id)

    def retrieve(self, command: RetrieveCommand) -> RetrieveResult:
        if self._kbs is None or self._retrieve is None:
            raise RuntimeError("knowledge_bases and retrieve ports required")
        kb = self._kbs.get_kb(command.actor.tenant_id, command.kb_id)
        assert_kb_usable(kb.get("status"))
        hits = self._retrieve.search(
            tenant_id=command.actor.tenant_id,
            kb_id=command.kb_id,
            query=command.query,
            strategy_id=command.strategy_id,
        )
        return RetrieveResult(hits=hits, provider_mode=self._retrieve.provider_label())


def map_knowledge_to_http(exc: KnowledgeDomainError) -> tuple[int, str]:
    from app.modules.knowledge.domain.exceptions import (
        EmptyUploadError,
        InvalidDocumentTransitionError,
        KnowledgeBaseNotEmptyError,
        KnowledgeBaseNotUsableError,
        UnknownDocumentStatusError,
    )

    if isinstance(exc, InvalidDocumentTransitionError):
        return 409, exc.message
    if isinstance(exc, UnknownDocumentStatusError):
        return 409, exc.message
    if isinstance(exc, KnowledgeBaseNotEmptyError):
        return 409, exc.message
    if isinstance(exc, KnowledgeBaseNotUsableError):
        return 409, exc.message
    if isinstance(exc, EmptyUploadError):
        return 400, exc.message
    return 400, exc.message
