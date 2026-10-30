"""Safe upload helpers: size gates, filename sanitization, type allowlists."""

from __future__ import annotations

import re
from pathlib import Path

# Text-oriented knowledge uploads for the MVP pipeline.
ALLOWED_UPLOAD_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".csv", ".json", ".log"})
ALLOWED_UPLOAD_CONTENT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/octet-stream",  # browsers often send this; still gated by suffix
        "",
    }
)
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]+")


def sanitize_filename(name: str | None, *, default: str = "upload.txt") -> str:
    """Return a basename-only safe filename (no path segments or traversal)."""
    raw = (name or default).strip() or default
    # Drop any directory components from client-supplied names.
    base = Path(raw.replace("\\", "/")).name
    if not base or base in {".", ".."}:
        base = default
    cleaned = _UNSAFE_NAME.sub("_", base).strip("._") or default
    if len(cleaned) > 180:
        stem = Path(cleaned).stem[:140]
        suffix = Path(cleaned).suffix[:20]
        cleaned = f"{stem}{suffix}" or default
    if "." not in cleaned:
        cleaned = f"{cleaned}.txt"
    return cleaned


def assert_allowed_upload(*, filename: str, content_type: str | None, size: int, max_bytes: int) -> None:
    """Raise ValueError when size/type/suffix violate policy (caller maps to HTTP)."""
    if size < 0:
        raise ValueError("invalid size")
    if size > max_bytes:
        raise ValueError(f"file too large (max {max_bytes} bytes)")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise ValueError("file type not allowed")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in ALLOWED_UPLOAD_CONTENT_TYPES:
        raise ValueError("content type not allowed")
