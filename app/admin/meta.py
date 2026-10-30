"""Response envelope helpers: separate payload from data-source metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(
    data: Any,
    *,
    source: str,
    is_demo: bool = False,
    note: str | None = None,
    updated_at: str | None = None,
) -> dict:
    """Wrap business data with provenance metadata for the admin UI."""
    meta = {
        "source": source,
        "is_demo": bool(is_demo),
        "updated_at": updated_at or utc_now_iso(),
    }
    if note:
        meta["note"] = note
    if isinstance(data, dict):
        return {**data, "_meta": meta}
    return {"data": data, "_meta": meta}
