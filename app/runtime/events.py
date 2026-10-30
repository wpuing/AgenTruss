"""Platform-level execution events (Phase 1).

Events are serializable dict-friendly models. Sensitive payloads must be
masked before append; keep payloads small for in-memory MVP audit lists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    MESSAGE_EMITTED = "message_emitted"
    TOOL_REQUESTED = "tool_requested"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_COMPLETED = "approval_completed"
    EXECUTION_PAUSED = "execution_paused"
    EXECUTION_RESUMED = "execution_resumed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionEvent(BaseModel):
    """One correlatable execution event."""

    schema_version: str = "1"
    type: EventType
    trace_id: str
    run_id: str
    tenant_id: str
    conversation_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    tool_call_id: str | None = None
    config_version: str | None = None
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    at: str = Field(default_factory=_utc_now)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
