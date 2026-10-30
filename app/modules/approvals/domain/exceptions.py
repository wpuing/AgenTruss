"""Domain errors for approval tickets (HTTP-agnostic)."""

from __future__ import annotations


class ApprovalDomainError(Exception):
    """Base domain error; application maps to HTTP."""

    def __init__(self, message: str, *, code: str = "domain_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class InvalidTransitionError(ApprovalDomainError):
    """Status transition is not allowed."""

    def __init__(self, message: str, *, current: str, action: str) -> None:
        super().__init__(message, code="invalid_transition")
        self.current = current
        self.action = action


class UnknownStatusError(ApprovalDomainError):
    """Persisted status is not in the known enum."""

    def __init__(self, status: str) -> None:
        super().__init__(f"unknown ticket status={status}", code="unknown_status")
        self.status = status


class TicketConflictError(ApprovalDomainError):
    """CAS / concurrent claim lost."""

    def __init__(self, message: str = "ticket already executing") -> None:
        super().__init__(message, code="conflict")


class TicketNotExecutableError(ApprovalDomainError):
    """Execute preconditions failed (not approved, terminal, etc.)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_executable")


class ExecutionFailedError(ApprovalDomainError):
    """Tool side-effect failed after claim; ticket should be marked failed."""

    def __init__(self, message: str = "tool execution failed") -> None:
        super().__init__(message, code="execution_failed")


class OutcomeUncertainError(ApprovalDomainError):
    """Tool result unknown (timeout / transport); ticket needs manual reconcile."""

    def __init__(self, message: str = "tool outcome uncertain") -> None:
        super().__init__(message, code="outcome_uncertain")


class RevalidationFailedError(ApprovalDomainError):
    """Pre-claim revalidation failed; ticket may already be marked failed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="revalidation_failed")
