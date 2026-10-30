"""Smoke: domain routers remain mounted after http.py split."""

from __future__ import annotations

from tests.conftest import login


def test_ops_and_runtime_routes_still_mounted(client):
    token = login(client, "admin", "demo")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/admin/overview", headers=headers).status_code == 200
    assert client.get("/api/admin/executions", headers=headers).status_code == 200
    assert client.get("/api/admin/system/status", headers=headers).status_code == 200
    assert client.get("/api/admin/agents", headers=headers).status_code == 200
    assert client.get("/api/admin/tools", headers=headers).status_code == 200
    assert client.get("/api/admin/sessions", headers=headers).status_code == 200
    chat = client.post("/api/chat", headers=headers, json={"message": "ping"})
    assert chat.status_code == 200
    ready = client.get("/ready")
    assert ready.status_code in {200, 503}
    assert "checks" in ready.json()
