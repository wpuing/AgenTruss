"""Knowledge domain — document pipeline status (no FastAPI / DB)."""

from __future__ import annotations

import pytest

from app.modules.knowledge.domain.enums import DocumentAction, DocumentStatus
from app.modules.knowledge.domain.exceptions import (
    InvalidDocumentTransitionError,
    UnknownDocumentStatusError,
)
from app.modules.knowledge.domain.policies import (
    assert_can_chunk,
    assert_can_parse,
    assert_can_vectorize,
    can_transition,
    parse_status,
    target_after,
    transition_table,
)


@pytest.mark.parametrize(
    "status,action",
    [
        ("uploaded", DocumentAction.PARSE),
        ("parsed", DocumentAction.PARSE),
        ("parsed", DocumentAction.CHUNK),
        ("chunked", DocumentAction.CHUNK),
        ("vectorized", DocumentAction.CHUNK),
        ("chunked", DocumentAction.VECTORIZE),
        ("vectorized", DocumentAction.VECTORIZE),
    ],
)
def test_legal_doc_transitions(status, action):
    assert can_transition(status, action)


@pytest.mark.parametrize(
    "status,action",
    [
        ("uploaded", DocumentAction.CHUNK),
        ("uploaded", DocumentAction.VECTORIZE),
        ("parsed", DocumentAction.VECTORIZE),
        ("chunked", DocumentAction.PARSE),
    ],
)
def test_illegal_doc_transitions(status, action):
    assert not can_transition(status, action)
    with pytest.raises(InvalidDocumentTransitionError):
        if action == DocumentAction.PARSE:
            assert_can_parse(status)
        elif action == DocumentAction.CHUNK:
            assert_can_chunk(status)
        else:
            assert_can_vectorize(status)


def test_unknown_status():
    with pytest.raises(UnknownDocumentStatusError):
        parse_status("weird")


def test_targets():
    assert target_after(DocumentAction.PARSE) == DocumentStatus.PARSED
    assert ("uploaded", "parse", "parsed") in transition_table()


def test_kb_usable_and_upload_payload():
    from app.modules.knowledge.domain.exceptions import EmptyUploadError, KnowledgeBaseNotUsableError
    from app.modules.knowledge.domain.policies import assert_kb_usable, assert_upload_payload

    assert_kb_usable("active")
    with pytest.raises(KnowledgeBaseNotUsableError):
        assert_kb_usable("disabled")
    assert_upload_payload("ok")
    with pytest.raises(EmptyUploadError):
        assert_upload_payload("   ")
