"""APIs backing formerly-placeholder Admin console pages."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import auth_header, login


def test_catalog_and_console_apis(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    assert client.get("/api/admin/industries", headers=headers).status_code == 200
    assert client.get("/api/admin/tenants", headers=headers).json()["items"]
    assert client.get("/api/admin/roles", headers=headers).json()["items"]
    assert client.get("/api/admin/permissions", headers=headers).json()["items"]
    assert client.get("/api/admin/channels", headers=headers).json()["items"]
    agent_id = client.get("/api/admin/agents", headers=headers).json()["items"][0]["agent_id"]
    cfg = client.get(f"/api/admin/agents/{agent_id}/industry-config", headers=headers).json()
    assert cfg["topics"] and cfg["prompt_full"]
    fb = client.post(
        "/api/admin/kb/feedback",
        headers=headers,
        json={"query": "退货", "rating": "up", "comment": "ok"},
    )
    assert fb.status_code == 200
    assert client.get("/api/admin/kb/feedback", headers=headers).json()["items"]
    hum = client.post(
        "/api/admin/tickets/human",
        headers=headers,
        json={"note": "跟进用户", "reason": "manual"},
    ).json()
    assert hum["type"] == "human"
    assert client.get("/api/admin/tickets/human", headers=headers).json()["items"]
    assert (
        client.post(
            f"/api/admin/tickets/human/{hum['id']}/resolve",
            headers=headers,
            json={"note": "done"},
        ).json()["status"]
        == "succeeded"
    )
    strategies = client.get("/api/admin/chunk-strategies", headers=headers).json()["items"]
    sid = strategies[0]["id"]
    patched = client.patch(
        f"/api/admin/chunk-strategies/{sid}",
        headers=headers,
        json={"chunk_size": 99},
    ).json()
    assert patched["chunk_size"] == 99
    chat = client.post("/api/chat", headers=headers, json={"message": "今天天气怎么样"}).json()
    assert chat.get("block_reason") == "off_topic"
    assert chat.get("need_human") is True
    aud = client.get("/api/admin/audit-logs", headers=headers).json()["items"][-1]
    detail = client.get(f"/api/admin/audit-logs/{aud['audit_id']}", headers=headers)
    assert detail.status_code == 200
    if aud.get("session_id"):
        trace = client.get(f"/api/admin/trace/{aud['session_id']}", headers=headers)
        assert trace.status_code == 200
        assert "turns" in trace.json()


def test_spa_has_no_placeholder_handlers():
    root = Path("app/static/admin")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    index = (root / "index.html").read_text(encoding="utf-8")
    # Explicit placeholder route bindings should be gone (fallback handler may remain).
    for path in [
        '"/kb/feedback": pagePlaceholder',
        '"/agent/wizard": pagePlaceholder',
        '"/agent/templates": pagePlaceholder',
        '"/agent/config/basic": pagePlaceholder',
        '"/agent/config/topics": pagePlaceholder',
        '"/agent/config/prompts": pagePlaceholder',
        '"/agent/config/guardrails": pagePlaceholder',
        '"/agent/config/publish": pagePlaceholder',
        '"/iam/roles": pagePlaceholder',
        '"/audit/trace": pagePlaceholder',
        '"/system/channel/web": pagePlaceholder',
    ]:
        assert path not in app_js
    # Domain pages live in pages-ext-*.js; console-pages.js is a thin shim.
    agent_ro = (root / "pages-ext-agent-readonly.js").read_text(encoding="utf-8")
    assert "pageAgentConfigBasic" in agent_ro
    assert '"/agent/config/basic": pageAgentConfigBasic' in agent_ro
    assert "pages-ext-agent-readonly.js" in index
    assert "admin-kit.js" in index
    assert "AgenTrussPageModules" in app_js or "registerPages" in (root / "admin-kit.js").read_text(
        encoding="utf-8"
    )
    shim = (root / "console-pages.js").read_text(encoding="utf-8")
    assert "console-pages.js" in index
    assert "AgenTrussRegisterPages" in shim
