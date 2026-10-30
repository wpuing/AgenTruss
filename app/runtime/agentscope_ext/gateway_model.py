"""Platform model gateway wrapping AgentScope ChatModelBase (2.0.10).

All LLM calls from TeamPipeline members/leader go through this class so the
platform can enforce call budgets and keep a deterministic stub for tests.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel, Field

from app.runtime.exceptions import RuntimeConfigError


class PlatformModelGateway:
    """Process-local counters / allow-list for model calls."""

    def __init__(self, *, max_llm_calls: int = 20, allowed_models: list[str] | None = None) -> None:
        self.max_llm_calls = max_llm_calls
        self.allowed_models = set(allowed_models or ["gateway-stub"])
        self.llm_calls = 0

    def before_call(self, model_name: str) -> None:
        if model_name not in self.allowed_models:
            raise RuntimeConfigError(f"model not allowed: {model_name}")
        if self.llm_calls >= self.max_llm_calls:
            raise RuntimeError("model gateway budget exceeded")
        self.llm_calls += 1


def build_stub_chat_model(responses: list[Any], gateway: PlatformModelGateway, model_name: str = "gateway-stub"):
    """Create a ChatModelBase subclass instance with a fixed response queue."""
    from agentscope.credential import CredentialBase
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.model._model_response import FinishedReason
    from agentscope.message import TextBlock, Msg

    class _GatewayCredential(CredentialBase):
        name: str = "agentruss-gateway"

        @classmethod
        def get_chat_model_class(cls):  # type: ignore[override]
            return _GatewayChatModel

    class _Params(BaseModel):
        temperature: float = Field(default=0.0)

    class _GatewayChatModel(ChatModelBase):
        class Parameters(BaseModel):
            temperature: float = 0.0

        def __init__(self) -> None:
            super().__init__(
                credential=_GatewayCredential(),
                model=model_name,
                parameters=_GatewayChatModel.Parameters(),
                stream=False,
                context_size=8192,
            )
            self.formatter = OpenAIChatFormatter()
            self._queue = list(responses)
            self.gateway = gateway

        async def _call_api(
            self,
            model_name: str,
            messages: list[Msg],
            tools: list[dict] | None = None,
            tool_choice: Any = None,
            **kwargs: Any,
        ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
            del messages, tools, tool_choice, kwargs
            self.gateway.before_call(model_name)
            if not self._queue:
                return ChatResponse(
                    content=[TextBlock(text="")],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                )
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    return _GatewayChatModel()
