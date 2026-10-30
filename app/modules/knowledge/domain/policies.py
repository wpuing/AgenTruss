"""Document pipeline transition rules (single source of truth)."""

from __future__ import annotations

from app.modules.knowledge.domain.enums import DocumentAction, DocumentStatus, KnowledgeBaseStatus
from app.modules.knowledge.domain.exceptions import (
    EmptyUploadError,
    InvalidDocumentTransitionError,
    KnowledgeBaseNotUsableError,
    UnknownDocumentStatusError,
)

# Mirrors app.rag.service historical allow-lists.
_ALLOWED: dict[DocumentAction, frozenset[DocumentStatus]] = {
    DocumentAction.PARSE: frozenset({DocumentStatus.UPLOADED, DocumentStatus.PARSED}),
    DocumentAction.CHUNK: frozenset(
        {DocumentStatus.PARSED, DocumentStatus.CHUNKED, DocumentStatus.VECTORIZED}
    ),
    DocumentAction.VECTORIZE: frozenset({DocumentStatus.CHUNKED, DocumentStatus.VECTORIZED}),
}

_TARGETS: dict[DocumentAction, DocumentStatus] = {
    DocumentAction.PARSE: DocumentStatus.PARSED,
    DocumentAction.CHUNK: DocumentStatus.CHUNKED,
    DocumentAction.VECTORIZE: DocumentStatus.VECTORIZED,
}


def parse_status(raw: str | None) -> DocumentStatus:
    if raw is None or raw == "":
        raise UnknownDocumentStatusError(str(raw))
    try:
        return DocumentStatus(raw)
    except ValueError as exc:
        raise UnknownDocumentStatusError(str(raw)) from exc


def can_transition(status: str | DocumentStatus, action: DocumentAction | str) -> bool:
    current = parse_status(status) if isinstance(status, str) else status
    act = DocumentAction(action) if isinstance(action, str) else action
    return current in _ALLOWED.get(act, frozenset())


def assert_can_parse(status: str) -> DocumentStatus:
    return _assert(status, DocumentAction.PARSE)


def assert_can_chunk(status: str) -> DocumentStatus:
    return _assert(status, DocumentAction.CHUNK)


def assert_can_vectorize(status: str) -> DocumentStatus:
    return _assert(status, DocumentAction.VECTORIZE)


def target_after(action: DocumentAction | str) -> DocumentStatus:
    act = DocumentAction(action) if isinstance(action, str) else action
    return _TARGETS[act]


def _assert(status: str, action: DocumentAction) -> DocumentStatus:
    current = parse_status(status)
    if current not in _ALLOWED[action]:
        raise InvalidDocumentTransitionError(
            f"cannot {action.value} from {current.value}",
            current=current.value,
            action=action.value,
        )
    return current


def transition_table() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for action, allowed in _ALLOWED.items():
        target = _TARGETS[action].value
        for st in sorted(s.value for s in allowed):
            rows.append((st, action.value, target))
    return rows


def assert_kb_usable(status: str | None) -> None:
    """Upload / retrieve require an active knowledge base."""
    if status != KnowledgeBaseStatus.ACTIVE.value:
        raise KnowledgeBaseNotUsableError(str(status))


def assert_upload_payload(text: str) -> None:
    if text is None or not str(text).strip():
        raise EmptyUploadError()
