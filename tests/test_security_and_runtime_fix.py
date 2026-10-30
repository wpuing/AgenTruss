"""Security + runtime registry + approval state-machine regression tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.passwords import hash_password, verify_password
from app.core.platform import Platform
from app.core.security import AuthContext, issue_token, parse_token
from app.main import create_app
from app.runtime.agentscope_ext.provider import resolve_team_timeout_ms
from app.runtime.agentscope_ext.team_config import LeaderConfig, MemberConfig, TeamConfig
from app.runtime.models import RunRequest, RuntimeId, SubjectContext
from app.tools.registry import authorize_call
from tests.conftest import auth_header, login


def test_logout_revokes_token(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    assert client.get("/api/admin/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    denied = client.get("/api/admin/me", headers=headers)
    assert denied.status_code == 401


def test_ticket_args_encrypted_not_in_api(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    ticket = client.get(f"/api/admin/tickets/risk/{risk['ticket_id']}", headers=headers).json()
    assert "args_encrypted" not in ticket
    assert "args" not in ticket or ticket.get("args") is None
    assert ticket.get("args_masked")
    platform = client.app.state.platform
    stored = platform.tickets[risk["ticket_id"]]
    assert stored.get("args_encrypted")
    assert stored.get("args") is None


def test_password_hashed_not_plaintext():
    hashed = hash_password("changeme")
    assert hashed != "changeme"
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("changeme", hashed)
    assert not verify_password("wrong", hashed)


def test_login_uses_hashed_demo_password(client):
    token = login(client, "admin", "demo")
    assert token
    platform = client.app.state.platform
    user = platform.user_by_name("admin")
    assert user["password"].startswith("pbkdf2_sha256$")


def test_jwt_contains_exp_and_rejects_expired(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-key-32chars-min!!")
    settings = Settings(jwt_secret="unit-test-secret-key-32chars-min!!", jwt_expire_minutes=60)
    with mock.patch("app.core.security.get_settings", return_value=settings):
        token = issue_token(AuthContext("u1", "demo", ["tenant_admin"], {"dashboard:view"}))
        ctx = parse_token(token)
        assert ctx.user_id == "u1"
        expired = jwt.encode(
            {
                "user_id": "u1",
                "tenant_id": "demo",
                "roles": [],
                "perms": [],
                "jti": "expired-jti-1",
                "iat": datetime.now(timezone.utc) - timedelta(hours=2),
                "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            parse_token(expired)


def test_api_rejects_expired_jwt(client):
    settings = Settings()
    expired = jwt.encode(
        {
            "user_id": "x",
            "tenant_id": "demo",
            "roles": ["tenant_admin"],
            "perms": ["dashboard:view"],
            "jti": "expired-jti-api",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = client.get("/api/admin/me", headers=auth_header(expired))
    assert resp.status_code == 401
    assert "expired" in resp.json()["message"]


def test_production_rejects_weak_jwt_and_demo_users():
    weak = Settings(app_env="production", jwt_secret="changeme", allow_demo_users=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        weak.validate_runtime()
    demo = Settings(app_env="production", jwt_secret="a" * 32, allow_demo_users=True)
    with pytest.raises(RuntimeError, match="ALLOW_DEMO_USERS"):
        demo.validate_runtime()
    admin_demo = Settings(
        app_env="production",
        jwt_secret="a" * 32,
        allow_demo_users=False,
        admin_demo_mode=True,
        storage_backend="postgres",
        database_url="postgresql+psycopg://agent:strong@127.0.0.1:5432/agenttruss",
        redis_url="redis://127.0.0.1:6379/0",
        ticket_args_secret="ticket-args-secret-32chars-min!!",
    )
    with pytest.raises(RuntimeError, match="ADMIN_DEMO_MODE"):
        admin_demo.validate_runtime()
    mem = Settings(
        app_env="production",
        jwt_secret="a" * 32,
        allow_demo_users=False,
        admin_demo_mode=False,
        storage_backend="memory",
    )
    with pytest.raises(RuntimeError, match="STORAGE_BACKEND"):
        mem.validate_runtime()
    ok = Settings(
        app_env="production",
        jwt_secret="a" * 32,
        allow_demo_users=False,
        admin_demo_mode=False,
        storage_backend="postgres",
        database_url="postgresql+psycopg://agent:strong@127.0.0.1:5432/agenttruss",
        redis_url="redis://127.0.0.1:6379/0",
        postgres_password="strong-db-pass-99",
        ticket_args_secret="ticket-args-secret-32chars-min!!",
    )
    ok.validate_runtime()


def test_production_seed_skips_demo_users(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "production-grade-secret-key-32b!")
    monkeypatch.setenv("ALLOW_DEMO_USERS", "false")
    monkeypatch.setenv("POSTGRES_PASSWORD", "strong-db-pass-99")
    # Bypass create_app validate by seeding Settings directly
    with mock.patch(
        "app.core.platform.get_settings",
        return_value=Settings(
            app_env="production",
            jwt_secret="production-grade-secret-key-32b!",
            allow_demo_users=False,
        ),
    ):
        platform = Platform.seed()
    assert platform.users == {}


def test_jobs_are_tenant_scoped(client):
    admin = login(client, "admin", "demo")
    fin = login(client, "finadmin", "finance")
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=auth_header(admin),
        json={"name": "kb", "code": "kb1"},
    ).json()
    files = {"file": ("a.md", b"# hello refund policy", "text/markdown")}
    client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=auth_header(admin),
        files=files,
    )
    demo_jobs = client.get("/api/admin/kb/jobs", headers=auth_header(admin)).json()["items"]
    assert demo_jobs
    assert all(j.get("tenant_id") == "demo" for j in demo_jobs)
    other = client.get("/api/admin/kb/jobs", headers=auth_header(fin)).json()["items"]
    assert other == []


def test_required_roles_enforced_but_submitter_can_request_refund(client):
    token = login(client, "admin", "demo")
    risk = client.post(
        "/api/chat",
        headers=auth_header(token),
        json={"message": "订单申请退款"},
    ).json()
    assert risk["ticket_id"]
    assert "已退款" not in risk["reply"]
    platform = client.app.state.platform
    agent = platform.default_agent("demo")
    # Role missing → denied
    auth = AuthContext("u", "demo", ["auditor"], set())
    denied = authorize_call(platform, auth, agent, "query_order", {})
    assert denied["reason"] == "tool_denied"


def test_approval_reject_and_execute_state_machine(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    # Reject path on a fresh pending ticket
    pending = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    rejected = client.post(
        f"/api/admin/tickets/{pending['ticket_id']}/reject",
        headers=headers,
        json={"reason": "policy"},
    ).json()
    assert rejected["status"] == "rejected"
    assert rejected["decision"] == "rejected"
    assert rejected["executed"] is False

    # Approve → execute → idempotent replay (new message after reject frees the flow;
    # identical args hit idempotency against succeeded tickets only after execute).
    platform = client.app.state.platform
    agent = platform.default_agent("demo")
    auth = platform.auth_for(platform.user_by_name("admin"))
    created = authorize_call(platform, auth, agent, "refund_order", {"order_id": "unique-exec-1"})
    ticket_id = created["ticket_id"]
    approved = client.post(f"/api/admin/tickets/{ticket_id}/approve", headers=headers).json()
    assert approved["status"] == "approved"
    assert approved["executed"] is False
    assert approved["approver_id"]
    executed = client.post(f"/api/admin/tickets/{ticket_id}/execute", headers=headers).json()
    assert executed["status"] == "succeeded"
    assert executed["executed"] is True
    assert "已退款" in executed["execution_result"]
    again = client.post(f"/api/admin/tickets/{ticket_id}/execute", headers=headers).json()
    assert again["status"] == "succeeded"


def test_chat_dispatches_single_runtime_by_default(client):
    token = login(client, "admin", "demo")
    resp = client.post(
        "/api/chat",
        headers=auth_header(token),
        json={"message": "查询订单"},
    ).json()
    assert resp["runtime_id"] == "single"
    assert resp["trace_id"]
    assert resp["run_id"]
    assert "owner=" in resp["reply"]


def test_chat_can_switch_to_agentscope_runtime(monkeypatch):
    pytest.importorskip("agentscope")
    monkeypatch.setenv("AGENT_RUNTIME", "agentscope_team")
    monkeypatch.setenv("AGENTSCOPE_ENABLED", "true")
    monkeypatch.setenv("AGENTSCOPE_MODEL_MODE", "stub")
    app = create_app()
    with TestClient(app) as client:
        token = login(client, "admin", "demo")
        resp = client.post(
            "/api/chat",
            headers=auth_header(token),
            json={"message": "订单物流怎么查"},
        ).json()
        assert resp["runtime_id"] == "agentscope_team"
        assert resp["status"] in {"succeeded", "blocked", "approval_required"}
        assert resp["trace_id"]
        assert resp["audit_id"]


def test_agentscope_timeout_env_overrides_team_yaml(monkeypatch):
    monkeypatch.setenv("AGENTSCOPE_TIMEOUT_MS", "1234")
    settings = Settings(agentscope_timeout_ms=1234)
    team = TeamConfig(
        timeout_ms=99_000,
        leader=LeaderConfig(name="L", system_prompt="x"),
        members=[MemberConfig(name="M", description="d", system_prompt="s")],
    )
    req = RunRequest(
        message="hi",
        subject=SubjectContext(user_id="u", tenant_id="demo"),
        runtime_id=RuntimeId.AGENTSCOPE_TEAM,
    )
    assert resolve_team_timeout_ms(req, settings, team) == 1234
    req2 = req.model_copy(update={"timeout_ms": 500})
    assert resolve_team_timeout_ms(req2, settings, team) == 500


def test_live_mode_without_credentials_fails_clearly(monkeypatch):
    pytest.importorskip("agentscope")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.runtime.agentscope_team import AgentscopeTeamRuntime
    from app.runtime.models import RunStatus

    platform = Platform.seed()
    user = platform.user_by_name("admin")
    settings = Settings(
        agent_runtime="agentscope_team",
        agentscope_enabled=True,
        agentscope_model_mode="live",
    )
    runtime = AgentscopeTeamRuntime(settings=settings)
    result = runtime.execute(
        platform,
        RunRequest(
            message="订单物流怎么查",
            subject=SubjectContext(
                user_id=user["id"],
                tenant_id=user["tenant_id"],
                roles=list(user["roles"]),
                perms=sorted(user["perms"]),
            ),
            runtime_id=RuntimeId.AGENTSCOPE_TEAM,
        ),
    )
    assert result.status == RunStatus.FAILED
    assert result.error_code == "model_not_configured"
    # Must not pretend a real live model call succeeded.
    assert result.status != RunStatus.SUCCEEDED

