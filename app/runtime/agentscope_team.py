"""AgentScope TeamPipeline runtime (Phase 2).

Uses ``AgentScopeProvider`` when ``agentscope`` is installed and enabled.
Keeps lazy import so the default single-agent path never loads AgentScope.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.platform import Platform
from app.runtime.agentscope_ext.availability import agentscope_available
from app.runtime.agentscope_ext.provider import AgentScopeProvider
from app.runtime.events import EventType, ExecutionEvent
from app.runtime.models import RunRequest, RunResult, RunStatus, RuntimeId
from app.core.platform import new_id


class AgentscopeTeamRuntime:
    """Optional multi-agent runtime backed by AgentScope 2.0.x TeamPipeline."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def runtime_id(self) -> RuntimeId:
        return RuntimeId.AGENTSCOPE_TEAM

    def execute(self, platform: Platform, request: RunRequest) -> RunResult:
        if not self.settings.agentscope_enabled:
            return _unavailable(
                request,
                "agentscope_team requires AGENTSCOPE_ENABLED=true",
                code="agentscope_disabled",
            )
        if not agentscope_available():
            return _unavailable(
                request,
                'agentscope not installed; run: pip install -e ".[agentscope]"',
                code="agentscope_unavailable",
            )
        provider = AgentScopeProvider(settings=self.settings)
        return provider.run(platform, request)


def _unavailable(request: RunRequest, message: str, *, code: str) -> RunResult:
    trace_id = request.trace_id or new_id("trc")
    run_id = request.run_id or new_id("run")
    event = ExecutionEvent(
        type=EventType.RUN_FAILED,
        trace_id=trace_id,
        run_id=run_id,
        tenant_id=request.subject.tenant_id,
        session_id=request.session_id,
        agent_id=request.agent_id,
        message=message,
        payload={"reason": code},
    )
    return RunResult(
        status=RunStatus.UNAVAILABLE,
        runtime_id=RuntimeId.AGENTSCOPE_TEAM,
        error_code=code,
        error_message=message,
        trace_id=trace_id,
        run_id=run_id,
        session_id=request.session_id,
        events=[event.as_dict()],
    )
