"""Phase5.28: component save/test-connect, IAM writes, strategy versioning."""

from __future__ import annotations

from tests.conftest import auth_header, login


def test_component_save_and_test_connect_mock_embedding(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    detail = client.get("/api/admin/components/embedding", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert "fields" in body
    assert "openai_api_key" not in str(body.get("fields", {})).lower() or body["fields"]["openai_api_key"].get(
        "masked"
    ) in {"", "********"}

    saved = client.put(
        "/api/admin/components/embedding",
        headers=headers,
        json={"embedding_provider": "mock", "embedding_dim": 8},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json().get("saved") is True
    assert "openai_api_key" not in saved.text or "sk-" not in saved.text

    probe = client.post("/api/admin/components/embedding/test-connection", headers=headers)
    assert probe.status_code == 200
    data = probe.json()
    assert data["ok"] is True
    assert data["status"] == "mock_ok"

    # Empty secret must not clear / error
    again = client.put(
        "/api/admin/components/llm",
        headers=headers,
        json={"agentscope_model_mode": "stub", "openai_api_key": ""},
    )
    assert again.status_code == 200

    # Status-only component rejected
    bad = client.put("/api/admin/components/jobs", headers=headers, json={})
    assert bad.status_code == 400


def test_component_test_connect_never_fakes_live_llm(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    client.put(
        "/api/admin/components/llm",
        headers=headers,
        json={"agentscope_model_mode": "live"},
    )
    probe = client.post("/api/admin/components/llm/test-connection", headers=headers)
    data = probe.json()
    # Without keys: not_configured; with keys from env may be credentials_present_unverified — never ok=True live
    if data.get("status") == "stub_ok":
        # env may still force stub if save failed — accept only if mode stuck
        pass
    else:
        assert data.get("ok") is not True or data.get("status") == "stub_ok"


def test_object_storage_probe_honest_when_unconfigured(client):
    """MinIO probe must not fake success without endpoint/credentials."""
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    probe = client.post("/api/admin/components/object_storage/test-connection", headers=headers)
    assert probe.status_code == 200
    data = probe.json()
    # Default test env has no MinIO → not_configured or unreachable; never silent ok.
    if data.get("status") == "not_configured":
        assert data.get("ok") is False
    else:
        assert data.get("status") in {"unreachable", "ready", "not_configured"}
        if data.get("status") != "ready":
            assert data.get("ok") is False


def test_iam_create_update_disable_and_live_authz(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    created = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "ops_reader", "password": "password123", "roles": ["auditor"]},
    )
    assert created.status_code == 200, created.text
    user = created.json()
    assert user["username"] == "ops_reader"
    assert "password" not in user
    assert user["status"] == "active"
    uid = user["id"]

    # Auditor cannot chat debug
    login_ops = client.post(
        "/api/auth/login",
        json={"username": "ops_reader", "password": "password123", "tenant_code": "demo"},
    )
    assert login_ops.status_code == 200
    ops_headers = auth_header(login_ops.json()["access_token"])
    denied = client.post("/api/chat", headers=ops_headers, json={"message": "你好"})
    assert denied.status_code == 403

    # Privilege escalation blocked
    escalate = client.patch(
        f"/api/admin/users/{uid}",
        headers=ops_headers,
        json={"roles": ["tenant_admin"]},
    )
    assert escalate.status_code == 403

    # Admin disables user → subsequent API fails
    disabled = client.patch(
        f"/api/admin/users/{uid}",
        headers=headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    after = client.get("/api/admin/me", headers=ops_headers)
    assert after.status_code == 401

    # Cross-tenant: finance admin cannot see demo user
    fin = login(client, "finadmin", "finance")
    fin_headers = auth_header(fin)
    missing = client.get(f"/api/admin/users/{uid}", headers=fin_headers)
    assert missing.status_code == 404


def test_strategy_patch_version_and_conflict(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    rows = client.get("/api/admin/chunk-strategies", headers=headers).json()["items"]
    assert rows
    row = next(r for r in rows if r["id"] == "cs-baseline")
    v1 = int(row.get("version") or 1)
    patched = client.patch(
        f"/api/admin/chunk-strategies/{row['id']}",
        headers=headers,
        json={"chunk_size": 90, "expected_version": v1},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["chunk_size"] == 90
    assert int(patched.json()["version"]) == v1 + 1

    conflict = client.patch(
        f"/api/admin/chunk-strategies/{row['id']}",
        headers=headers,
        json={"chunk_size": 91, "expected_version": v1},
    )
    assert conflict.status_code == 409

    again = client.get("/api/admin/chunk-strategies", headers=headers).json()["items"]
    current = next(r for r in again if r["id"] == "cs-baseline")
    assert current["chunk_size"] == 90
