"""Knowledge-base admin HTTP routes (list/upload/pipeline/retrieve/QA/strategies list)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import need, need_any, platform_dep
from app.core.config import get_settings
from app.core.platform import Platform
from app.core.security import AuthContext
from app.rag import service as rag
from app.security.redaction import redact_for_api

router = APIRouter()


class KbIn(BaseModel):
    name: str
    code: str


class ChunkIn(BaseModel):
    strategy_id: str | None = None


class RetrieveIn(BaseModel):
    kb_id: str
    query: str
    strategy_id: str | None = None


@router.get("/api/admin/knowledge-bases")
def list_kb(auth: AuthContext = Depends(need("kb:base:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_kbs

        rows = list_kbs(auth.tenant_id)
        for row in rows:
            platform.kbs[row["id"]] = row
        return {"items": rows, "source": "postgres"}
    rows = [row for row in platform.kbs.values() if row["tenant_id"] == auth.tenant_id]
    return {"items": rows, "source": "memory"}


@router.post("/api/admin/knowledge-bases")
def post_kb(
    body: KbIn,
    auth: AuthContext = Depends(need("kb:base:create")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    return knowledge_facade.create_knowledge_base(platform, auth, body.name, body.code)


@router.delete("/api/admin/knowledge-bases/{kb_id}")
def remove_kb(
    kb_id: str,
    auth: AuthContext = Depends(need("kb:base:delete")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    knowledge_facade.delete_knowledge_base(platform, auth, kb_id)
    return {"ok": True}


@router.get("/api/admin/kb/{kb_id}/documents")
def list_docs(
    kb_id: str,
    auth: AuthContext = Depends(need("kb:doc:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    rag.get_kb(platform, auth.tenant_id, kb_id)
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_documents

        rows = list_documents(auth.tenant_id, kb_id)
    else:
        rows = [
            row
            for row in platform.documents.values()
            if row["tenant_id"] == auth.tenant_id and row["kb_id"] == kb_id
        ]
    return {
        "items": [
            {"id": r["id"], "filename": r["filename"], "status": r["status"], "kb_id": r["kb_id"]}
            for r in rows
        ]
    }


@router.get("/api/admin/documents")
def list_all_docs(
    kb_id: str | None = None,
    auth: AuthContext = Depends(need("kb:doc:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_documents

        rows = list_documents(auth.tenant_id, kb_id)
    else:
        rows = [row for row in platform.documents.values() if row["tenant_id"] == auth.tenant_id]
        if kb_id:
            rows = [row for row in rows if row["kb_id"] == kb_id]
    return {
        "items": [
            {"id": r["id"], "filename": r["filename"], "status": r["status"], "kb_id": r["kb_id"]}
            for r in rows
        ]
    }


@router.post("/api/admin/kb/{kb_id}/documents")
async def upload_doc(
    kb_id: str,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(need("kb:doc:upload")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.core.files import assert_allowed_upload, sanitize_filename

    settings = get_settings()
    max_bytes = settings.max_upload_bytes
    safe_name = sanitize_filename(file.filename)
    raw = await file.read(max_bytes + 1)
    try:
        assert_allowed_upload(
            filename=safe_name,
            content_type=file.content_type,
            size=len(raw),
            max_bytes=max_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="file must be UTF-8 text") from exc
    from app.modules.knowledge.api import facade as knowledge_facade

    return knowledge_facade.upload_document(platform, auth, kb_id, safe_name, text)


@router.post("/api/admin/documents/{doc_id}/parse")
def parse_doc(
    doc_id: str,
    auth: AuthContext = Depends(need("kb:doc:parse")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    return knowledge_facade.parse_document(platform, auth, doc_id)


@router.post("/api/admin/documents/{doc_id}/chunk")
def chunk_doc(
    doc_id: str,
    body: ChunkIn,
    auth: AuthContext = Depends(need("kb:doc:chunk")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    return knowledge_facade.chunk_document(platform, auth, doc_id, body.strategy_id)


@router.post("/api/admin/documents/{doc_id}/vectorize")
def vector_doc(
    doc_id: str,
    auth: AuthContext = Depends(need("kb:doc:vectorize")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    return knowledge_facade.vectorize_document(platform, auth, doc_id)


@router.get("/api/admin/documents/{doc_id}")
def get_doc(
    doc_id: str,
    auth: AuthContext = Depends(need("kb:doc:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    doc = knowledge_facade.get_document(platform, auth, doc_id)
    return redact_for_api(
        {
            "id": doc["id"],
            "kb_id": doc["kb_id"],
            "filename": doc["filename"],
            "status": doc["status"],
            "text": doc.get("text") or "",
            "storage": doc.get("storage"),
            "body_durable": bool(doc.get("body_durable")),
            "object_key": doc.get("object_key"),
        }
    )


@router.delete("/api/admin/documents/{doc_id}")
def delete_doc(
    doc_id: str,
    auth: AuthContext = Depends(need_any("kb:doc:delete", "kb:doc:upload")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    knowledge_facade.delete_document(platform, auth, doc_id)
    return {"ok": True, "doc_id": doc_id}


@router.get("/api/admin/documents/{doc_id}/segments")
def segments(
    doc_id: str,
    auth: AuthContext = Depends(need("kb:segment:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return {"items": rag.list_segments(platform, auth.tenant_id, doc_id)}


@router.get("/api/admin/retrieve-strategies")
def list_retrieve(
    auth: AuthContext = Depends(need("kb:retrieve_strategy:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return {"items": list(platform.retrieve_strategies.values())}


@router.get("/api/admin/chunk-strategies")
def list_chunk(
    auth: AuthContext = Depends(need("kb:chunk_strategy:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    return {"items": list(platform.chunk_strategies.values())}


@router.get("/api/admin/kb/jobs")
def list_jobs(auth: AuthContext = Depends(need("kb:job:view")), platform: Platform = Depends(platform_dep)) -> dict:
    rows = [row for row in platform.jobs.values() if row.get("tenant_id") == auth.tenant_id]
    return {"items": rows}


@router.post("/api/admin/kb/retrieve-test")
def retrieve_test(
    body: RetrieveIn,
    auth: AuthContext = Depends(need_any("kb:retrieve:test", "kb:retrieve:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.modules.knowledge.api import facade as knowledge_facade

    result = knowledge_facade.retrieve(platform, auth, body.kb_id, body.query, body.strategy_id)
    return redact_for_api({"hits": result["hits"], "provider_mode": result["provider_mode"]})


@router.post("/api/admin/kb/qa")
def qa(
    body: RetrieveIn,
    auth: AuthContext = Depends(need_any("kb:qa:test", "kb:qa:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    """Trial QA over retrieved snippets only — does not call an external LLM."""
    from app.modules.knowledge.api import facade as knowledge_facade

    result = knowledge_facade.retrieve(platform, auth, body.kb_id, body.query, body.strategy_id)
    hits = result["hits"]
    mode = result.get("provider_mode") or "unknown"
    if not hits:
        return {
            "reply": "知识库中没有足够依据，建议转人工。",
            "sources": [],
            "llm_called": False,
            "provider_mode": mode,
        }
    return redact_for_api(
        {
            "reply": f"根据知识库：{hits[0]['text']}",
            "sources": [hit["segment_id"] for hit in hits],
            "llm_called": False,
            "synthesis": "retrieval_template",
            "provider_mode": mode,
        }
    )
