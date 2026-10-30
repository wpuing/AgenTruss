"""Application upload/retrieve — Fake ports; no FastAPI / MinIO / Milvus."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.knowledge.application.dto import (
    RetrieveCommand,
    TenantActor,
    UploadDocumentCommand,
)
from app.modules.knowledge.application.services import KnowledgePipelineService
from app.modules.knowledge.domain.exceptions import EmptyUploadError, KnowledgeBaseNotUsableError


class FakeDocs:
    def get_document(self, tenant_id: str, doc_id: str) -> dict[str, Any]:
        raise LookupError("unused")

    def save_document(self, doc: dict[str, Any]) -> None:
        return None

    def delete_document(self, tenant_id: str, doc_id: str) -> None:
        return None


class FakeKb:
    def __init__(self, status: str = "active") -> None:
        self.status = status
        self.gets = 0

    def get_kb(self, tenant_id: str, kb_id: str) -> dict[str, Any]:
        self.gets += 1
        return {"id": kb_id, "tenant_id": tenant_id, "status": self.status}

    def has_documents(self, tenant_id: str, kb_id: str) -> bool:
        return False

    def delete_kb(self, tenant_id: str, kb_id: str) -> None:
        return None

    def create_kb(self, tenant_id: str, name: str, code: str) -> dict[str, Any]:
        return {"id": "kb_new", "tenant_id": tenant_id, "name": name, "code": code, "status": "active"}


class FakeIngest:
    def __init__(self) -> None:
        self.calls = 0

    def ingest(self, *, tenant_id, kb_id, filename, text, user_id) -> dict[str, Any]:
        self.calls += 1
        return {
            "id": "doc1",
            "tenant_id": tenant_id,
            "kb_id": kb_id,
            "filename": filename,
            "status": "uploaded",
            "text": text,
            "uploader_id": user_id,
        }


class FakeRetrieve:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def search(self, *, tenant_id, kb_id, query, strategy_id) -> list[dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("vector store down")
        return [{"segment_id": "s1", "text": query or "x", "score": 0.9}]

    def provider_label(self) -> str:
        return "embedding=mock;vector=mock"


def _actor() -> TenantActor:
    return TenantActor(tenant_id="demo", user_id="u1")


def test_upload_happy_path():
    ingest = FakeIngest()
    svc = KnowledgePipelineService(FakeDocs(), knowledge_bases=FakeKb(), ingest=ingest)
    out = svc.upload_document(
        UploadDocumentCommand(kb_id="kb1", filename="a.md", text="hello", actor=_actor())
    )
    assert out.document["status"] == "uploaded"
    assert ingest.calls == 1


def test_upload_empty_blocked_before_ingest():
    ingest = FakeIngest()
    svc = KnowledgePipelineService(FakeDocs(), knowledge_bases=FakeKb(), ingest=ingest)
    with pytest.raises(EmptyUploadError):
        svc.upload_document(
            UploadDocumentCommand(kb_id="kb1", filename="a.md", text="  ", actor=_actor())
        )
    assert ingest.calls == 0


def test_upload_inactive_kb_blocked():
    ingest = FakeIngest()
    svc = KnowledgePipelineService(
        FakeDocs(), knowledge_bases=FakeKb(status="disabled"), ingest=ingest
    )
    with pytest.raises(KnowledgeBaseNotUsableError):
        svc.upload_document(
            UploadDocumentCommand(kb_id="kb1", filename="a.md", text="hi", actor=_actor())
        )
    assert ingest.calls == 0


def test_retrieve_happy_and_failure():
    ret = FakeRetrieve()
    svc = KnowledgePipelineService(FakeDocs(), knowledge_bases=FakeKb(), retrieve=ret)
    out = svc.retrieve(RetrieveCommand(kb_id="kb1", query="退货", actor=_actor()))
    assert out.hits and out.provider_mode.startswith("embedding=")
    assert ret.calls == 1
    bad = FakeRetrieve(fail=True)
    svc2 = KnowledgePipelineService(FakeDocs(), knowledge_bases=FakeKb(), retrieve=bad)
    with pytest.raises(RuntimeError, match="vector store down"):
        svc2.retrieve(RetrieveCommand(kb_id="kb1", query="x", actor=_actor()))


def test_retrieve_inactive_kb_no_search():
    ret = FakeRetrieve()
    svc = KnowledgePipelineService(
        FakeDocs(), knowledge_bases=FakeKb(status="archived"), retrieve=ret
    )
    with pytest.raises(KnowledgeBaseNotUsableError):
        svc.retrieve(RetrieveCommand(kb_id="kb1", query="x", actor=_actor()))
    assert ret.calls == 0
