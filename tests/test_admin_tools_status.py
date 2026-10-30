"""Admin tools catalog and system status (no secrets)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def _login(client: TestClient) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "changeme", "tenant_code": "demo"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_tools_catalog_and_system_status_safe():
    client = TestClient(create_app())
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    tools = client.get("/api/admin/tools", headers=headers)
    assert tools.status_code == 200
    items = tools.json()["items"]
    names = {row["name"] for row in items}
    assert "query_order" in names
    assert "refund_order" in names
    assert all("handler" not in row for row in items)
    assert any(row["name"] == "refund_order" and row["high_risk"] for row in items)

    status = client.get("/api/admin/system/status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["health"]["status"] == "ok"
    assert "ready" in body
    assert "embedding_provider" in body
    assert "jwt_secret" not in body
    assert "ticket_args_secret" not in body
    assert "password" not in body
    assert "changeme" not in str(body).lower()
