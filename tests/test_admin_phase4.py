"""Phase4 admin overview / executions / components / workflows / demo isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _client(monkeypatch, **env) -> TestClient:
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return TestClient(create_app())


def _login(client: TestClient) -> dict:
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "changeme", "tenant_code": "demo"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_overview_and_components_have_meta_no_secrets(monkeypatch):
    client = _client(
        monkeypatch,
        ADMIN_DEMO_MODE="false",
        STORAGE_BACKEND="memory",
        ALLOW_DEMO_USERS="true",
    )
    headers = _login(client)
    ov = client.get("/api/admin/overview", headers=headers)
    assert ov.status_code == 200
    body = ov.json()
    assert "_meta" in body
    assert body["_meta"]["is_demo"] is False
    assert body["tickets"].get("success_rate") is None
    assert "changeme" not in str(body).lower()

    comps = client.get("/api/admin/components", headers=headers)
    assert comps.status_code == 200
    items = comps.json()["items"]
    assert any(c["id"] == "vector" for c in items)
    assert "jwt_secret" not in str(comps.json()).lower()


def test_executions_aggregate_and_agent_detail(monkeypatch):
    client = _client(
        monkeypatch,
        ADMIN_DEMO_MODE="false",
        STORAGE_BACKEND="memory",
        ALLOW_DEMO_USERS="true",
    )
    headers = _login(client)
    client.post("/api/chat", headers=headers, json={"message": "查询订单", "session_id": None})
    ex = client.get("/api/admin/executions", headers=headers)
    assert ex.status_code == 200
    items = ex.json()["items"]
    assert isinstance(items, list)
    assert all(not row.get("is_demo") for row in items if row.get("kind") != "demo")

    agents = client.get("/api/admin/agents", headers=headers).json()["items"]
    aid = agents[0]["agent_id"]
    detail = client.get(f"/api/admin/agents/{aid}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["agent_id"] == aid
    assert "api_key" not in detail.json()


def test_demo_workflow_requires_admin_demo_mode(monkeypatch):
    client = _client(
        monkeypatch,
        ADMIN_DEMO_MODE="false",
        STORAGE_BACKEND="memory",
        ALLOW_DEMO_USERS="true",
    )
    headers = _login(client)
    denied = client.put(
        "/api/admin/workflows/demo_refund_flow/demo-draft",
        headers=headers,
        json={"nodes": [], "edges": []},
    )
    assert denied.status_code == 403

    client2 = _client(
        monkeypatch,
        ADMIN_DEMO_MODE="true",
        STORAGE_BACKEND="memory",
        ALLOW_DEMO_USERS="true",
    )
    headers2 = _login(client2)
    ok = client2.put(
        "/api/admin/workflows/demo_refund_flow/demo-draft",
        headers=headers2,
        json={
            "name": "演示草稿",
            "nodes": [{"id": "a", "type": "start", "title": "A"}],
            "edges": [],
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("_meta", {}).get("is_demo") is True
    assert ok.json().get("demo_only") is True or ok.json().get("status") == "demo_draft"

    # Live API failure must not be replaced by demo success: bad token → 401
    bad = client2.get("/api/admin/overview", headers={"Authorization": "Bearer invalid"})
    assert bad.status_code == 401
