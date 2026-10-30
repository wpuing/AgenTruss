"""HTTP-level reconcile auth, tenant isolation, and no tool re-run."""

from __future__ import annotations

from tests.conftest import auth_header, login


def _seed_reconciling(client, token: str) -> str:
    """Create approved ticket then force reconciliation_required in platform store."""
    headers = auth_header(token)
    risk = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "订单申请退款"},
    ).json()
    tid = risk["ticket_id"]
    assert client.post(f"/api/admin/tickets/{tid}/approve", headers=headers).status_code == 200
    platform = client.app.state.platform
    ticket = platform.tickets[tid]
    ticket["status"] = "reconciliation_required"
    ticket["reason"] = "outcome_uncertain_after_restart"
    ticket["execution_result"] = "side_effect_unknown_manual_reconcile_required"
    ticket["executed"] = False
    return tid


def test_reconcile_requires_approve_permission(client):
    admin = login(client, "admin", "demo")
    auditor = login(client, "auditor", "demo")
    tid = _seed_reconciling(client, admin)
    denied = client.post(
        f"/api/admin/tickets/{tid}/reconcile",
        headers=auth_header(auditor),
        json={"outcome": "succeeded", "note": "should_deny"},
    )
    assert denied.status_code == 403


def test_reconcile_cross_tenant_not_found(client):
    admin = login(client, "admin", "demo")
    other = login(client, "finadmin", "finance")
    tid = _seed_reconciling(client, admin)
    resp = client.post(
        f"/api/admin/tickets/{tid}/reconcile",
        headers=auth_header(other),
        json={"outcome": "failed", "note": "cross"},
    )
    assert resp.status_code == 404
    assert client.app.state.platform.tickets[tid]["status"] == "reconciliation_required"


def test_reconcile_succeeded_persists_without_execute(client, monkeypatch):
    admin = login(client, "admin", "demo")
    tid = _seed_reconciling(client, admin)
    calls: list[str] = []

    def boom(*_a, **_k):
        calls.append("run")
        raise AssertionError("executor must not run on reconcile")

    monkeypatch.setattr(
        "app.modules.approvals.infrastructure.adapters.PlatformToolExecutor.run",
        boom,
        raising=False,
    )
    # Patch registry handler path used by executor if constructed fresh
    from app.tools import registry as reg

    monkeypatch.setattr(reg, "get_tool", lambda _n: {"handler": boom, "name": "x"})

    ok = client.post(
        f"/api/admin/tickets/{tid}/reconcile",
        headers=auth_header(admin),
        json={"outcome": "succeeded", "note": "external_ok"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "succeeded"
    assert body["executed"] is True
    assert "reconcile_confirmed_success" in (body.get("reason") or "")
    # Re-GET
    got = client.get(f"/api/admin/tickets/risk/{tid}", headers=auth_header(admin)).json()
    assert got["status"] == "succeeded"


def test_reconcile_conflict_when_already_succeeded(client):
    admin = login(client, "admin", "demo")
    tid = _seed_reconciling(client, admin)
    headers = auth_header(admin)
    assert client.post(
        f"/api/admin/tickets/{tid}/reconcile",
        headers=headers,
        json={"outcome": "succeeded", "note": "first"},
    ).status_code == 200
    again = client.post(
        f"/api/admin/tickets/{tid}/reconcile",
        headers=headers,
        json={"outcome": "failed", "note": "late"},
    )
    assert again.status_code == 409
    assert client.get(f"/api/admin/tickets/risk/{tid}", headers=headers).json()["status"] == (
        "succeeded"
    )
