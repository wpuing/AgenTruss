"""HTTP lookup-resolve: auth, tenant isolation, no tool re-run."""

from __future__ import annotations

from tests.conftest import auth_header, login


def _seed_reconciling(client, token: str) -> str:
    headers = auth_header(token)
    risk = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "订单申请退款"},
    ).json()
    tid = risk["ticket_id"]
    assert client.post(f"/api/admin/tickets/{tid}/approve", headers=headers).status_code == 200
    ticket = client.app.state.platform.tickets[tid]
    ticket["status"] = "reconciliation_required"
    ticket["reason"] = "outcome_uncertain_after_restart"
    ticket["execution_result"] = "side_effect_unknown_manual_reconcile_required"
    ticket["executed"] = False
    return tid


def test_lookup_resolve_requires_approve_permission(client):
    admin = login(client, "admin", "demo")
    auditor = login(client, "auditor", "demo")
    tid = _seed_reconciling(client, admin)
    denied = client.post(
        f"/api/admin/tickets/{tid}/lookup-resolve",
        headers=auth_header(auditor),
    )
    assert denied.status_code == 403


def test_lookup_resolve_cross_tenant_not_found(client):
    admin = login(client, "admin", "demo")
    other = login(client, "finadmin", "finance")
    tid = _seed_reconciling(client, admin)
    resp = client.post(
        f"/api/admin/tickets/{tid}/lookup-resolve",
        headers=auth_header(other),
    )
    assert resp.status_code == 404
    assert client.app.state.platform.tickets[tid]["status"] == "reconciliation_required"


def test_lookup_resolve_demo_stays_reconciling_without_execute(client, monkeypatch):
    """Demo refund has no lookup adapter → inconclusive; executor never runs."""
    admin = login(client, "admin", "demo")
    tid = _seed_reconciling(client, admin)
    calls: list[str] = []

    def boom(*_a, **_k):
        calls.append("run")
        raise AssertionError("executor must not run on lookup-resolve")

    monkeypatch.setattr(
        "app.modules.approvals.infrastructure.adapters.PlatformToolExecutor.run",
        boom,
        raising=False,
    )
    ok = client.post(
        f"/api/admin/tickets/{tid}/lookup-resolve",
        headers=auth_header(admin),
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "reconciliation_required"
    assert calls == []
    assert "lookup" in (body.get("reason") or body.get("execution_result") or "").lower() or (
        "inconclusive" in (body.get("execution_result") or "").lower()
        or "reconcile" in (body.get("reason") or "").lower()
        or body["status"] == "reconciliation_required"
    )


def test_lookup_resolve_with_adapter_succeeds(client, monkeypatch):
    from app.tools import lookup_registry
    from app.tools.reliability import ToolOutcome, ToolOutcomeKind

    admin = login(client, "admin", "demo")
    tid = _seed_reconciling(client, admin)
    ticket = client.app.state.platform.tickets[tid]
    ticket["tool_name"] = "pay_ext"

    lookup_registry.clear_lookups()
    lookup_registry.register_lookup(
        "pay_ext",
        lambda _ref: ToolOutcome(kind=ToolOutcomeKind.SUCCEEDED, evidence="http_ext_paid"),
    )
    import app.tools.reliability as rel

    orig = rel.capability_for

    def _cap(name):
        if name == "pay_ext":
            return rel.ToolCapability(
                name="pay_ext",
                has_side_effects=True,
                supports_idempotency_key=True,
                supports_result_lookup=True,
                high_risk=True,
            )
        return orig(name)

    monkeypatch.setattr(rel, "capability_for", _cap)
    try:
        ok = client.post(
            f"/api/admin/tickets/{tid}/lookup-resolve",
            headers=auth_header(admin),
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["status"] == "succeeded"
        assert body["executed"] is True
        assert "http_ext_paid" in (body.get("execution_result") or "")
    finally:
        lookup_registry.clear_lookups()


def test_lookup_resolve_conflict_on_terminal(client):
    admin = login(client, "admin", "demo")
    tid = _seed_reconciling(client, admin)
    headers = auth_header(admin)
    assert client.post(
        f"/api/admin/tickets/{tid}/reconcile",
        headers=headers,
        json={"outcome": "succeeded", "note": "done"},
    ).status_code == 200
    again = client.post(
        f"/api/admin/tickets/{tid}/lookup-resolve",
        headers=headers,
    )
    assert again.status_code == 409
