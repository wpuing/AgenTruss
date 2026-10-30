"""Knowledge domain errors (HTTP-agnostic)."""

from __future__ import annotations


class KnowledgeDomainError(Exception):
    def __init__(self, message: str, *, code: str = "knowledge_domain_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class InvalidDocumentTransitionError(KnowledgeDomainError):
    def __init__(self, message: str, *, current: str, action: str) -> None:
        super().__init__(message, code="invalid_document_transition")
        self.current = current
        self.action = action


class UnknownDocumentStatusError(KnowledgeDomainError):
    def __init__(self, status: str) -> None:
        super().__init__(f"unknown document status={status}", code="unknown_document_status")
        self.status = status


class KnowledgeBaseNotEmptyError(KnowledgeDomainError):
    def __init__(self) -> None:
        super().__init__("kb not empty", code="kb_not_empty")


class KnowledgeBaseNotUsableError(KnowledgeDomainError):
    def __init__(self, status: str) -> None:
        super().__init__(f"kb not usable status={status}", code="kb_not_usable")
        self.status = status


class EmptyUploadError(KnowledgeDomainError):
    def __init__(self) -> None:
        super().__init__("upload text is empty", code="empty_upload")
