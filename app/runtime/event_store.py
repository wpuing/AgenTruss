"""Execution event persistence interface with mandatory redaction on append."""

from __future__ import annotations

from typing import Protocol

from app.runtime.events import ExecutionEvent
from app.security.redaction import redact_for_trace


class EventStore(Protocol):
    def append(self, event: ExecutionEvent | dict) -> None: ...

    def list_by_run(self, tenant_id: str, run_id: str) -> list[dict]: ...

    def list_by_trace(self, tenant_id: str, trace_id: str) -> list[dict]: ...


class InMemoryEventStore:
    """Process-local event log. Cleared on restart — not a checkpoint store."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def append(self, event: ExecutionEvent | dict) -> None:
        row = event.as_dict() if isinstance(event, ExecutionEvent) else dict(event)
        # Full recursive redaction — not only key-name filtering.
        safe = redact_for_trace(row)
        if isinstance(safe, dict):
            self._rows.append(safe)
        else:
            self._rows.append({"message": str(safe)})

    def list_by_run(self, tenant_id: str, run_id: str) -> list[dict]:
        return [r for r in self._rows if r.get("tenant_id") == tenant_id and r.get("run_id") == run_id]

    def list_by_trace(self, tenant_id: str, trace_id: str) -> list[dict]:
        return [r for r in self._rows if r.get("tenant_id") == tenant_id and r.get("trace_id") == trace_id]


default_event_store = InMemoryEventStore()
