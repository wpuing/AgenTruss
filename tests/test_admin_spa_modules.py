"""Static architecture checks for Admin SPA page modules (no browser)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path("app/static/admin")


def test_index_loads_kit_and_all_page_modules():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "admin-kit.js" in index
    assert "app.js" in index
    pages = sorted(ROOT.glob("pages*.js"))
    assert len(pages) >= 19
    for path in pages:
        assert path.name in index, f"index.html missing script for {path.name}"
        text = path.read_text(encoding="utf-8")
        assert "registerPages" in text, f"{path.name} must call registerPages"


def test_no_duplicate_local_pickAgent_in_page_modules():
    """pickAgent lives in admin-kit createPageScope — pages should not redefine it."""
    for path in ROOT.glob("pages*.js"):
        text = path.read_text(encoding="utf-8")
        assert "async function pickAgent(" not in text, f"duplicate pickAgent in {path.name}"
        assert "function agentFilter(" not in text, f"duplicate agentFilter in {path.name}"


def test_chat_playground_uses_for_chat_and_empty_state():
    """Chat must load dialable Agents via for_chat; never fall back to non-callable."""
    chat = (ROOT / "pages-chat.js").read_text(encoding="utf-8")
    assert "/api/admin/agents?for_chat=true" in chat
    assert "没有可对话的 published Agent" in chat
    assert 'api("/api/admin/agents")' not in chat  # must not load unfiltered list
    assert "sendGen" in chat
    assert "chat_callable" in chat


def test_runtime_debug_and_basic_use_chat_callable_not_status_alone():
    runtime = (ROOT / "pages-agent-runtime.js").read_text(encoding="utf-8")
    assert "chat_callable === true" in runtime
    assert 'a.status === "disabled" ? "disabled"' not in runtime
    basic = (ROOT / "pages-ext-agent-readonly.js").read_text(encoding="utf-8")
    assert "chat_callable === true" in basic


def test_channel_and_kb_detail_do_not_swallow_load_errors():
    channels = (ROOT / "pages-ext-channels.js").read_text(encoding="utf-8")
    assert "渠道配置加载失败" in channels
    page_channel = channels.split("async function pageChannel")[1].split("async function pagePhase2")[0]
    assert "catch {" not in page_channel  # no empty catch swallowing detail errors
    assert "failPanel" in page_channel
    kb = (ROOT / "pages-ext-kb.js").read_text(encoding="utf-8")
    assert "文档列表加载失败" in kb
    assert "未将失败显示为" in kb


def test_index_cache_bust_ui30():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "ui30" in index
    assert "ui29" not in index
