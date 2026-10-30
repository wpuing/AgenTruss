"""Security audit regression suite — fictional data only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.config import Settings
from app.core.files import sanitize_filename
from app.core.passwords import hash_password, verify_password
from app.tools import tickets as ticket_svc
from app.tools.registry import authorize_call, get_tool
from tests.conftest import auth_header, login


def test_unauthenticated_api_rejected(client):
    assert client.get("/api/admin/me").status_code == 401
    assert client.get("/api/admin/audit-logs").status_code == 401
    assert client.post("/api/chat", json={"message": "查询订单"}).status_code == 401


def test_auditor_cannot_approve_or_execute(client):
    admin = login(client, "admin", "demo")
    auditor = login(client, "auditor", "demo")
    risk = client.post(
        "/api/chat",
        headers=auth_header(admin),
        json={"message": "订单申请退款"},
    ).json()
    tid = risk["ticket_id"]
    assert client.post(
        f"/api/admin/tickets/{tid}/approve",
        headers=auth_header(auditor),
    ).status_code == 403
    client.post(f"/api/admin/tickets/{tid}/approve", headers=auth_header(admin))
    assert client.post(
        f"/api/admin/tickets/{tid}/execute",
        headers=auth_header(auditor),
    ).status_code == 403


def test_cross_tenant_ticket_and_session_denied(client):
    admin = login(client, "admin", "demo")
    other = login(client, "finadmin", "finance")
    risk = client.post(
        "/api/chat",
        headers=auth_header(admin),
        json={"message": "订单申请退款"},
    ).json()
    tid = risk["ticket_id"]
    sid = risk["session_id"]
    assert client.get(
        f"/api/admin/tickets/risk/{tid}",
        headers=auth_header(other),
    ).status_code == 404
    assert client.post(
        f"/api/admin/tickets/{tid}/approve",
        headers=auth_header(other),
    ).status_code == 404
    assert client.get(
        f"/api/admin/sessions/{sid}",
        headers=auth_header(other),
    ).status_code == 404


def test_forged_body_identity_ignored(client):
    token = login(client, "admin", "demo")
    resp = client.post(
        "/api/chat",
        headers=auth_header(token),
        json={
            "message": "查询订单",
            "user_id": "attacker",
            "tenant_id": "finance",
        },
    ).json()
    assert resp.get("guardrail_hit") is False or resp.get("reply")
    audits = client.get("/api/admin/audit-logs", headers=auth_header(token)).json()["items"]
    assert audits[-1]["tenant_id"] == "demo"
    assert "attacker" not in str(audits[-1].get("user_id"))


def test_revoked_jwt_rejected(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/admin/me", headers=headers).status_code == 401


def test_unregistered_tool_denied(client):
    token = login(client, "admin", "demo")
    platform = client.app.state.platform
    user = platform.user_by_name("admin")
    auth = platform.auth_for(user)
    agent = platform.default_agent("demo")
    decision = authorize_call(platform, auth, agent, "drop_database", {})
    assert decision["ok"] is False
    assert decision["reason"] == "tool_denied"


def test_pending_ticket_cannot_execute(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    denied = client.post(f"/api/admin/tickets/{risk['ticket_id']}/execute", headers=headers)
    assert denied.status_code == 409


def test_approve_does_not_accept_arg_override(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    tid = risk["ticket_id"]
    before = client.app.state.platform.tickets[tid]["args_encrypted"]
    client.post(
        f"/api/admin/tickets/{tid}/approve",
        headers=headers,
        json={"reason": "ok", "execute": False, "args": {"order_id": "HACK"}},
    )
    after = client.app.state.platform.tickets[tid]["args_encrypted"]
    assert before == after


def test_concurrent_execute_single_side_effect(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    tid = risk["ticket_id"]
    client.post(f"/api/admin/tickets/{tid}/approve", headers=headers)

    platform = client.app.state.platform
    auth = platform.auth_for(platform.user_by_name("admin"))
    tool = get_tool("refund_order")
    assert tool is not None
    calls = {"n": 0}
    original = tool["handler"]

    def counting(args, ctx):
        calls["n"] += 1
        return original(args, ctx)

    tool["handler"] = counting
    try:
        def _exec(_):
            try:
                ticket_svc.execute_ticket(platform, auth, tid)
                return "ok"
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(_exec, range(8)))
        assert calls["n"] == 1
        assert outcomes.count("ok") >= 1
        ticket = platform.tickets[tid]
        assert ticket["status"] == "succeeded"
        assert ticket["executed"] is True
    finally:
        tool["handler"] = original


def test_upload_rejects_oversized_and_bad_type(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=headers,
        json={"name": "sec", "code": "sec1"},
    ).json()
    big = b"x" * (2 * 1024 * 1024 + 10)
    oversized = client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=headers,
        files={"file": ("big.txt", big, "text/plain")},
    )
    assert oversized.status_code == 400
    bad = client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=headers,
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert bad.status_code == 400


def test_filename_sanitized_no_traversal():
    assert ".." not in sanitize_filename("../../etc/passwd")
    assert "/" not in sanitize_filename("a/b/c.txt")
    assert sanitize_filename("..\\..\\x.md").endswith(".md")


def test_upload_sanitizes_traversal_name(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=headers,
        json={"name": "path", "code": "path1"},
    ).json()
    resp = client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=headers,
        files={"file": ("../../secret.md", b"# ok", "text/markdown")},
    )
    assert resp.status_code == 200
    assert ".." not in resp.json()["filename"]
    assert "/" not in resp.json()["filename"]


def test_production_rejects_plaintext_password(monkeypatch):
    settings = Settings(
        app_env="production",
        jwt_secret="a" * 32,
        allow_demo_users=False,
        storage_backend="postgres",
        database_url="postgresql://x",
        redis_url="redis://x",
        ticket_args_secret="b" * 16,
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    assert verify_password("changeme", "changeme") is False
    assert verify_password("changeme", hash_password("changeme")) is True


def test_redaction_failure_does_not_echo_secret(monkeypatch):
    import logging

    from app.security.redaction import RedactingFilter

    def boom(*_a, **_k):
        raise RuntimeError("secret=sk-abcdefghijklmnopqrstuvwxyz012345")

    monkeypatch.setattr("app.security.redaction.redact_text", boom)
    record = logging.LogRecord(
        name="t",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="should not leak",
        args=(),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in str(record.msg)
    assert record.msg == "[REDACTION_ERROR]"


def test_login_rate_limited(client):
    # Exhaust login attempts for a bad password (limit default 10/min).
    for _ in range(11):
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong", "tenant_code": "demo"},
        )
    blocked = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong", "tenant_code": "demo"},
    )
    assert blocked.status_code == 429


def test_jwt_algorithm_none_rejected(client):
    import base64
    import json

    def b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'user_id': 'x', 'tenant_id': 'demo', 'perms': ['dashboard:view'], 'jti': 'n1', 'iat': 1, 'exp': 9999999999})}."
    resp = client.get("/api/admin/me", headers=auth_header(token))
    assert resp.status_code == 401


def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_tampered_ticket_args_rejected(client):
    from app.core.crypto import encrypt_json

    token = login(client, "admin", "demo")
    headers = auth_header(token)
    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    tid = risk["ticket_id"]
    client.post(f"/api/admin/tickets/{tid}/approve", headers=headers)
    ticket = client.app.state.platform.tickets[tid]
    ticket["args_encrypted"] = encrypt_json({"order_id": "HACKED", "owner_user_id": "usr_x"})
    denied = client.post(f"/api/admin/tickets/{tid}/execute", headers=headers)
    assert denied.status_code == 409
    assert client.app.state.platform.tickets[tid]["status"] == "failed"
    assert client.app.state.platform.tickets[tid]["reason"] == "args_integrity_failed"


def test_redis_rate_limit_fail_closed_in_production(monkeypatch):
    from app.infra import redis_client as rc

    settings = Settings(
        app_env="production",
        jwt_secret="a" * 32,
        allow_demo_users=False,
        storage_backend="postgres",
        database_url="postgresql://x",
        redis_url="redis://127.0.0.1:6379/0",
        redis_required=True,
        ticket_args_secret="b" * 16,
    )
    monkeypatch.setattr(rc, "get_settings", lambda: settings)
    monkeypatch.setattr(rc, "get_redis", lambda: None)
    assert rc.redis_rate_limit("k", 10) is False


def test_recovery_marks_executing_as_reconciliation_required():
    """Crash mid-execute → uncertain outcome, not a confirmed tool failure."""
    from app.agents.recovery import recover_on_startup
    from app.core.platform import Platform

    platform = Platform.seed()
    platform.tickets["tck_crash"] = {
        "id": "tck_crash",
        "tenant_id": "demo",
        "status": "executing",
        "tool_name": "refund_order",
        "executed": False,
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.agents.recovery.persistence_enabled", lambda: True)
        mp.setattr("app.agents.recovery.load_all_into", lambda _p: 0)
        mp.setattr("app.agents.recovery.list_paused_for_recovery", lambda: [])
        mp.setattr("app.infra.entity_repo.load_users_into", lambda _p: 1)
        mp.setattr("app.infra.entity_repo.load_kb_meta_into", lambda _p: 0)
        mp.setattr("app.infra.ticket_repo.upsert_ticket", lambda _t: None)
        report = recover_on_startup(platform)
    assert "tck_crash" in report["interrupted_executing"]
    assert platform.tickets["tck_crash"]["status"] == "reconciliation_required"
    assert platform.tickets["tck_crash"]["reason"] == "outcome_uncertain_after_restart"
    assert platform.tickets["tck_crash"]["status"] != "failed"
