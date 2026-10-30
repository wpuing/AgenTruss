"""Regression for user-visible main-flow gaps fixed in Phase5.27."""

from __future__ import annotations

from pathlib import Path

from app.agents.nodes import _stub_llm
from app.agents.recovery import recover_on_startup
from app.core.platform import Platform
from app.rag.service import rehydrate_mock_vectors
from app.rag.vector_store import get_mock_vector_store, reset_mock_vector_store
from tests.conftest import auth_header, login
from tests.test_acceptance import _ingest


def test_stub_llm_never_claims_live_call():
    reply, called = _stub_llm("你好", [], [])
    assert called is False
    assert "已收到" in reply
    reply2, called2 = _stub_llm("退货", [{"text": "七天无理由"}], [{"role": "user"}, {"role": "assistant"}])
    assert called2 is False
    assert "根据知识库" in reply2
    assert "参考前" in reply2


def test_stable_agent_ids_and_patch_status(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    agents = client.get("/api/admin/agents", headers=headers).json()["items"]
    assert agents
    agent_id = agents[0]["agent_id"]
    assert agent_id == "agt-demo-default"
    patched = client.patch(
        f"/api/admin/agents/{agent_id}",
        headers=headers,
        json={"status": "disabled"},
    ).json()
    assert patched["status"] == "disabled"
    again = client.get(f"/api/admin/agents/{agent_id}", headers=headers).json()
    assert again["status"] == "disabled"
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "published"})


def test_chat_returns_llm_mode_stub_and_ticket_deep_link_fields(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    chat = client.post("/api/chat", headers=headers, json={"message": "查询订单 ORD-1"}).json()
    assert chat.get("llm_called") is False
    assert chat.get("llm_mode") == "stub"
    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    assert risk["ticket_id"]
    assert risk.get("need_human") is True


def test_system_health_tracks_ready_ok(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    status = client.get("/api/admin/system/status", headers=headers).json()
    # Memory mode: ready_ok True → health ok
    assert status["ready_ok"] is True
    assert status["health"]["status"] == "ok"


def test_mock_vector_rehydrate_after_clear(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb_id, _doc_id = _ingest(client, token)
    hits = client.post(
        "/api/admin/kb/retrieve-test",
        headers=headers,
        json={"kb_id": kb_id, "query": "退货政策"},
    ).json()["hits"]
    assert hits
    # Simulate process restart: wipe Mock store while keeping platform segments.
    reset_mock_vector_store()
    assert get_mock_vector_store()._by_id == {}
    platform = client.app.state.platform
    n = rehydrate_mock_vectors(platform)
    assert n > 0
    hits2 = client.post(
        "/api/admin/kb/retrieve-test",
        headers=headers,
        json={"kb_id": kb_id, "query": "退货政策"},
    ).json()["hits"]
    assert hits2


def test_multi_turn_stub_sees_history(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    first = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "查询订单 ORD-1001", "session_id": "ses-flow-hist"},
    ).json()
    second = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "再查一下物流状态", "session_id": first["session_id"]},
    ).json()
    # Second turn must consume prior history (stub acknowledges prior rounds) or tool path.
    assert "会话已有" in second["reply"] or "1 轮" in second["reply"] or "订单" in second["reply"]


def test_spa_ticket_filter_uses_succeeded_not_executed():
    # Pages split out of app.js — assert against tickets + chat modules.
    tickets = Path("app/static/admin/pages-tickets.js").read_text(encoding="utf-8")
    chat = Path("app/static/admin/pages-chat.js").read_text(encoding="utf-8")
    # Status dropdown options (not the boolean column row.executed).
    assert '["pending", "approved", "rejected", "succeeded"' in tickets
    assert '"executed", "failed"' not in tickets
    assert "tickets/risk/detail?id=" in chat  # chat deep-link target exists


def test_spa_execution_detail_and_human_landing():
    exec_js = Path("app/static/admin/pages-executions.js").read_text(encoding="utf-8")
    audit_js = Path("app/static/admin/pages-ext-audit.js").read_text(encoding="utf-8")
    system_js = Path("app/static/admin/pages-system.js").read_text(encoding="utf-8")
    assert '"/audit/executions/detail": pageExecutionDetail' in exec_js
    assert "/api/admin/executions/" in exec_js
    assert "pageHumanTicketsFull" in audit_js
    assert '"/system/ratelimit": pageRateLimit' in system_js
    assert "人工" in audit_js


def test_execution_detail_api_memory(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    client.post("/api/chat", headers=headers, json={"message": "查询订单 ORD-detail"})
    items = client.get("/api/admin/executions", headers=headers).json()["items"]
    assert items
    run_id = items[0]["run_id"]
    detail = client.get(f"/api/admin/executions/{run_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id
    missing = client.get("/api/admin/executions/no_such_run", headers=headers)
    assert missing.status_code == 404


def test_memory_mode_recovery_note_without_crash():
    platform = Platform.seed()
    report = recover_on_startup(platform)
    assert report["note"]
    assert platform.agents["agt-demo-default"]["agent_id"] == "agt-demo-default"


def test_auditor_cannot_chat(client):
    auditor = login(client, "auditor", "demo")
    resp = client.post(
        "/api/chat",
        headers=auth_header(auditor),
        json={"message": "查询订单"},
    )
    assert resp.status_code == 403


def test_disabled_agent_cannot_be_invoked(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    agent_id = client.get("/api/admin/agents", headers=headers).json()["items"][0]["agent_id"]
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "disabled"})
    resp = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "查询订单", "agent_id": agent_id},
    )
    assert resp.status_code == 404
    body = resp.json()
    # Unified error envelope uses message (not raw FastAPI detail).
    msg = str(body.get("message") or body.get("detail") or "")
    assert "not published" in msg
    assert "disabled" in msg
    client.patch(f"/api/admin/agents/{agent_id}", headers=headers, json={"status": "published"})


def test_workflows_agents_are_tenant_scoped(client):
    demo = login(client, "admin", "demo")
    finance = login(client, "finadmin", "finance")
    demo_agents = {
        a["agent_id"]
        for a in client.get("/api/admin/workflows", headers=auth_header(demo)).json()["agents"]
    }
    finance_agents = {
        a["agent_id"]
        for a in client.get("/api/admin/workflows", headers=auth_header(finance)).json()["agents"]
    }
    assert demo_agents == {"agt-demo-default"}
    assert finance_agents == {"agt-finance-default"}
    assert demo_agents.isdisjoint(finance_agents)


def test_jsonplaceholder_base_rejects_ssrf_host(monkeypatch):
    from app.tools import jsonplaceholder_adapter as jp

    monkeypatch.setenv("JSONPLACEHOLDER_BASE_URL", "http://169.254.169.254/")
    try:
        jp._base()
    except RuntimeError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
