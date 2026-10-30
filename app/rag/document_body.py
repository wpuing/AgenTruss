"""Resolve full document body from memory, postgres body_text, or MinIO."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def hydrate_document_text(doc: dict[str, Any]) -> dict[str, Any]:
    """Ensure ``doc['text']`` is the full body when durable sources exist.

    Order:
    1. In-memory text already longer than preview / marked durable → keep.
    2. MinIO object_key → fetch bytes.
    3. Postgres body via get_document_row → use body_text.
    4. Else leave preview (body_durable=False).
    """
    preview = (doc.get("text_preview") or "")[:500]
    current = doc.get("text") or ""
    storage = doc.get("storage") or "memory"
    object_key = doc.get("object_key")

    if doc.get("body_durable") and current and (not preview or len(current) > len(preview) or current != preview):
        return doc
    if current and len(current) > 500:
        doc["body_durable"] = True
        return doc

    if storage == "minio" and object_key:
        try:
            from app.storage.minio_store import get_bytes

            raw = get_bytes(object_key)
            doc["text"] = raw.decode("utf-8")
            doc["body_durable"] = True
            return doc
        except Exception as exc:  # noqa: BLE001
            from app.core.config import get_settings
            from app.security.redaction import sanitize_exception

            settings = get_settings()
            if settings.is_production or settings.storage_backend == "postgres":
                raise HTTPException(
                    status_code=503, detail=f"object storage read failed: {sanitize_exception(exc)}"
                ) from exc
            logger.warning("minio read skipped: %s", sanitize_exception(exc))

    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import get_document_row

        row = get_document_row(doc["tenant_id"], doc["id"])
        if row and row.get("text") and (
            not preview or len(row["text"]) > len(preview) or row.get("body_durable")
        ):
            doc["text"] = row["text"]
            doc["body_durable"] = bool(row.get("body_durable"))
            doc["object_key"] = row.get("object_key") or object_key
            doc["storage"] = row.get("storage") or storage
            return doc

    doc["body_durable"] = bool(current) and storage == "memory"
    return doc
