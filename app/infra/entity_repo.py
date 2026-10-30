"""Durable entity repositories — postgres only; never silent memory fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.infra.db import session_scope
from app.infra.models import (
    AgentConfigRow,
    AuditRow,
    ChunkStrategyRow,
    DocumentRow,
    KnowledgeBaseRow,
    RetrieveStrategyRow,
    SegmentRow,
    SessionRow,
    SessionTurnRow,
    SettingsKvRow,
    UserRow,
)
from app.infra.ticket_repo import persistence_enabled

logger = logging.getLogger(__name__)


def require_persistence() -> None:
    if not persistence_enabled():
        raise RuntimeError("durable repository requires STORAGE_BACKEND=postgres and initialized DB")


def upsert_agent(agent: dict) -> None:
    """Persist agent instance config (kb_binding / status / tools)."""
    if not persistence_enabled():
        return
    binding = agent.get("kb_binding") or {}
    with session_scope() as session:
        row = session.get(AgentConfigRow, agent["agent_id"])
        if row is None:
            row = AgentConfigRow(
                agent_id=agent["agent_id"],
                tenant_id=agent["tenant_id"],
                name=agent.get("name") or "default",
                industry=agent.get("industry") or "ecommerce",
            )
            session.add(row)
        row.tenant_id = agent["tenant_id"]
        row.name = agent.get("name") or row.name
        row.code = agent.get("code") or "default"
        row.industry = agent.get("industry") or row.industry
        row.status = agent.get("status") or "published"
        row.version = int(agent.get("version") or 1)
        row.is_default = bool(agent.get("is_default", True))
        row.kb_id = binding.get("kb_id")
        row.rag_enabled = bool(binding.get("rag_enabled"))
        row.enabled_tools_json = json.dumps(list(agent.get("enabled_tools") or []), ensure_ascii=False)


def load_agents_into(platform) -> int:
    """Hydrate agent configs from postgres over seed rows (stable agent_id)."""
    if not persistence_enabled():
        return 0
    count = 0
    with session_scope() as session:
        for row in session.query(AgentConfigRow).all():
            tools = json.loads(row.enabled_tools_json or "[]")
            platform.agents[row.agent_id] = {
                "agent_id": row.agent_id,
                "tenant_id": row.tenant_id,
                "name": row.name,
                "code": row.code,
                "industry": row.industry,
                "status": row.status,
                "version": row.version,
                "is_default": bool(row.is_default),
                "kb_binding": {"kb_id": row.kb_id, "rag_enabled": bool(row.rag_enabled)},
                "enabled_tools": tools,
            }
            count += 1
    return count


def upsert_user(user: dict) -> None:
    require_persistence()
    with session_scope() as session:
        row = session.get(UserRow, user["id"])
        if row is None:
            row = UserRow(id=user["id"], username=user["username"], tenant_id=user["tenant_id"])
            session.add(row)
        row.username = user["username"]
        row.password_hash = user.get("password") or user.get("password_hash") or ""
        row.tenant_id = user["tenant_id"]
        row.roles_json = json.dumps(list(user.get("roles") or []), ensure_ascii=False)
        row.perms_json = json.dumps(sorted(user.get("perms") or []), ensure_ascii=False)
        row.status = user.get("status") or "active"


def load_users_into(platform) -> int:
    if not persistence_enabled():
        return 0
    from app.core.rbac import perms_for_roles_lenient

    count = 0
    with session_scope() as session:
        for row in session.query(UserRow).all():
            roles = json.loads(row.roles_json or "[]")
            perms = perms_for_roles_lenient(roles)
            if not perms:
                perms = set(json.loads(row.perms_json or "[]"))
            platform.users[row.username] = {
                "id": row.id,
                "username": row.username,
                "password": row.password_hash,
                "tenant_id": row.tenant_id,
                "roles": roles,
                "perms": perms,
                "status": row.status,
            }
            count += 1
    return count


def append_audit(audit: dict) -> None:
    """Write audit when durable; memory callers still keep platform.audits."""
    if not persistence_enabled():
        return
    with session_scope() as session:
        session.add(
            AuditRow(
                audit_id=audit["audit_id"],
                tenant_id=audit["tenant_id"],
                user_id=audit["user_id"],
                session_id=audit.get("session_id"),
                run_id=audit.get("run_id"),
                trace_id=audit.get("trace_id"),
                payload_json=json.dumps(audit, ensure_ascii=False, default=str),
            )
        )


def load_kb_meta_into(platform) -> int:
    """Hydrate knowledge bases + document metadata (not full text bodies)."""
    if not persistence_enabled():
        return 0
    count = 0
    with session_scope() as session:
        for row in session.query(KnowledgeBaseRow).all():
            platform.kbs[row.id] = {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "name": row.name,
                "code": row.code,
                "status": row.status,
                "default_chunk_strategy_id": row.default_chunk_strategy_id,
                "default_retrieve_strategy_id": row.default_retrieve_strategy_id,
            }
            count += 1
        for doc_row in session.query(DocumentRow).all():
            platform.documents[doc_row.id] = _document_dict_from_row(doc_row)
        for seg_row in session.query(SegmentRow).all():
            platform.segments[seg_row.id] = {
                "id": seg_row.id,
                "tenant_id": seg_row.tenant_id,
                "kb_id": seg_row.kb_id,
                "doc_id": seg_row.doc_id,
                "parent_id": seg_row.parent_id,
                "seg_type": seg_row.seg_type,
                "content": seg_row.content,
                "ordinal": seg_row.ordinal,
                "vector_status": seg_row.vector_status,
            }
    return count


def upsert_kb(row: dict) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        entity = session.get(KnowledgeBaseRow, row["id"])
        if entity is None:
            entity = KnowledgeBaseRow(id=row["id"], tenant_id=row["tenant_id"], name=row["name"], code=row["code"])
            session.add(entity)
        entity.name = row["name"]
        entity.code = row["code"]
        entity.status = row.get("status") or "active"
        entity.default_chunk_strategy_id = row.get("default_chunk_strategy_id")
        entity.default_retrieve_strategy_id = row.get("default_retrieve_strategy_id")


def _document_dict_from_row(row: DocumentRow) -> dict[str, Any]:
    """Map ORM row to platform dict; prefer body_text over truncated preview."""
    storage = row.storage or "memory"
    text = ""
    if row.body_text:
        text = row.body_text
    elif row.object_key and storage == "minio":
        # Body lives in object store; leave empty until hydrate_document_text.
        text = ""
    else:
        text = row.text_preview or ""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "kb_id": row.kb_id,
        "filename": row.filename,
        "text": text,
        "status": row.status,
        "uploader_id": row.uploader_id,
        "error_message": None,
        "object_key": row.object_key,
        "storage": storage,
        "text_preview": row.text_preview,
        "body_durable": bool(row.body_text) or (storage == "minio" and bool(row.object_key)),
    }


def upsert_document_meta(row: dict) -> None:
    """Persist metadata + durable body when not using MinIO object storage.

    - Always store text_preview (≤500) for list UIs.
    - When storage is minio and object_key is set, body_text is cleared (authoritative object).
    - Otherwise (postgres without MinIO) persist full text in body_text for restart recovery.
    """
    if not persistence_enabled():
        return
    full_text = row.get("text") or ""
    storage = row.get("storage") or "memory"
    object_key = row.get("object_key")
    with session_scope() as session:
        entity = session.get(DocumentRow, row["id"])
        if entity is None:
            entity = DocumentRow(
                id=row["id"],
                tenant_id=row["tenant_id"],
                kb_id=row["kb_id"],
                filename=row["filename"],
            )
            session.add(entity)
        entity.filename = row.get("filename") or entity.filename
        entity.status = row.get("status") or "uploaded"
        entity.text_preview = full_text[:500]
        entity.object_key = object_key
        entity.storage = storage
        entity.uploader_id = row.get("uploader_id")
        if storage == "minio" and object_key:
            entity.body_text = None
        else:
            # Durable postgres / memory-mirrored path: keep full body for restart.
            entity.body_text = full_text


def replace_segments(doc_id: str, tenant_id: str, segments: list[dict]) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        session.query(SegmentRow).filter(SegmentRow.doc_id == doc_id, SegmentRow.tenant_id == tenant_id).delete()
        for seg in segments:
            session.add(
                SegmentRow(
                    id=seg["id"],
                    tenant_id=tenant_id,
                    kb_id=seg["kb_id"],
                    doc_id=doc_id,
                    parent_id=seg.get("parent_id"),
                    seg_type=seg["seg_type"],
                    content=seg["content"],
                    ordinal=int(seg.get("ordinal") or 0),
                    vector_status=seg.get("vector_status") or "pending",
                )
            )


def upsert_session_index(session_id: str, tenant_id: str, user_id: str, turn_count: int) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(SessionRow, session_id)
        if row is None:
            row = SessionRow(id=session_id, tenant_id=tenant_id, user_id=user_id)
            session.add(row)
        row.turn_count = turn_count


def append_session_turn(
    session_id: str,
    tenant_id: str,
    user_text: str,
    assistant_text: str,
    blocked: bool,
) -> None:
    if not persistence_enabled() or blocked:
        return
    with session_scope() as session:
        session.add(
            SessionTurnRow(
                session_id=session_id,
                tenant_id=tenant_id,
                user_text=user_text[:2000],
                assistant_text=assistant_text[:2000],
                blocked=False,
            )
        )


def list_kbs(tenant_id: str) -> list[dict]:
    """Authoritative KB list when postgres is enabled."""
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.query(KnowledgeBaseRow).filter(KnowledgeBaseRow.tenant_id == tenant_id).all()
        return [
            {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "name": row.name,
                "code": row.code,
                "status": row.status,
                "default_chunk_strategy_id": row.default_chunk_strategy_id,
                "default_retrieve_strategy_id": row.default_retrieve_strategy_id,
            }
            for row in rows
        ]


def get_kb(tenant_id: str, kb_id: str) -> dict | None:
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = session.get(KnowledgeBaseRow, kb_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "name": row.name,
            "code": row.code,
            "status": row.status,
            "default_chunk_strategy_id": row.default_chunk_strategy_id,
            "default_retrieve_strategy_id": row.default_retrieve_strategy_id,
        }


def list_documents(tenant_id: str, kb_id: str | None = None) -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        q = session.query(DocumentRow).filter(DocumentRow.tenant_id == tenant_id)
        if kb_id:
            q = q.filter(DocumentRow.kb_id == kb_id)
        return [_document_dict_from_row(row) for row in q.all()]


def get_document_row(tenant_id: str, doc_id: str) -> dict[str, Any] | None:
    """Authoritative document metadata (+ body_text when present)."""
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = session.get(DocumentRow, doc_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return _document_dict_from_row(row)


def delete_document_meta(tenant_id: str, doc_id: str) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        session.query(SegmentRow).filter(SegmentRow.doc_id == doc_id, SegmentRow.tenant_id == tenant_id).delete()
        row = session.get(DocumentRow, doc_id)
        if row is not None and row.tenant_id == tenant_id:
            session.delete(row)


def delete_kb_meta(tenant_id: str, kb_id: str) -> None:
    if not persistence_enabled():
        return
    with session_scope() as session:
        row = session.get(KnowledgeBaseRow, kb_id)
        if row is not None and row.tenant_id == tenant_id:
            session.delete(row)


def list_audits(tenant_id: str, limit: int = 200) -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = (
            session.query(AuditRow)
            .filter(AuditRow.tenant_id == tenant_id)
            .order_by(AuditRow.id.desc())
            .limit(max(1, min(limit, 1000)))
            .all()
        )
        out: list[dict] = []
        for row in reversed(rows):
            try:
                payload = json.loads(row.payload_json or "{}")
            except json.JSONDecodeError:
                payload = {"audit_id": row.audit_id, "tenant_id": row.tenant_id, "user_id": row.user_id}
            out.append(payload)
        return out


def list_sessions(tenant_id: str) -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.query(SessionRow).filter(SessionRow.tenant_id == tenant_id).all()
        return [
            {
                "session_id": row.id,
                "tenant_id": row.tenant_id,
                "user_id": row.user_id,
                "turn_count": row.turn_count,
            }
            for row in rows
        ]


def list_session_turns(tenant_id: str, session_id: str) -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        sess = session.get(SessionRow, session_id)
        if sess is None or sess.tenant_id != tenant_id:
            return []
        rows = (
            session.query(SessionTurnRow)
            .filter(SessionTurnRow.session_id == session_id, SessionTurnRow.tenant_id == tenant_id)
            .order_by(SessionTurnRow.id.asc())
            .all()
        )
        return [
            {"user": row.user_text, "assistant": row.assistant_text, "blocked": bool(row.blocked)}
            for row in rows
        ]


def list_users(tenant_id: str) -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.query(UserRow).filter(UserRow.tenant_id == tenant_id).all()
        return [
            {
                "id": row.id,
                "username": row.username,
                "tenant_id": row.tenant_id,
                "roles": json.loads(row.roles_json or "[]"),
                "status": row.status,
            }
            for row in rows
        ]


def find_user_by_username(username: str, tenant_id: str) -> dict | None:
    """Login path: prefer DB when durable storage is on."""
    if not persistence_enabled():
        return None
    with session_scope() as session:
        row = (
            session.query(UserRow)
            .filter(UserRow.username == username, UserRow.tenant_id == tenant_id)
            .one_or_none()
        )
        if row is None:
            return None
        from app.core.rbac import perms_for_roles_lenient

        roles = json.loads(row.roles_json or "[]")
        perms = perms_for_roles_lenient(roles)
        if not perms:
            perms = set(json.loads(row.perms_json or "[]"))
        return {
            "id": row.id,
            "username": row.username,
            "password": row.password_hash,
            "tenant_id": row.tenant_id,
            "roles": roles,
            "perms": perms,
            "status": row.status,
        }


def dashboard_counts(tenant_id: str) -> dict[str, int]:
    if not persistence_enabled():
        return {}
    with session_scope() as session:
        kb_count = session.query(KnowledgeBaseRow).filter(KnowledgeBaseRow.tenant_id == tenant_id).count()
        doc_count = session.query(DocumentRow).filter(DocumentRow.tenant_id == tenant_id).count()
        audit_count = session.query(AuditRow).filter(AuditRow.tenant_id == tenant_id).count()
        return {"kb_count": kb_count, "doc_count": doc_count, "chat_count": audit_count}


class ConflictError(Exception):
    """Optimistic concurrency conflict on strategy update."""


def upsert_settings_kv(
    key: str,
    value: Any,
    *,
    is_secret: bool,
    version: int,
    updated_by: str | None = None,
) -> None:
    """Upsert one settings key. value is JSON-serializable or already encrypted string."""
    require_persistence()
    with session_scope() as session:
        row = session.get(SettingsKvRow, key)
        if row is None:
            row = SettingsKvRow(key=key, value_json="null")
            session.add(row)
        row.value_json = json.dumps(value, ensure_ascii=False, default=str)
        row.is_secret = bool(is_secret)
        row.version = int(version)
        row.updated_by = updated_by


def list_settings_kv() -> list[dict]:
    if not persistence_enabled():
        return []
    with session_scope() as session:
        rows = session.query(SettingsKvRow).all()
        out: list[dict] = []
        for row in rows:
            try:
                value = json.loads(row.value_json)
            except json.JSONDecodeError:
                value = row.value_json
            out.append(
                {
                    "key": row.key,
                    "value": value,
                    "is_secret": bool(row.is_secret),
                    "version": row.version,
                    "updated_by": row.updated_by,
                }
            )
        return out


def upsert_chunk_strategy(row: dict) -> dict:
    """Insert/update chunk strategy; returns persisted row with version."""
    require_persistence()
    with session_scope() as session:
        entity = session.get(ChunkStrategyRow, row["id"])
        if entity is None:
            entity = ChunkStrategyRow(id=row["id"], name=row.get("name") or row["id"])
            session.add(entity)
            entity.version = int(row.get("version") or 1)
        else:
            expected = row.get("expected_version")
            if expected is not None and int(entity.version) != int(expected):
                raise ConflictError(f"chunk strategy version conflict: have={entity.version} expected={expected}")
            entity.version = int(entity.version) + 1
        entity.name = row.get("name") or entity.name
        entity.chunk_size = int(row.get("chunk_size", entity.chunk_size))
        entity.child_size = int(row.get("child_size", entity.child_size))
        entity.overlap = int(row.get("overlap", entity.overlap))
        entity.is_seed = bool(row.get("is_seed", entity.is_seed if entity.is_seed is not None else True))
        entity.updated_by = row.get("updated_by")
        session.flush()
        return {
            "id": entity.id,
            "name": entity.name,
            "chunk_size": entity.chunk_size,
            "child_size": entity.child_size,
            "overlap": entity.overlap,
            "is_seed": entity.is_seed,
            "version": entity.version,
        }


def upsert_retrieve_strategy(row: dict) -> dict:
    """Insert/update retrieve strategy; returns persisted row with version."""
    require_persistence()
    with session_scope() as session:
        entity = session.get(RetrieveStrategyRow, row["id"])
        if entity is None:
            entity = RetrieveStrategyRow(id=row["id"], name=row.get("name") or row["id"])
            session.add(entity)
            entity.version = int(row.get("version") or 1)
        else:
            expected = row.get("expected_version")
            if expected is not None and int(entity.version) != int(expected):
                raise ConflictError(
                    f"retrieve strategy version conflict: have={entity.version} expected={expected}"
                )
            entity.version = int(entity.version) + 1
        entity.name = row.get("name") or entity.name
        entity.vector_min_score = float(row.get("vector_min_score", entity.vector_min_score))
        entity.rerank_min_score = float(row.get("rerank_min_score", entity.rerank_min_score))
        entity.top_k = int(row.get("top_k", entity.top_k))
        entity.is_seed = bool(row.get("is_seed", entity.is_seed if entity.is_seed is not None else True))
        entity.updated_by = row.get("updated_by")
        session.flush()
        return {
            "id": entity.id,
            "name": entity.name,
            "vector_min_score": entity.vector_min_score,
            "rerank_min_score": entity.rerank_min_score,
            "top_k": entity.top_k,
            "is_seed": entity.is_seed,
            "version": entity.version,
        }


def load_strategies_into(platform) -> int:
    """Hydrate chunk/retrieve strategies from PG. Returns total rows loaded."""
    if not persistence_enabled():
        return 0
    count = 0
    with session_scope() as session:
        chunk_rows = session.query(ChunkStrategyRow).all()
        for crow in chunk_rows:
            platform.chunk_strategies[crow.id] = {
                "id": crow.id,
                "name": crow.name,
                "chunk_size": crow.chunk_size,
                "child_size": crow.child_size,
                "overlap": crow.overlap,
                "is_seed": crow.is_seed,
                "version": crow.version,
            }
            count += 1
        retrieve_rows = session.query(RetrieveStrategyRow).all()
        for rrow in retrieve_rows:
            platform.retrieve_strategies[rrow.id] = {
                "id": rrow.id,
                "name": rrow.name,
                "vector_min_score": rrow.vector_min_score,
                "rerank_min_score": rrow.rerank_min_score,
                "top_k": rrow.top_k,
                "is_seed": rrow.is_seed,
                "version": rrow.version,
            }
            count += 1
    return count


def seed_strategies_if_empty(platform) -> int:
    """Persist in-memory seed strategies when PG tables are empty."""
    if not persistence_enabled():
        return 0
    with session_scope() as session:
        if session.query(ChunkStrategyRow).count() or session.query(RetrieveStrategyRow).count():
            return 0
    written = 0
    for row in platform.chunk_strategies.values():
        upsert_chunk_strategy({**row, "version": row.get("version") or 1})
        written += 1
    for row in platform.retrieve_strategies.values():
        upsert_retrieve_strategy({**row, "version": row.get("version") or 1})
        written += 1
    return written
