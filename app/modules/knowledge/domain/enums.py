"""Document pipeline statuses as used by ``app.rag.service``."""

from __future__ import annotations

from enum import StrEnum


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    CHUNKED = "chunked"
    VECTORIZED = "vectorized"


class DocumentAction(StrEnum):
    PARSE = "parse"
    CHUNK = "chunk"
    VECTORIZE = "vectorize"


class KnowledgeBaseStatus(StrEnum):
    ACTIVE = "active"
