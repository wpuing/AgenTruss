"""Phase-1 agent runtime protocol tests (no AgentScope install required)."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.platform import Platform
from app.runtime import (
    RuntimeConfigError,
    RuntimeId,
    RunRequest,
    RunStatus,
    SubjectContext,
    resolve_runtime,
)
from app.runtime.agentscope_team import AgentscopeTeamRuntime
from app.runtime.single import SingleAgentRuntime


def _subject(platform: Platform) -> SubjectContext:
    user = platform.user_by_name("admin")
    assert user is not None
    return SubjectContext(
        user_id=user["id"],
        tenant_id=user["tenant_id"],
        roles=list(user["roles"]),
        perms=sorted(user["perms"]),
    )


def test_default_runtime_is_single():
    runtime = resolve_runtime(Settings(agent_runtime="single", agentscope_enabled=False))
    assert isinstance(runtime, SingleAgentRuntime)
    assert runtime.runtime_id == RuntimeId.SINGLE


def test_agentscope_requires_explicit_enable_flag():
    with pytest.raises(RuntimeConfigError, match="AGENTSCOPE_ENABLED"):
        resolve_runtime(Settings(agent_runtime="agentscope_team", agentscope_enabled=False))


def test_unknown_runtime_rejected():
    with pytest.raises(RuntimeConfigError, match="unknown"):
        resolve_runtime(Settings(agent_runtime="langgraph_fantasy"))


def test_single_runtime_executes_existing_chat_path():
    platform = Platform.seed()
    runtime = SingleAgentRuntime()
    result = runtime.execute(
        platform,
        RunRequest(
            message="查询订单",
            subject=_subject(platform),
            channel="web",
        ),
    )
    assert result.runtime_id == RuntimeId.SINGLE
    assert result.status == RunStatus.SUCCEEDED
    assert result.audit_id
    assert result.trace_id
    assert result.run_id
    assert any(evt["type"] == "run_started" for evt in result.events)
    assert any(evt["type"] == "run_finished" for evt in result.events)
    assert "订单查询成功" in result.reply


def test_single_runtime_maps_high_risk_to_approval_required():
    platform = Platform.seed()
    runtime = SingleAgentRuntime()
    result = runtime.execute(
        platform,
        RunRequest(message="订单申请退款", subject=_subject(platform)),
    )
    assert result.status == RunStatus.APPROVAL_REQUIRED
    assert result.need_human is True
    assert result.ticket_id
    assert any(evt["type"] == "approval_requested" for evt in result.events)


def test_agentscope_disabled_returns_unavailable_not_success():
    platform = Platform.seed()
    runtime = AgentscopeTeamRuntime(Settings(agentscope_enabled=False))
    assert runtime.runtime_id == RuntimeId.AGENTSCOPE_TEAM
    result = runtime.execute(
        platform,
        RunRequest(message="hello", subject=_subject(platform)),
    )
    assert result.status == RunStatus.UNAVAILABLE
    assert result.error_code == "agentscope_disabled"


def test_enabled_flag_selects_team_runtime_class():
    runtime = resolve_runtime(Settings(agent_runtime="agentscope_team", agentscope_enabled=True))
    assert isinstance(runtime, AgentscopeTeamRuntime)
