"""Live chat-model factory for AgentScope 2.0.10 (public DashScope/OpenAI APIs).

Verified against docs.agentscope.io 2.0.10 quickstart / LLM pages:

```python
from agentscope.model import DashScopeChatModel
from agentscope.credential import DashScopeCredential
model = DashScopeChatModel(
    credential=DashScopeCredential(api_key=os.environ["DASHSCOPE_API_KEY"]),
    model="qwen-plus",
)
```

API keys are read only from process environment — never from YAML or logs.
"""

from __future__ import annotations

import os
from typing import Any

from app.runtime.exceptions import RuntimeConfigError


class ModelNotConfiguredError(RuntimeConfigError):
    """Raised when live mode is selected but no credential is available."""


class ModelCallFailedError(RuntimeError):
    """Raised when the upstream provider rejects or fails a call."""


def resolve_api_key(provider: str) -> str:
    """Return API key from env for the provider. Never log the value."""
    provider = (provider or "dashscope").strip().lower()
    if provider == "dashscope":
        key = os.environ.get("DASHSCOPE_API_KEY") or ""
    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY") or ""
    else:
        raise RuntimeConfigError(f"unsupported model provider: {provider}")
    if not key.strip():
        raise ModelNotConfiguredError(
            f"live model requires env credential for provider={provider}"
        )
    return key.strip()


def build_live_chat_model(
    *,
    provider: str,
    model_name: str,
    gateway: Any,
    stream: bool = False,
):
    """Construct a real AgentScope chat model via public constructors.

    Call budget is enforced by wrapping the public async ``__call__`` if present,
    otherwise by registering the model name on the gateway for observability.
    """
    from agentscope.credential import DashScopeCredential, OpenAICredential
    from agentscope.model import DashScopeChatModel, OpenAIChatModel

    provider = (provider or "dashscope").strip().lower()
    api_key = resolve_api_key(provider)
    gateway.allowed_models.add(model_name)

    # AgentScope credential constructors accept str at runtime; stubs may disagree.
    model: Any
    if provider == "dashscope":
        model = DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),  # type: ignore[arg-type]
            model=model_name,
            stream=stream,
        )
    elif provider == "openai":
        model = OpenAIChatModel(
            credential=OpenAICredential(api_key=api_key),  # type: ignore[arg-type]
            model=model_name,
            stream=stream,
        )
    else:
        raise RuntimeConfigError(f"unsupported model provider: {provider}")

    model.test_mode = False  # type: ignore[attr-defined]
    model.live_claimed = True  # type: ignore[attr-defined]
    _install_budget_hook(model, gateway, model_name)
    return model


def _install_budget_hook(model: Any, gateway: Any, model_name: str) -> None:
    """Count LLM calls without importing private agentscope modules."""
    if not callable(model):
        return
    original = model.__call__

    async def _budgeted(*args: Any, **kwargs: Any):
        gateway.before_call(model_name)
        try:
            return await original(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            from app.security.redaction import sanitize_exception

            raise ModelCallFailedError(sanitize_exception(exc, max_len=300)) from exc

    model.__call__ = _budgeted  # type: ignore[method-assign]


def build_test_live_stub(
    responses: list[Any],
    gateway: Any,
    *,
    model_name: str = "test-live-stub",
    mark_test_mode: bool = True,
):
    """Deterministic stand-in when tests exercise the live path without credentials.

    Explicitly marked ``test_mode=True`` — never report as a real model success.
    """
    from app.runtime.agentscope_ext.gateway_model import build_stub_chat_model

    model = build_stub_chat_model(responses, gateway, model_name=model_name)
    model.test_mode = mark_test_mode  # type: ignore[attr-defined]
    model.live_claimed = False  # type: ignore[attr-defined]
    gateway.allowed_models.add(model_name)
    return model
