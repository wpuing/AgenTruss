"""Phase5.28b: channel save≠connect; authz gaps for components/IAM."""

from __future__ import annotations

from tests.conftest import auth_header, login


def test_channel_wecom_save_never_claims_connected(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    saved = client.put(
        "/api/admin/channels/wecom",
        headers=headers,
        json={
            "corp_id": "ww_demo",
            "agent_id": "1000001",
            "secret": "super-secret-wecom",
            "enabled": True,
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body.get("saved") is True
    assert body.get("connected") is False
    assert body.get("mode") == "config_saved_not_connected"
    assert "super-secret" not in saved.text

    # Empty secret keeps previous
    again = client.put(
        "/api/admin/channels/wecom",
        headers=headers,
        json={"corp_id": "ww_demo2", "secret": ""},
    )
    assert again.status_code == 200
    detail = client.get("/api/admin/channels/wecom", headers=headers).json()
    assert detail["fields"]["corp_id"] == "ww_demo2"
    assert detail["fields"]["secret"]["configured"] is True

    probe = client.post("/api/admin/channels/wecom/test-connection", headers=headers)
    assert probe.status_code == 200
    pdata = probe.json()
    assert pdata["ok"] is False
    assert pdata["status"] == "protocol_not_integrated"


def test_auditor_forbidden_component_and_iam_writes(client):
    admin = login(client, "admin", "demo")
    admin_h = auth_header(admin)
    created = client.post(
        "/api/admin/users",
        headers=admin_h,
        json={"username": "aud2", "password": "password123", "roles": ["auditor"]},
    )
    assert created.status_code == 200, created.text

    aud_login = client.post(
        "/api/auth/login",
        json={"username": "aud2", "password": "password123", "tenant_code": "demo"},
    )
    assert aud_login.status_code == 200
    aud_h = auth_header(aud_login.json()["access_token"])

    assert client.put(
        "/api/admin/components/embedding",
        headers=aud_h,
        json={"embedding_provider": "mock"},
    ).status_code == 403
    assert client.post(
        "/api/admin/users",
        headers=aud_h,
        json={"username": "x", "password": "password123", "roles": ["auditor"]},
    ).status_code == 403
    assert client.put(
        "/api/admin/channels/wecom",
        headers=aud_h,
        json={"corp_id": "x"},
    ).status_code == 403


def test_component_secret_not_echoed_in_audit_detail(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    client.put(
        "/api/admin/components/llm",
        headers=headers,
        json={"agentscope_model_mode": "stub", "openai_api_key": "sk-should-not-leak"},
    )
    # Memory audits on platform
    platform = client.app.state.platform
    leaked = any("sk-should-not-leak" in str(a) for a in platform.audits[-5:])
    assert leaked is False
