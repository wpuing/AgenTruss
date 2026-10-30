"""Full-path redaction regression: API, memory, LLM block, logging exceptions."""

from __future__ import annotations

import logging

from app.security.redaction import RedactingFilter, install_logging_redaction, redact_text
from tests.conftest import auth_header, login


def test_chat_blocks_credential_before_llm(client):
    token = login(client, "admin", "demo")
    resp = client.post(
        "/api/chat",
        headers=auth_header(token),
        json={"message": "订单查询 api_key=sk-abcdefghijklmnopqrstuvwxyz012345"},
    ).json()
    assert resp.get("block_reason") == "credential_in_prompt"
    assert resp.get("guardrail_hit") is True
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in str(resp)


def test_audit_and_session_api_redact_secrets(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    chat = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "订单物流 手机13900001111"},
    ).json()
    sid = chat["session_id"]
    audits = client.get("/api/admin/audit-logs", headers=headers).json()["items"]
    blob = str(audits)
    assert "13900001111" not in blob
    assert "sk-" not in blob or "[REDACTED" in blob
    detail = client.get(f"/api/admin/sessions/{sid}", headers=headers).json()
    assert "13900001111" not in str(detail)


def test_kb_qa_does_not_claim_llm_called(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=headers,
        json={"name": "qa", "code": "qa1"},
    ).json()
    client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=headers,
        files={"file": ("a.md", "# 退货政策 七天无理由".encode("utf-8"), "text/markdown")},
    )
    docs = client.get(f"/api/admin/kb/{kb['id']}/documents", headers=headers).json()["items"]
    doc_id = docs[0]["id"]
    client.post(f"/api/admin/documents/{doc_id}/parse", headers=headers)
    client.post(f"/api/admin/documents/{doc_id}/chunk", headers=headers, json={})
    client.post(f"/api/admin/documents/{doc_id}/vectorize", headers=headers)
    qa = client.post(
        "/api/admin/kb/qa",
        headers=headers,
        json={"kb_id": kb["id"], "query": "退货政策"},
    ).json()
    assert qa["sources"]
    assert qa["llm_called"] is False
    assert qa.get("synthesis") == "retrieval_template"


def test_logger_exception_redacts_exc_text():
    install_logging_redaction()
    logger = logging.getLogger("app.test_redaction_exc")
    logger.setLevel(logging.ERROR)
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    logger.handlers = [handler]
    try:
        raise RuntimeError("boom postgres://agent:SuperSecretPass99@db/app Bearer sk-abcdefghijklmnopqrstuvwxyz012345")
    except RuntimeError:
        record = logging.LogRecord(
            name="app.test_redaction_exc",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=True,
        )
        # Simulate formatted exc_text
        record.exc_text = (
            "Traceback...\nRuntimeError: boom postgres://agent:SuperSecretPass99@db/app "
            "Bearer sk-abcdefghijklmnopqrstuvwxyz012345"
        )
        assert RedactingFilter().filter(record) is True
        assert "SuperSecretPass99" not in (record.exc_text or "")
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in (record.exc_text or "")


def test_minio_and_aws_secret_patterns():
    text = (
        "MINIO_SECRET_KEY=minio-super-secret-value99 "
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY "
        "POSTGRES_PASSWORD=PgPass123456 "
        "Cookie: session=abcsecretcookievalue"
    )
    out = redact_text(text)
    assert "minio-super-secret-value99" not in out.value
    assert "wJalrXUtnFEMI" not in out.value
    assert "PgPass123456" not in out.value
    assert "abcsecretcookievalue" not in out.value
