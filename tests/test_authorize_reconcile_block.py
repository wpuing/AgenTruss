"""Authorize path must not re-submit while reconciliation_required."""

from __future__ import annotations

from types import SimpleNamespace

from app.tools.registry import _idempotency_key, authorize_call


def test_authorize_blocks_resubmit_while_reconciling():
    auth = SimpleNamespace(
        user_id="u1",
        tenant_id="demo",
        roles=["tenant_admin", "end_user"],
    )
    agent = {"enabled_tools": ["refund_order"]}
    clean = {"order_id": "1001", "owner_user_id": "u1"}
    idem = _idempotency_key("demo", "refund_order", clean)
    platform = SimpleNamespace(
        tickets={
            "tck_old": {
                "id": "tck_old",
                "tenant_id": "demo",
                "tool_name": "refund_order",
                "idempotency_key": idem,
                "status": "reconciliation_required",
            }
        }
    )
    out = authorize_call(platform, auth, agent, "refund_order", {"order_id": "1001"})
    assert out["ok"] is False
    assert out["reason"] == "reconciliation_required"
    assert out["ticket_id"] == "tck_old"
    assert out["need_human"] is True
    assert len(platform.tickets) == 1


def test_map_uncertain_to_http_409():
    from app.modules.approvals.application.services import map_domain_to_http
    from app.modules.approvals.domain.exceptions import OutcomeUncertainError

    code, detail = map_domain_to_http(OutcomeUncertainError("tool outcome uncertain"))
    assert code == 409
    assert "uncertain" in detail


def test_platform_executor_query_timeout_not_reconcile():
    """Read-only tool timeout stays RuntimeError (SAFE_RETRY), not OutcomeUncertain."""
    import pytest
    from app.modules.approvals.domain.exceptions import OutcomeUncertainError
    from app.modules.approvals.infrastructure.adapters import PlatformToolExecutor

    exe = PlatformToolExecutor()
    tool = __import__("app.tools.registry", fromlist=["get_tool"]).get_tool("query_order")
    assert tool is not None
    original = tool["handler"]

    def boom(_args, _ctx):
        raise TimeoutError("upstream timed out")

    tool["handler"] = boom
    try:
        with pytest.raises(RuntimeError, match="timed out") as caught:
            exe.run("query_order", {}, {"owner_user_id": "u1"})
        assert not isinstance(caught.value, OutcomeUncertainError)
    finally:
        tool["handler"] = original


def test_different_logical_args_get_different_idempotency_keys():
    from app.tools.registry import _idempotency_key

    a = _idempotency_key("demo", "refund_order", {"order_id": "1", "owner_user_id": "u1"})
    b = _idempotency_key("demo", "refund_order", {"order_id": "2", "owner_user_id": "u1"})
    assert a != b
