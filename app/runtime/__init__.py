"""Optional agent runtimes behind a unified protocol (Phase 1).

Default path stays ``single`` (existing turn pipeline). AgentScope TeamPipeline
is an optional Phase-2 adapter and is not imported until implemented.
"""

from app.runtime.events import EventType, ExecutionEvent
from app.runtime.exceptions import RuntimeConfigError, RuntimeUnavailableError
from app.runtime.models import (
    RunRequest,
    RunResult,
    RunStatus,
    RuntimeId,
    SubjectContext,
    UsageStats,
)
from app.runtime.registry import resolve_runtime

__all__ = [
    "EventType",
    "ExecutionEvent",
    "RuntimeConfigError",
    "RuntimeUnavailableError",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "RuntimeId",
    "SubjectContext",
    "UsageStats",
    "resolve_runtime",
]
