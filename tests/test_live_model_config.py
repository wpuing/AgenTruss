"""Live model config errors without calling providers (no API key required)."""

from __future__ import annotations

import pytest
from app.runtime.agentscope_ext.live_model import (
    ModelNotConfiguredError,
    build_test_live_stub,
    resolve_api_key,
)
from app.runtime.exceptions import RuntimeConfigError


def test_resolve_api_key_missing_dashscope(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ModelNotConfiguredError):
        resolve_api_key("dashscope")


def test_resolve_api_key_missing_openai(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelNotConfiguredError):
        resolve_api_key("openai")


def test_resolve_api_key_unsupported() -> None:
    with pytest.raises(RuntimeConfigError):
        resolve_api_key("not-a-provider")


def test_test_live_stub_is_not_live_claimed() -> None:
    """Offline stand-in must not be reported as a successful Live provider call."""
    pytest.importorskip("agentscope")

    class _Gw:
        allowed_models: set[str] = set()

    model = build_test_live_stub(["ok"], _Gw())
    assert getattr(model, "live_claimed", None) is False
    assert getattr(model, "test_mode", None) is True
