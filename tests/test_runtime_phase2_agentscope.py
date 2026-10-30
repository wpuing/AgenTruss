"""Phase-2 AgentScope TeamPipeline adapter tests.

Requires optional dependency: ``pip install -e '.[agentscope]'`` (agentscope==2.0.10).
Tests skip when the package is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.config import Settings
from app.core.platform import Platform
from app.runtime import RuntimeConfigError, RuntimeId, RunRequest, RunStatus, SubjectContext, resolve_runtime
from app.runtime.agentscope_ext.availability import agentscope_available
from app.runtime.agentscope_ext.provider import AgentScopeProvider
from app.runtime.agentscope_ext.team_config import load_team_config
from app.runtime.agentscope_team import AgentscopeTeamRuntime
from app.runtime.single import SingleAgentRuntime

pytestmark = pytest.mark.skipif(
    not agentscope_available(),
    reason="agentscope==2.0.10 not installed",
)


def _subject(platform: Platform) -> SubjectContext:
    user = platform.user_by_name("admin")
    assert user is not None
    return SubjectContext(
        user_id=user["id"],
        tenant_id=user["tenant_id"],
        roles=list(user["roles"]),
        perms=sorted(user["perms"]),
    )


def _enabled_settings(**kwargs) -> Settings:
    # Force stub so suite order / process .env Live keys cannot flip TeamPipeline to live.
    base = dict(
        agent_runtime="agentscope_team",
        agentscope_enabled=True,
        agentscope_timeout_ms=15_000,
        agentscope_model_mode="stub",
        dashscope_api_key="",
        openai_api_key="",
    )
    base.update(kwargs)
    base["agentscope_model_mode"] = "stub"
    return Settings(**base)


def test_single_default_unchanged_when_agentscope_installed():
    runtime = resolve_runtime(Settings(agent_runtime="single", agentscope_enabled=False))
    assert isinstance(runtime, SingleAgentRuntime)
    platform = Platform.seed()
    result = runtime.execute(platform, RunRequest(message="查询订单", subject=_subject(platform)))
    assert result.status == RunStatus.SUCCEEDED
    assert result.runtime_id == RuntimeId.SINGLE


def test_team_pipeline_success_maps_events_and_reply():
    platform = Platform.seed()
    settings = _enabled_settings()
    runtime = AgentscopeTeamRuntime(settings=settings)
    result = runtime.execute(
        platform,
        RunRequest(
            message="订单物流怎么查",
            subject=_subject(platform),
            runtime_id=RuntimeId.AGENTSCOPE_TEAM,
        ),
    )
    assert result.status == RunStatus.SUCCEEDED
    assert result.runtime_id == RuntimeId.AGENTSCOPE_TEAM
    assert "综合成员结果" in result.reply
    assert result.usage.llm_calls >= 3
    types = {e["type"] for e in result.events}
    assert "run_started" in types
    assert "run_finished" in types
    assert "agent_started" in types
    assert result.structured.get("leader") == "Leader"
    assert "Researcher" in result.structured.get("members", [])


def test_timeout_returns_timeout_status():
    platform = Platform.seed()
    settings = _enabled_settings(agentscope_timeout_ms=50)
    runtime = AgentscopeTeamRuntime(settings=settings)
    result = runtime.execute(
        platform,
        RunRequest(
            message="订单问题",
            subject=_subject(platform),
            timeout_ms=50,
            metadata={"inject_sleep_s": 1.0},
        ),
    )
    assert result.status == RunStatus.TIMEOUT
    assert result.error_code == "timeout"


def test_model_exception_maps_to_failed():
    platform = Platform.seed()
    # Build a team config that injects an error in leader stub steps.
    cfg = load_team_config()
    cfg.stub_script.leader_steps = [{"error": "boom-from-model"}]
    provider = AgentScopeProvider(settings=_enabled_settings(), team_config=cfg)
    result = provider.run(platform, RunRequest(message="订单", subject=_subject(platform)))
    assert result.status == RunStatus.FAILED
    assert result.error_code == "runtime_error"
    assert "boom-from-model" in (result.error_message or "")


def test_illegal_team_config_rejected(tmp_path: Path):
    bad = tmp_path / "bad_team.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "leader": {"name": "Leader", "system_prompt": "x"},
                "members": [
                    {"name": "Leader", "description": "dup", "system_prompt": "y", "tools": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeConfigError):
        load_team_config(bad)
    settings = _enabled_settings(agentscope_team_config=str(bad))
    runtime = AgentscopeTeamRuntime(settings=settings)
    result = runtime.execute(platform=Platform.seed(), request=RunRequest(message="订单", subject=_subject(Platform.seed())))
    assert result.status == RunStatus.FAILED
    assert result.error_code == "invalid_config"


def test_permission_denied_tool_maps_to_blocked():
    platform = Platform.seed()
    agent = platform.default_agent("demo")
    agent["enabled_tools"] = []  # deny all platform tools
    settings = _enabled_settings()
    runtime = AgentscopeTeamRuntime(settings=settings)
    result = runtime.execute(
        platform,
        RunRequest(
            message="订单核查",
            subject=_subject(platform),
            metadata={
                "force_member_tool": {
                    "member": "RiskChecker",
                    "tool": "query_order",
                    "args": {},
                }
            },
        ),
    )
    assert result.status == RunStatus.BLOCKED
    assert result.error_code == "tool_denied"
    assert result.guardrail_hit is True
    assert any(e["type"] == "tool_failed" for e in result.events)


def test_high_risk_tool_maps_to_approval_required():
    platform = Platform.seed()
    settings = _enabled_settings()
    runtime = AgentscopeTeamRuntime(settings=settings)
    result = runtime.execute(
        platform,
        RunRequest(
            message="订单退款核查",
            subject=_subject(platform),
            metadata={
                "force_member_tool": {
                    "member": "RiskChecker",
                    "tool": "refund_order",
                    "args": {"order_id": "1001"},
                }
            },
        ),
    )
    assert result.status == RunStatus.APPROVAL_REQUIRED
    assert result.need_human is True
    assert result.ticket_id
    assert any(e["type"] == "approval_requested" for e in result.events)


def test_resolve_runtime_selects_team_when_enabled():
    runtime = resolve_runtime(_enabled_settings())
    assert isinstance(runtime, AgentscopeTeamRuntime)
