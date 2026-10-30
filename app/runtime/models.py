"""Unified agent-runtime request and result models (Phase 1).

These models are framework-agnostic. AgentScope adapters must map into them;
business code must not parse runtime-specific strings to decide success.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RuntimeId(str, Enum):
    """Known runtime identifiers. Extend only when an adapter exists."""

    SINGLE = "single"
    AGENTSCOPE_TEAM = "agentscope_team"


class RunStatus(str, Enum):
    """Terminal or paused statuses for one sub-run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    APPROVAL_REQUIRED = "approval_required"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class SubjectContext(BaseModel):
    """Trusted identity. Must come from JWT/AuthContext, never from client body."""

    user_id: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)
    perms: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    """One agent/sub-task execution request."""

    schema_version: str = "1"
    runtime_id: RuntimeId = RuntimeId.SINGLE
    message: str = Field(max_length=2000)
    subject: SubjectContext
    session_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    channel: str = "web"
    config_version: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    timeout_ms: int | None = Field(default=None, ge=1, le=600_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageStats(BaseModel):
    """Optional token / cost hints. Zero when no real model was called."""

    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    llm_calls: int = 0


class RunResult(BaseModel):
    """Structured outcome of one runtime execution."""

    schema_version: str = "1"
    status: RunStatus
    runtime_id: RuntimeId
    reply: str = ""
    structured: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    trace_id: str
    run_id: str
    session_id: str | None = None
    audit_id: str | None = None
    ticket_id: str | None = None
    need_human: bool = False
    guardrail_hit: bool = False
    sources: list[str] = Field(default_factory=list)
    usage: UsageStats = Field(default_factory=UsageStats)
    events: list[dict[str, Any]] = Field(default_factory=list)
