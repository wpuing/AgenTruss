"""Unified redaction unit + leak-path tests. All samples are fictional."""

from __future__ import annotations

import logging

from app.runtime.event_store import InMemoryEventStore
from app.runtime.events import EventType, ExecutionEvent
from app.security.redaction import (
    RedactingFilter,
    mask_tool_args,
    redact_for_api,
    redact_for_llm,
    redact_for_logs,
    redact_mapping,
    redact_text,
    sanitize_exception,
)
from tests.conftest import auth_header, login


def test_redact_phone_id_email():
    text = "联系我 13800138000 身份证 110101199001011234 邮箱 a.b@example.com"
    out = redact_text(text)
    assert "13800138000" not in out.value
    assert "110101199001011234" not in out.value
    assert "a.b@example.com" not in out.value
    assert out.categories.get("phone", 0) >= 1
    assert out.categories.get("id_card", 0) >= 1
    assert out.categories.get("email", 0) >= 1
    # Result must not echo raw hits
    assert "13800138000" not in str(out.categories)


def test_redact_bearer_jwt_api_key_and_db_url():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    text = (
        f"Authorization: Bearer abcdefghijklmnop "
        f"token={jwt} "
        f"OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz012345 "
        f"postgres://agent:SuperSecretPass99@db:5432/app "
        f"https://x.test/cb?api_key=leakme123456&x=1"
    )
    out = redact_text(text)
    assert "SuperSecretPass99" not in out.value
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out.value
    assert "abcdefghijklmnop" not in out.value or "Bearer [REDACTED]" in out.value
    assert "leakme123456" not in out.value
    assert out.blocked is True


def test_redact_nested_mapping_and_preserve_order_id():
    payload = {
        "order_id": "ORD-1001",
        "password": "hunter2-secret",
        "note": "手机 13900001111",
        "nested": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz012345", "ok": True},
        "list": ["Bearer tokentokentoken123456"],
    }
    out = redact_mapping(payload)
    assert out.value["order_id"] == "ORD-1001"
    assert out.value["password"] == "***"
    assert "13900001111" not in str(out.value)
    assert "sk-" not in str(out.value["nested"]["api_key"]) or out.value["nested"]["api_key"] == "***"
    assert "tokentokentoken123456" not in str(out.value)


def test_mask_tool_args_keeps_order_masks_owner():
    masked = mask_tool_args({"order_id": "1001", "owner_user_id": "usr_raw", "password": "x"})
    assert masked["order_id"] == "1001"
    assert masked["owner_user_id"] == "***"
    assert masked["password"] == "***"


def test_sanitize_exception_strips_secrets():
    msg = sanitize_exception(RuntimeError("failed url=postgres://u:SecretPass99@h/db Authorization=Bearer abc.def.ghi"))
    assert "SecretPass99" not in msg
    assert "Bearer abc" not in msg or "[REDACTED]" in msg


def test_event_store_redacts_payload_secrets():
    store = InMemoryEventStore()
    store.append(
        ExecutionEvent(
            type=EventType.TOOL_FAILED,
            trace_id="trc1",
            run_id="run1",
            tenant_id="demo",
            message="api_key=sk-abcdefghijklmnopqrstuvwxyz012345",
            payload={"authorization": "Bearer secret-token-value", "order_id": "O1"},
        )
    )
    row = store.list_by_run("demo", "run1")[0]
    blob = str(row)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in blob
    assert "secret-token-value" not in blob
    assert row.get("payload", {}).get("order_id") == "O1" or "O1" in blob


def test_logging_filter_redacts_message():
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="user phone 13700001111 key=sk-abcdefghijklmnopqrstuvwxyz012345",
        args=(),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert "13700001111" not in record.getMessage()
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in record.getMessage()


def test_api_ticket_and_chat_do_not_echo_secrets(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    # Chat with embedded fictional secret + phone — must not appear in reply/audit fields raw
    chat = client.post(
        "/api/chat",
        headers=headers,
        json={"message": "订单查询 手机13800138000 api_key=sk-abcdefghijklmnopqrstuvwxyz012345"},
    ).json()
    blob = str(chat)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in blob
    assert "13800138000" not in blob

    risk = client.post("/api/chat", headers=headers, json={"message": "订单申请退款"}).json()
    detail = client.get(f"/api/admin/tickets/risk/{risk['ticket_id']}", headers=headers).json()
    assert "args_encrypted" not in detail
    assert detail.get("args") in (None, {}, [])
    assert "password" not in str(detail.get("args_masked") or {}).lower() or "***" in str(detail)


def test_redact_for_llm_blocks_credentials():
    r = redact_for_llm("请使用 sk-abcdefghijklmnopqrstuvwxyz012345 调用")
    assert r.blocked is True
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in r.value


def test_redact_for_logs_and_api_helpers():
    assert "13800138000" not in str(redact_for_logs({"m": "13800138000"}))
    assert redact_for_api({"order_id": "A1", "api_key": "x"})["api_key"] == "***"
