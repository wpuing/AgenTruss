"""Backend fields aligned with Admin SPA (chat_callable, rate-limit readonly, overview)."""

from __future__ import annotations

from tests.conftest import auth_header, login


def test_agents_list_exposes_chat_callable_and_for_chat_filter(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    payload = client.get("/api/admin/agents", headers=headers).json()
    assert payload["items"]
    assert "counts" in payload
    assert payload["counts"]["chat_callable"] >= 1
    row = payload["items"][0]
    assert "chat_callable" in row
    assert row["chat_callable"] is (row["status"] == "published")

    agent_id = row["agent_id"]
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "disabled"})
    all_rows = client.get("/api/admin/agents", headers=headers).json()
    disabled = next(a for a in all_rows["items"] if a["agent_id"] == agent_id)
    assert disabled["chat_callable"] is False
    assert all_rows["counts"]["disabled"] >= 1

    for_chat = client.get("/api/admin/agents?for_chat=true", headers=headers).json()
    assert all(a["status"] == "published" and a["chat_callable"] for a in for_chat["items"])
    assert agent_id not in {a["agent_id"] for a in for_chat["items"]}

    detail = client.get(f"/api/admin/agents/{agent_id}", headers=headers).json()
    assert detail["chat_callable"] is False
    assert detail["status"] == "disabled"

    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "published"})


def test_system_basic_exposes_readonly_rate_limits(client):
    token = login(client, "admin", "demo")
    basic = client.get("/api/admin/system/basic", headers=auth_header(token)).json()
    assert basic["model.api_key"] == "********"
    assert isinstance(basic["rate_limit_per_minute"], int)
    assert isinstance(basic["login_rate_limit_per_minute"], int)
    assert basic["rate_limit_writable"] is False
    assert "只读" in basic.get("note", "") or "未开放" in basic.get("note", "")


def test_overview_agents_published_disabled_counts(client):
    token = login(client, "admin", "demo")
    overview = client.get("/api/admin/overview", headers=auth_header(token)).json()
    agents = overview["agents"]
    assert agents["total"] >= 1
    assert agents["published"] == agents["chat_callable"]
    assert "disabled" in agents
    assert agents["published"] + agents["disabled"] + agents["other"] == agents["total"]


def test_chat_without_agent_id_404_when_default_disabled(client):
    """Omitting agent_id must not swallow KeyError into HTTP 200 FAILED."""
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    agent_id = client.get("/api/admin/agents", headers=headers).json()["items"][0]["agent_id"]
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "disabled"})
    resp = client.post("/api/chat", headers=headers, json={"message": "查询订单"})
    assert resp.status_code == 404
    msg = str(resp.json().get("message") or resp.json().get("detail") or "")
    assert "not published" in msg
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "published"})


def test_workflows_agents_expose_chat_callable(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    data = client.get("/api/admin/workflows", headers=headers).json()
    assert data.get("agents")
    for row in data["agents"]:
        assert "chat_callable" in row
        assert row["chat_callable"] is (row["status"] == "published")
    agent_id = data["agents"][0]["agent_id"]
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "disabled"})
    again = client.get("/api/admin/workflows", headers=headers).json()
    disabled = next(a for a in again["agents"] if a["agent_id"] == agent_id)
    assert disabled["chat_callable"] is False
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "published"})
