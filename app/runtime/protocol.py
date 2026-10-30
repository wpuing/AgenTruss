"""AgentRuntime protocol. Workflows depend on this, not on AgentScope classes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.platform import Platform
from app.runtime.models import RunRequest, RunResult, RuntimeId


@runtime_checkable
class AgentRuntime(Protocol):
    """Pluggable sub-task executor behind LangGraph/linear turn orchestration."""

    @property
    def runtime_id(self) -> RuntimeId:
        """Stable identifier used in config and audit."""

    def execute(self, platform: Platform, request: RunRequest) -> RunResult:
        """Run one request synchronously (MVP). Async adapters wrap this later."""
