"""AgentScopeProvider: TeamPipeline adapter behind the unified runtime API."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from fastapi import HTTPException

from app.core.config import Settings, agentscope_timeout_from_env, get_settings
from app.core.platform import Platform, new_id
from app.core.security import AuthContext
from app.guardrails.engine import check_input, check_output, mask_pii
from app.memory.store import append_turn, history_messages, read_turns
from app.runtime.agentscope_ext.availability import agentscope_available, require_agentscope
from app.runtime.agentscope_ext.gateway_model import PlatformModelGateway, build_stub_chat_model
from app.runtime.agentscope_ext.gateway_tools import ToolGatewayState, build_member_toolkit
from app.runtime.agentscope_ext.live_model import ModelNotConfiguredError, build_live_chat_model
from app.runtime.agentscope_ext.team_config import TeamConfig, load_team_config
from app.runtime.event_store import default_event_store
from app.runtime.events import EventType, ExecutionEvent
from app.runtime.exceptions import RuntimeConfigError, RuntimeUnavailableError
from app.runtime.models import RunRequest, RunResult, RunStatus, RuntimeId, UsageStats
from app.security.redaction import redact_for_api, redact_for_llm, redact_for_logs, sanitize_exception


def _persist_audit(platform: Platform, audit: dict[str, Any]) -> None:
    """Keep memory list and durable audit_logs in sync (parity with single/LangGraph)."""
    safe = redact_for_api(audit)
    platform.audits.append(safe if isinstance(safe, dict) else audit)
    try:
        from app.infra.entity_repo import append_audit

        append_audit(safe if isinstance(safe, dict) else audit)
    except Exception:  # noqa: BLE001
        from app.infra.ticket_repo import persistence_enabled

        if persistence_enabled():
            raise


def resolve_team_timeout_ms(request: RunRequest, settings: Settings, team: TeamConfig) -> int:
    """Priority: request override → explicit AGENTSCOPE_TIMEOUT_MS env → team YAML → settings default."""
    if request.timeout_ms:
        return int(request.timeout_ms)
    if agentscope_timeout_from_env():
        return int(settings.agentscope_timeout_ms)
    return int(team.timeout_ms or settings.agentscope_timeout_ms)


class AgentScopeProvider:
    """Build and run a configured TeamPipeline through platform gateways."""

    def __init__(self, settings: Settings | None = None, team_config: TeamConfig | None = None) -> None:
        self.settings = settings or get_settings()
        self._team_config = team_config

    def run(self, platform: Platform, request: RunRequest) -> RunResult:
        """Synchronous entry used by AgentscopeTeamRuntime."""
        trace_id = request.trace_id or new_id("trc")
        run_id = request.run_id or new_id("run")
        events: list[dict] = []
        started = time.perf_counter()
        audit_id = new_id("aud")

        def emit(event_type: EventType, message: str | None = None, **payload: Any) -> None:
            event = ExecutionEvent(
                type=event_type,
                trace_id=trace_id,
                run_id=run_id,
                tenant_id=request.subject.tenant_id,
                conversation_id=request.conversation_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                config_version=request.config_version,
                message=message,
                tool_call_id=payload.pop("tool_call_id", None),
                payload=payload,
            )
            row = event.as_dict()
            events.append(row)
            default_event_store.append(row)

        if not self.settings.agentscope_enabled:
            raise RuntimeConfigError("AGENTSCOPE_ENABLED must be true")

        if not agentscope_available():
            emit(EventType.RUN_FAILED, message="agentscope missing")
            return RunResult(
                status=RunStatus.UNAVAILABLE,
                runtime_id=RuntimeId.AGENTSCOPE_TEAM,
                error_code="agentscope_unavailable",
                error_message='agentscope not installed; pip install -e ".[agentscope]"',
                trace_id=trace_id,
                run_id=run_id,
                session_id=request.session_id,
                audit_id=audit_id,
                events=events,
            )

        try:
            require_agentscope()
            cfg_path = self.settings.agentscope_team_config or None
            team = self._team_config or load_team_config(cfg_path)
        except (RuntimeConfigError, RuntimeUnavailableError) as exc:
            safe = sanitize_exception(exc)
            emit(EventType.RUN_FAILED, message=safe)
            return RunResult(
                status=RunStatus.FAILED if isinstance(exc, RuntimeConfigError) else RunStatus.UNAVAILABLE,
                runtime_id=RuntimeId.AGENTSCOPE_TEAM,
                error_code="invalid_config" if isinstance(exc, RuntimeConfigError) else "agentscope_unavailable",
                error_message=safe,
                trace_id=trace_id,
                run_id=run_id,
                session_id=request.session_id,
                audit_id=audit_id,
                events=events,
            )

        timeout_ms = resolve_team_timeout_ms(request, self.settings, team)
        emit(EventType.RUN_STARTED, timeout_ms=timeout_ms, model_mode=self.settings.agentscope_model_mode)
        emit(EventType.AGENT_STARTED, runtime="agentscope_team", leader=team.leader.name)

        auth = AuthContext(
            user_id=request.subject.user_id,
            tenant_id=request.subject.tenant_id,
            roles=list(request.subject.roles),
            perms=set(request.subject.perms),
        )
        try:
            agent = _resolve_agent(platform, auth.tenant_id, request.agent_id)
        except HTTPException:
            # Propagate published-gate 404; do not convert to FAILED RunResult.
            raise
        except RuntimeConfigError as exc:
            safe = sanitize_exception(exc)
            emit(EventType.RUN_FAILED, message=safe)
            return RunResult(
                status=RunStatus.FAILED,
                runtime_id=RuntimeId.AGENTSCOPE_TEAM,
                error_code="invalid_config",
                error_message=safe,
                trace_id=trace_id,
                run_id=run_id,
                session_id=request.session_id,
                audit_id=audit_id,
                events=events,
            )

        industry = platform.industries[agent["industry"]]
        session_id = request.session_id or new_id("ses")
        audit = {
            "audit_id": audit_id,
            "user_id": auth.user_id,
            "tenant_id": auth.tenant_id,
            "session_id": session_id,
            "channel": request.channel,
            "agent_id": agent["agent_id"],
            "runtime_id": RuntimeId.AGENTSCOPE_TEAM.value,
            "trace_id": trace_id,
            "run_id": run_id,
            "input_message": redact_for_logs(mask_pii(request.message)),
            "llm_called": False,
            "blocked": False,
            "need_human": False,
            "ticket_id": None,
            "sources": [],
        }
        guard = check_input(request.message, industry)
        audit["topic"] = guard.topic
        audit["confidence"] = guard.confidence
        audit["sanitized"] = guard.sanitized_message
        if not guard.allowed:
            audit["blocked"] = guard.reason in {"injection", "too_long"}
            audit["block_reason"] = guard.reason
            audit["output_message"] = guard.block_message
            audit["latency_ms"] = int((time.perf_counter() - started) * 1000)
            _persist_audit(platform, audit)
            emit(EventType.AGENT_FAILED, block_reason=guard.reason)
            emit(EventType.RUN_FINISHED, status=RunStatus.BLOCKED.value)
            return RunResult(
                status=RunStatus.BLOCKED,
                runtime_id=RuntimeId.AGENTSCOPE_TEAM,
                reply=guard.block_message,
                structured={"block_reason": guard.reason, "llm_called": False, "llm_mode": "stub"},
                error_code=guard.reason,
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                audit_id=audit_id,
                guardrail_hit=True,
                events=events,
            )

        # Credentials must not enter external models — honor blocked, not only .value.
        llm_prep = redact_for_llm(guard.sanitized_message)
        if llm_prep.blocked:
            audit["blocked"] = True
            audit["block_reason"] = "credential_in_prompt"
            audit["output_message"] = "请求包含不可发送给模型的凭证信息，已拦截。"
            audit["latency_ms"] = int((time.perf_counter() - started) * 1000)
            _persist_audit(platform, audit)
            emit(EventType.AGENT_FAILED, block_reason="credential_in_prompt")
            emit(EventType.RUN_FINISHED, status=RunStatus.BLOCKED.value)
            return RunResult(
                status=RunStatus.BLOCKED,
                runtime_id=RuntimeId.AGENTSCOPE_TEAM,
                reply=audit["output_message"],
                structured={"block_reason": "credential_in_prompt", "llm_called": False, "llm_mode": "stub"},
                error_code="credential_in_prompt",
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                audit_id=audit_id,
                guardrail_hit=True,
                events=events,
            )
        # Hydrate prior turns for gateways that want conversation context.
        audit["history_roles"] = [
            item["role"] for item in history_messages(read_turns(platform, auth.tenant_id, session_id))
        ]
        llm_msg = llm_prep.value
        guarded_request = request.model_copy(update={"message": llm_msg, "session_id": session_id})
        tool_state = ToolGatewayState()
        gateway = PlatformModelGateway(max_llm_calls=team.max_llm_calls)

        async def _bounded() -> tuple[str, dict[str, Any]]:
            sleep_s = float((request.metadata or {}).get("inject_sleep_s") or 0)
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
            return await self._run_pipeline(
                platform=platform,
                auth=auth,
                agent=agent,
                team=team,
                request=guarded_request,
                gateway=gateway,
                tool_state=tool_state,
                emit=emit,
            )

        def _fail(status: RunStatus, code: str, message: str, *, guardrail: bool = False) -> RunResult:
            safe_msg = sanitize_exception(message)
            audit["blocked"] = guardrail or status == RunStatus.BLOCKED
            audit["block_reason"] = code
            audit["output_message"] = safe_msg
            audit["llm_called"] = gateway.llm_calls > 0
            audit["latency_ms"] = int((time.perf_counter() - started) * 1000)
            _persist_audit(platform, audit)
            return RunResult(
                status=status,
                runtime_id=RuntimeId.AGENTSCOPE_TEAM,
                reply=safe_msg,
                structured={
                    "block_reason": code,
                    "llm_called": bool(audit["llm_called"]),
                    "llm_mode": self.settings.agentscope_model_mode,
                },
                error_code=code,
                error_message=safe_msg,
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                audit_id=audit_id,
                guardrail_hit=guardrail,
                usage=UsageStats(
                    llm_calls=gateway.llm_calls,
                    latency_ms=audit["latency_ms"],
                ),
                events=events,
            )

        try:
            reply_text, structured = asyncio.run(
                asyncio.wait_for(_bounded(), timeout=timeout_ms / 1000.0)
            )
        except asyncio.TimeoutError:
            emit(EventType.RUN_FAILED, message="timeout")
            return _fail(RunStatus.TIMEOUT, "timeout", f"TeamPipeline exceeded {timeout_ms}ms")
        except ModelNotConfiguredError as exc:
            emit(EventType.RUN_FAILED, message=sanitize_exception(exc))
            return _fail(RunStatus.FAILED, "model_not_configured", sanitize_exception(exc))
        except RuntimeConfigError as exc:
            emit(EventType.RUN_FAILED, message=sanitize_exception(exc))
            # Classify on sanitized text only — never log/emit raw SDK exception strings.
            safe = sanitize_exception(exc)
            code = "model_not_configured" if "credential" in safe.lower() else "invalid_config"
            emit(EventType.RUN_FAILED, message=safe)
            return _fail(RunStatus.FAILED, code, safe)
        except Exception as exc:  # noqa: BLE001
            safe = sanitize_exception(exc)
            emit(EventType.AGENT_FAILED, message=safe)
            emit(EventType.RUN_FAILED, message=safe)
            code = "model_budget_exceeded" if "budget" in safe.lower() else "runtime_error"
            return _fail(RunStatus.FAILED, code, safe)

        output = check_output(reply_text or "", industry)
        if not output.allowed:
            emit(EventType.AGENT_FAILED, block_reason=output.reason)
            emit(EventType.RUN_FINISHED, status=RunStatus.BLOCKED.value)
            return _fail(
                RunStatus.BLOCKED,
                output.reason or "output_leak",
                output.block_message,
                guardrail=True,
            )

        status = RunStatus.SUCCEEDED
        error_code = None
        if tool_state.approval_required:
            status = RunStatus.APPROVAL_REQUIRED
            error_code = "approval_required"
            emit(EventType.EXECUTION_PAUSED, message="approval_required")
        elif tool_state.denied:
            status = RunStatus.BLOCKED
            error_code = "tool_denied"

        emit(EventType.AGENT_COMPLETED if status == RunStatus.SUCCEEDED else EventType.AGENT_FAILED)
        emit(EventType.RUN_FINISHED, status=status.value)
        emit(EventType.MESSAGE_EMITTED, preview=(reply_text or "")[:120])

        audit["llm_called"] = gateway.llm_calls > 0
        audit["need_human"] = bool(tool_state.approval_required)
        audit["ticket_id"] = tool_state.last_ticket_id
        audit["blocked"] = status == RunStatus.BLOCKED
        audit["block_reason"] = error_code
        audit["output_message"] = redact_for_logs((reply_text or "")[:2000])
        audit["latency_ms"] = int((time.perf_counter() - started) * 1000)
        _persist_audit(platform, audit)
        append_turn(
            platform,
            auth.tenant_id,
            auth.user_id,
            session_id,
            guard.sanitized_message or "",
            reply_text or "",
            blocked=bool(audit["blocked"]),
        )

        structured = dict(structured or {})
        structured["model_mode"] = self.settings.agentscope_model_mode
        structured["llm_mode"] = self.settings.agentscope_model_mode
        structured["llm_called"] = bool(audit.get("llm_called"))
        structured["test_mode"] = self.settings.agentscope_model_mode == "stub"
        if error_code:
            structured["block_reason"] = error_code

        return RunResult(
            status=status,
            runtime_id=RuntimeId.AGENTSCOPE_TEAM,
            reply=reply_text or "",
            structured=structured,
            error_code=error_code,
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            audit_id=audit_id,
            ticket_id=tool_state.last_ticket_id,
            need_human=bool(tool_state.approval_required),
            guardrail_hit=bool(tool_state.denied),
            usage=UsageStats(
                llm_calls=gateway.llm_calls,
                latency_ms=audit["latency_ms"],
            ),
            events=events,
        )

    async def _run_pipeline(
        self,
        *,
        platform: Platform,
        auth: AuthContext,
        agent: dict,
        team: TeamConfig,
        request: RunRequest,
        gateway: PlatformModelGateway,
        tool_state: ToolGatewayState,
        emit: Callable[..., None],
    ) -> tuple[str, dict[str, Any]]:
        from agentscope.agent import Agent
        from agentscope.message import TextBlock, ToolCallBlock, UserMsg
        from agentscope.model import ChatResponse
        from agentscope.model._model_response import FinishedReason
        from agentscope.pipeline import TeamMember, TeamPipeline
        from agentscope.tool import Toolkit

        message = request.message
        mode = (self.settings.agentscope_model_mode or "stub").lower()
        live = mode == "live"

        if live:
            leader_model = build_live_chat_model(
                provider=team.leader.model.provider if team.leader.model.provider != "stub" else "dashscope",
                model_name=team.leader.model.model if team.leader.model.model != "gateway-stub" else "qwen-plus",
                gateway=gateway,
            )
        else:
            leader_responses = _build_leader_stub_responses(team, message)
            leader_model = build_stub_chat_model(leader_responses, gateway)

        members: list[TeamMember] = []
        for member in team.members:
            member_text = team.stub_script.member_replies.get(member.name) or f"{member.name} done"
            # Optional: force a platform tool call before the text reply when tools configured
            # and metadata requests it (used by permission tests).
            force_tool = (request.metadata or {}).get("force_member_tool")
            if live:
                member_model = build_live_chat_model(
                    provider=member.model.provider if member.model.provider != "stub" else "dashscope",
                    model_name=member.model.model if member.model.model != "gateway-stub" else "qwen-plus",
                    gateway=gateway,
                )
            else:
                responses: list[Any] = []
                if force_tool and force_tool.get("member") == member.name:
                    tool_name = force_tool.get("tool") or (member.tools[0] if member.tools else "query_order")
                    args = force_tool.get("args") or {}
                    responses.append(
                        ChatResponse(
                            content=[
                                ToolCallBlock(
                                    id=new_id("tc"),
                                    name=tool_name,
                                    input=json.dumps(args, ensure_ascii=False),
                                )
                            ],
                            is_last=True,
                            finished_reason=FinishedReason.COMPLETED,
                        )
                    )
                responses.append(
                    ChatResponse(
                        content=[TextBlock(text=member_text)],
                        is_last=True,
                        finished_reason=FinishedReason.COMPLETED,
                    )
                )
                member_model = build_stub_chat_model(responses, gateway)
            tool_names = list(member.tools)
            # Ensure forced test/demo tools are registered on the toolkit.
            if force_tool and force_tool.get("member") == member.name:
                forced_name = force_tool.get("tool")
                if forced_name and forced_name not in tool_names:
                    tool_names.append(str(forced_name))
            toolkit = (
                build_member_toolkit(
                    platform=platform,
                    auth=auth,
                    agent=agent,
                    tool_names=tool_names,
                    state=tool_state,
                    emit=emit,
                )
                if tool_names
                else Toolkit()
            )
            member_agent = Agent(
                name=member.name,
                system_prompt=member.system_prompt,
                model=member_model,
                toolkit=toolkit,
            )
            members.append(TeamMember(agent=member_agent, description=member.description))

        leader = Agent(
            name=team.leader.name,
            system_prompt=team.leader.system_prompt,
            model=leader_model,
            toolkit=Toolkit(),
        )
        pipe = TeamPipeline(leader=leader, members=members)

        deltas: list[str] = []
        last_reply_id: str | None = None
        reply_deltas: dict[str, list[str]] = {}
        async for evt in pipe.reply_stream(UserMsg(name="user", content=message)):
            name = type(evt).__name__
            data = evt.model_dump() if hasattr(evt, "model_dump") else {}
            if name == "TextBlockDeltaEvent":
                rid = data.get("reply_id")
                delta = data.get("delta") or ""
                if rid:
                    reply_deltas.setdefault(rid, []).append(delta)
                    last_reply_id = rid
            elif name == "ReplyEndEvent":
                last_reply_id = data.get("reply_id") or last_reply_id
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))
            elif name == "ToolCallStartEvent":
                emit(
                    EventType.TOOL_REQUESTED,
                    tool_call_id=str(data.get("name") or data.get("tool_name") or "tool"),
                    message="agentscope_tool_start",
                )

        if last_reply_id and last_reply_id in reply_deltas:
            deltas = reply_deltas[last_reply_id]
        elif reply_deltas:
            # Fall back to the chronologically last reply bucket.
            deltas = list(reply_deltas.values())[-1]
        reply_text = "".join(deltas).strip()
        structured = {
            "leader": team.leader.name,
            "members": [m.name for m in team.members],
            "tool_decisions": list(tool_state.decisions),
            "llm_calls": gateway.llm_calls,
        }
        return reply_text, structured


def _build_leader_stub_responses(team: TeamConfig, message: str) -> list[Any]:
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatResponse
    from agentscope.model._model_response import FinishedReason

    responses: list[Any] = []
    steps = team.stub_script.leader_steps
    if not steps:
        raise RuntimeConfigError("stub_script.leader_steps is required for Phase-2 stub mode")
    for step in steps:
        if "assign" in step:
            assign = step["assign"]
            prompt = str(assign.get("prompt") or "").replace("{message}", message)
            responses.append(
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=new_id("asg"),
                            name="TeamAssign",
                            input=json.dumps(
                                {"member": assign["member"], "prompt": prompt},
                                ensure_ascii=False,
                            ),
                        )
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                )
            )
        elif "final" in step:
            text = str(step["final"]).replace("{message}", message)
            responses.append(
                ChatResponse(
                    content=[TextBlock(text=text)],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                )
            )
        elif "error" in step:
            responses.append(RuntimeError(str(step["error"])))
        else:
            raise RuntimeConfigError(f"unsupported stub leader step: {step!r}")
    return responses


def _resolve_agent(platform: Platform, tenant_id: str, agent_id: str | None) -> dict:
    """Align with app.agents.nodes: unpublished / missing default → HTTP 404 semantics."""
    if agent_id:
        agent = platform.agents.get(agent_id)
        if not agent or agent["tenant_id"] != tenant_id:
            raise HTTPException(status_code=404, detail="agent not found")
        if agent.get("status") != "published":
            status = agent.get("status") or "unknown"
            raise HTTPException(
                status_code=404,
                detail=f"agent not published (status={status})",
            )
        return agent
    try:
        return platform.default_agent(tenant_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="agent not published (status=disabled)",
        ) from exc
