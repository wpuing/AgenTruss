"""Knowledge application tests with Fake stores (no embedding / MinIO)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.knowledge.application.dto import (
    ChunkDocumentCommand,
    DeleteKnowledgeBaseCommand,
    ParseDocumentCommand,
    TenantActor,
    VectorizeDocumentCommand,
)
from app.modules.knowledge.application.services import KnowledgePipelineService
from app.modules.knowledge.domain.exceptions import (
    InvalidDocumentTransitionError,
    KnowledgeBaseNotEmptyError,
)


class FakeDocs:
    def __init__(self, docs: dict[str, dict[str, Any]]) -> None:
        self.docs = docs
        self.saves = 0
        self.deletes = 0

    def get_document(self, tenant_id: str, doc_id: str) -> dict[str, Any]:
        d = self.docs.get(doc_id)
        if not d or d["tenant_id"] != tenant_id:
            raise LookupError("missing")
        return d

    def save_document(self, doc: dict[str, Any]) -> None:
        self.docs[doc["id"]] = doc
        self.saves += 1

    def delete_document(self, tenant_id: str, doc_id: str) -> None:
        self.deletes += 1
        self.docs.pop(doc_id, None)


def test_delete_document_via_application():
    from app.modules.knowledge.application.dto import DeleteDocumentCommand

    docs = FakeDocs(
        {
            "d1": {
                "id": "d1",
                "tenant_id": "demo",
                "kb_id": "kb1",
                "status": "uploaded",
                "text": "x",
            }
        }
    )
    svc = KnowledgePipelineService(docs)
    svc.delete_document(
        DeleteDocumentCommand(doc_id="d1", actor=TenantActor(user_id="u", tenant_id="demo"))
    )
    assert docs.deletes == 1
    assert "d1" not in docs.docs


class FakeChunk:
    def __init__(self) -> None:
        self.calls = 0

    def chunk(self, doc: dict[str, Any], strategy_id: str | None) -> dict[str, Any]:
        self.calls += 1
        doc = dict(doc)
        doc["status"] = "chunked"
        return doc


class FakeVec:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def vectorize(self, doc: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embed down")
        doc = dict(doc)
        doc["status"] = "vectorized"
        return doc


class FakeKb:
    def __init__(self, *, empty: bool = True) -> None:
        self.empty = empty
        self.deleted = False

    def get_kb(self, tenant_id: str, kb_id: str) -> dict[str, Any]:
        return {"id": kb_id, "tenant_id": tenant_id}

    def has_documents(self, tenant_id: str, kb_id: str) -> bool:
        return not self.empty

    def delete_kb(self, tenant_id: str, kb_id: str) -> None:
        self.deleted = True


def _actor() -> TenantActor:
    return TenantActor(tenant_id="demo", user_id="u1")


def test_parse_happy_and_illegal():
    store = FakeDocs(
        {"d1": {"id": "d1", "tenant_id": "demo", "status": "uploaded", "text": "hello world"}}
    )
    svc = KnowledgePipelineService(store)
    out = svc.parse(ParseDocumentCommand(doc_id="d1", actor=_actor()))
    assert out.document["status"] == "parsed"
    assert store.saves == 1
    with pytest.raises(InvalidDocumentTransitionError):
        # chunked cannot parse
        store.docs["d1"]["status"] = "chunked"
        svc.parse(ParseDocumentCommand(doc_id="d1", actor=_actor()))


def test_chunk_gate_blocks_before_port():
    store = FakeDocs({"d1": {"id": "d1", "tenant_id": "demo", "status": "uploaded", "text": "x"}})
    chunk = FakeChunk()
    svc = KnowledgePipelineService(store, chunking=chunk)
    with pytest.raises(InvalidDocumentTransitionError):
        svc.chunk(ChunkDocumentCommand(doc_id="d1", actor=_actor()))
    assert chunk.calls == 0


def test_chunk_and_vectorize_order():
    store = FakeDocs({"d1": {"id": "d1", "tenant_id": "demo", "status": "parsed", "text": "x"}})
    chunk = FakeChunk()
    vec = FakeVec()
    svc = KnowledgePipelineService(store, chunking=chunk, vectorize=vec)
    assert svc.chunk(ChunkDocumentCommand(doc_id="d1", actor=_actor())).document["status"] == "chunked"
    store.docs["d1"]["status"] = "chunked"
    assert svc.vectorize(VectorizeDocumentCommand(doc_id="d1", actor=_actor())).document[
        "status"
    ] == "vectorized"
    assert chunk.calls == 1 and vec.calls == 1


def test_vectorize_failure_propagates():
    store = FakeDocs({"d1": {"id": "d1", "tenant_id": "demo", "status": "chunked", "text": "x"}})
    vec = FakeVec()
    vec.fail = True
    svc = KnowledgePipelineService(store, vectorize=vec)
    with pytest.raises(RuntimeError, match="embed down"):
        svc.vectorize(VectorizeDocumentCommand(doc_id="d1", actor=_actor()))


def test_delete_kb_refuses_when_not_empty():
    svc = KnowledgePipelineService(FakeDocs({}), knowledge_bases=FakeKb(empty=False))
    with pytest.raises(KnowledgeBaseNotEmptyError):
        svc.delete_knowledge_base(DeleteKnowledgeBaseCommand(kb_id="kb1", actor=_actor()))
    kb = FakeKb(empty=True)
    KnowledgePipelineService(FakeDocs({}), knowledge_bases=kb).delete_knowledge_base(
        DeleteKnowledgeBaseCommand(kb_id="kb1", actor=_actor())
    )
    assert kb.deleted is True
