"""Bridge AgentScope Toolkit tools to AgenTruss ``authorize_call``."""

from __future__ import annotations

from typing import Any, Callable

from app.core.platform import Platform
from app.core.security import AuthContext
from app.runtime.events import EventType
from app.tools.registry import authorize_call, get_tool


class ToolGatewayState:
    """Collects tool decisions for mapping into RunResult."""

    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []
        self.last_ticket_id: str | None = None
        self.denied: bool = False
        self.approval_required: bool = False


def build_member_toolkit(
    *,
    platform: Platform,
    auth: AuthContext,
    agent: dict,
    tool_names: list[str],
    state: ToolGatewayState,
    emit: Callable[..., None],
):
    """Build an AgentScope Toolkit whose tools always hit the platform gateway."""
    from agentscope.message import TextBlock
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import FunctionTool, Toolkit
    from agentscope.tool._response import ToolChunk

    # AgentScope permission ASK would park the agent; platform authorize_call is
    # the authoritative gate, so mark gateway tools as ALLOW at AS layer.
    allow = PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="Delegated to AgenTruss tool gateway",
    )

    tools: list[FunctionTool] = []
    for name in tool_names:
        meta = get_tool(name)
        if meta is None:
            # Illegal tool in config — still register a denier so the agent cannot bypass.
            def _denied_factory(tool_name: str = name):
                def _denied(**kwargs: Any) -> ToolChunk:
                    del kwargs
                    decision = {
                        "ok": False,
                        "reason": "tool_denied",
                        "reply": f"工具未注册: {tool_name}",
                    }
                    state.decisions.append(decision)
                    state.denied = True
                    emit(
                        EventType.TOOL_FAILED,
                        tool_call_id=tool_name,
                        message=decision["reply"],
                        payload={"reason": "tool_denied"},
                    )
                    return ToolChunk(
                        content=[TextBlock(text=str(decision["reply"]))],
                        is_last=True,
                    )

                return _denied

            tools.append(
                FunctionTool(
                    _denied_factory(),
                    name=name,
                    description=f"Blocked unregistered tool {name}",
                    permission=allow,
                )
            )
            continue

        def _factory(tool_name: str = name, description: str = meta["description"]):
            def _call(**kwargs: Any) -> ToolChunk:
                emit(
                    EventType.TOOL_REQUESTED,
                    tool_call_id=tool_name,
                    message=tool_name,
                    payload={"args_keys": sorted(kwargs.keys())},
                )
                decision = authorize_call(platform, auth, agent, tool_name, dict(kwargs))
                state.decisions.append(decision)
                if decision.get("ticket_id"):
                    state.last_ticket_id = decision["ticket_id"]
                if decision.get("reason") == "approval_required":
                    state.approval_required = True
                    emit(
                        EventType.APPROVAL_REQUESTED,
                        tool_call_id=tool_name,
                        message=decision.get("reply"),
                        payload={"ticket_id": decision.get("ticket_id")},
                    )
                elif not decision.get("ok"):
                    state.denied = True
                    emit(
                        EventType.TOOL_FAILED,
                        tool_call_id=tool_name,
                        message=decision.get("reply"),
                        payload={"reason": decision.get("reason")},
                    )
                else:
                    emit(
                        EventType.TOOL_COMPLETED,
                        tool_call_id=tool_name,
                        message=decision.get("reply", "")[:120],
                    )
                text = str(decision.get("reply") or decision.get("reason") or "tool error")
                return ToolChunk(content=[TextBlock(text=text)], is_last=True)

            _call.__doc__ = description
            return FunctionTool(
                _call,
                name=tool_name,
                description=description,
                permission=allow,
            )

        tools.append(_factory())

    # Toolkit stub typing may not accept FunctionTool list; runtime accepts it.
    return Toolkit(tools=tools)  # type: ignore[arg-type]
