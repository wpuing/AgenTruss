"""LangGraph workflow path smoke tests (memory checkpointer)."""

from __future__ import annotations

import pytest

from app.agents.graph import run_langgraph_chat
from app.core.config import Settings
from app.core.platform import Platform
from tests.conftest import auth_header, login


def test_langgraph_chat_via_api(client, monkeypatch):
    pytest.importorskip("langgraph")
    monkeypatch.setenv("WORKFLOW_ENGINE", "langgraph")
    token = login(client, "admin", "demo")
    resp = client.post(
        "/api/chat",
        headers=auth_header(token),
        json={"message": "查询订单"},
    ).json()
    assert resp["runtime_id"] == "single"
    assert "owner=" in resp["reply"]
    # When langgraph runs, workflow_engine may appear on raw path; API maps RunResult.
    assert resp.get("trace_id")


def test_langgraph_direct_invoke(monkeypatch):
    pytest.importorskip("langgraph")
    monkeypatch.setenv("WORKFLOW_ENGINE", "langgraph")
    from unittest import mock

    with mock.patch(
        "app.agents.graph.get_settings",
        return_value=Settings(workflow_engine="langgraph", storage_backend="memory"),
    ):
        platform = Platform.seed()
        user = platform.user_by_name("admin")
        auth = platform.auth_for(user)
        out = run_langgraph_chat(platform, auth, {"message": "查询订单"})
        assert out.get("workflow_engine") == "langgraph"
        assert "owner=" in out["reply"]
        assert out.get("workflow_thread_id", "").startswith("demo:")
