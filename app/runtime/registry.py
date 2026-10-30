"""Resolve the configured AgentRuntime. Default remains single-agent."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.runtime.agentscope_team import AgentscopeTeamRuntime
from app.runtime.exceptions import RuntimeConfigError
from app.runtime.models import RuntimeId
from app.runtime.protocol import AgentRuntime
from app.runtime.single import SingleAgentRuntime


def resolve_runtime(settings: Settings | None = None) -> AgentRuntime:
    """Pick runtime from settings. Never auto-enable AgentScope."""
    cfg = settings or get_settings()
    name = (cfg.agent_runtime or RuntimeId.SINGLE.value).strip().lower()

    if name in {RuntimeId.SINGLE.value, "default", ""}:
        return SingleAgentRuntime()

    if name == RuntimeId.AGENTSCOPE_TEAM.value:
        if not cfg.agentscope_enabled:
            raise RuntimeConfigError(
                "agent_runtime=agentscope_team requires AGENTSCOPE_ENABLED=true; "
                "Phase 1 keeps multi-agent off by default."
            )
        return AgentscopeTeamRuntime(settings=cfg)

    raise RuntimeConfigError(f"unknown agent_runtime={name!r}")
