"""Browser E2E against embedded /admin SPA (Playwright).

Requires: ``pip install playwright && playwright install chromium``
Gate: ``RUN_BROWSER_E2E=1`` — skip otherwise (skip ≠ pass).
Runs in memory + Mock RAG — not a claim of real PG/MinIO/Milvus browser coverage.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

_RUN = os.getenv("RUN_BROWSER_E2E") == "1"
_DEFAULT_TIMEOUT_MS = 60_000
_CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--no-sandbox"]

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set RUN_BROWSER_E2E=1 and install playwright chromium",
)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _launch_chromium(playwright):
    """CI-friendly Chromium (small /dev/shm on GitHub runners)."""
    try:
        return playwright.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
    except Exception as exc:  # noqa: BLE001
        if _RUN:
            pytest.fail(f"chromium unavailable while RUN_BROWSER_E2E=1: {exc}")
        pytest.skip(f"chromium unavailable: {exc}")


@pytest.fixture(scope="module")
def live_base():
    from app.main import create_app

    import urllib.error
    import urllib.request

    port = _free_port()
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 45
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("uvicorn failed to start for browser e2e")
    base = f"http://127.0.0.1:{port}"
    # Wait until HTTP accepts connections (server.started can race on slow runners).
    health_deadline = time.time() + 30
    while time.time() < health_deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=2) as resp:
                if getattr(resp, "status", 200) < 500:
                    break
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.1)
    else:
        pytest.fail("uvicorn started but /health unreachable for browser e2e")
    yield base
    server.should_exit = True
    thread.join(timeout=8)


def _new_admin_page(browser, live_base: str):
    """Fresh page with CI-friendly timeouts and auto-accept leave/confirm dialogs."""
    page = browser.new_page()
    page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{live_base}/admin", wait_until="domcontentloaded")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "changeme")
    page.fill('input[name="tenant_code"]', "demo")
    page.click('button[type="submit"]')
    page.wait_for_selector("#logout-btn", timeout=_DEFAULT_TIMEOUT_MS)
    page.wait_for_selector("#page-body", timeout=_DEFAULT_TIMEOUT_MS)
    return page


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if _RUN:
            pytest.fail("playwright package not installed while RUN_BROWSER_E2E=1")
        pytest.skip("playwright package not installed")
    return sync_playwright


def test_admin_login_kb_flow_logout(live_base):
    sync_playwright = _require_playwright()

    body = ("退货政策：七天无理由。" * 60) + "\nE2E_MARK"
    upload_path = Path(__file__).resolve().parent / "_tmp_e2e_policy.md"
    upload_path.write_text(body, encoding="utf-8")
    token_holder: dict[str, str] = {}

    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)

            def _capture_login(response):
                if "/api/auth/login" in response.url and response.ok:
                    try:
                        data = response.json()
                        if data.get("access_token"):
                            token_holder["token"] = data["access_token"]
                    except Exception:  # noqa: BLE001
                        pass

            page = browser.new_page()
            page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
            page.on("dialog", lambda dialog: dialog.accept())
            page.on("response", _capture_login)
            page.goto(f"{live_base}/admin", wait_until="domcontentloaded")
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "changeme")
            page.fill('input[name="tenant_code"]', "demo")
            page.click('button[type="submit"]')
            page.wait_for_selector("#logout-btn", timeout=_DEFAULT_TIMEOUT_MS)
            assert token_holder.get("token"), "login did not yield access_token"

            page.goto(f"{live_base}/admin#/kb/base")
            page.wait_for_selector("#kb-create", timeout=_DEFAULT_TIMEOUT_MS)
            with page.expect_response(
                lambda r: "/api/admin/knowledge-bases" in r.url and r.request.method == "POST" and r.ok
            ):
                page.click("#kb-create")
                page.fill("#m-name", "e2e-kb")
                page.fill("#m-code", "e2e1")
                page.click("#modal-ok")
            page.wait_for_selector("table.data tbody tr", timeout=_DEFAULT_TIMEOUT_MS)

            page.goto(f"{live_base}/admin#/kb/docs")
            page.wait_for_selector("#doc-upload", timeout=_DEFAULT_TIMEOUT_MS)
            with page.expect_response(
                lambda r: "/documents" in r.url and r.request.method == "POST" and r.ok
            ):
                page.click("#doc-upload")
                page.set_input_files("#m-file", str(upload_path))
                page.click("#modal-ok")
            page.wait_for_selector("table.data tbody tr", timeout=_DEFAULT_TIMEOUT_MS)
            assert page.locator("table.data tbody tr").count() >= 1

            with page.expect_response(
                lambda r: "/api/admin/documents/" in r.url and r.request.method == "GET" and r.ok
            ):
                page.locator('button[data-act="view"]').first.click()
            page.wait_for_selector("pre.mono", timeout=_DEFAULT_TIMEOUT_MS)
            assert "E2E_MARK" in page.locator("pre.mono").inner_text()
            page.click("#modal-ok")

            with page.expect_response(lambda r: r.url.endswith("/parse") and r.ok):
                page.locator('button[data-act="parse"]').first.click()
            with page.expect_response(lambda r: "/chunk" in r.url and r.request.method == "POST" and r.ok):
                page.locator('button[data-act="chunk"]').first.click()
            with page.expect_response(lambda r: r.url.endswith("/vectorize") and r.ok):
                page.locator('button[data-act="vectorize"]').first.click()

            page.goto(f"{live_base}/admin#/kb/retrieve")
            page.fill("#r-q", "退货政策")
            with page.expect_response(
                lambda r: "/retrieve-test" in r.url and r.ok
            ) as retrieve_info:
                page.click("#r-go")
            retrieve_json = retrieve_info.value.json()
            mode = str(retrieve_json.get("provider_mode") or "")
            assert "mock" in mode.lower(), f"expected mock provider_mode, got {mode!r}"
            page.wait_for_selector("#r-out code", timeout=_DEFAULT_TIMEOUT_MS)
            assert "mock" in page.locator("#r-out code").inner_text().lower()

            page.goto(f"{live_base}/admin#/kb/docs")
            page.wait_for_selector("table.data tbody tr", timeout=_DEFAULT_TIMEOUT_MS)
            page.once("dialog", lambda dialog: dialog.accept())
            with page.expect_response(
                lambda r: r.request.method == "DELETE" and "/documents/" in r.url and r.ok
            ):
                page.locator('button[data-act="delete"]').first.click()
            page.wait_for_selector(".empty, table.data tbody tr", timeout=_DEFAULT_TIMEOUT_MS)

            page.click("#logout-btn")
            page.wait_for_selector("#login-form", timeout=_DEFAULT_TIMEOUT_MS)

            import httpx

            old = token_holder["token"]
            denied = httpx.get(
                f"{live_base}/api/admin/overview",
                headers={"Authorization": f"Bearer {old}"},
                timeout=10.0,
            )
            assert denied.status_code in {401, 403}, denied.status_code
            browser.close()
    finally:
        upload_path.unlink(missing_ok=True)


def test_admin_reconcile_filter_and_auditor_forbidden(live_base):
    """SPA lists reconciliation_required filter; auditor cannot call reconcile API."""
    sync_playwright = _require_playwright()

    import httpx

    with httpx.Client(base_url=live_base, timeout=30.0) as http:
        login = http.post(
            "/api/auth/login",
            json={"username": "admin", "password": "changeme", "tenant_code": "demo"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        chat = http.post("/api/chat", headers=headers, json={"message": "订单申请退款"})
        assert chat.status_code == 200, chat.text
        tid = chat.json()["ticket_id"]
        assert http.post(f"/api/admin/tickets/{tid}/approve", headers=headers).status_code == 200

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        page = _new_admin_page(browser, live_base)
        page.goto(f"{live_base}/admin#/tickets/risk")
        page.wait_for_selector("#tk-status", timeout=_DEFAULT_TIMEOUT_MS)
        options = page.locator("#tk-status option").all_text_contents()
        assert any("reconciliation_required" in o for o in options)
        page.goto(f"{live_base}/admin#/tickets/risk/detail?id={tid}")
        page.wait_for_selector("#page-body .panel", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("[data-reconcile-ok]").count() == 0
        with httpx.Client(base_url=live_base, timeout=30.0) as http:
            aud = http.post(
                "/api/auth/login",
                json={"username": "auditor", "password": "changeme", "tenant_code": "demo"},
            )
            assert aud.status_code == 200
            denied = http.post(
                f"/api/admin/tickets/{tid}/reconcile",
                headers={"Authorization": f"Bearer {aud.json()['access_token']}"},
                json={"outcome": "succeeded", "note": "e2e"},
            )
            assert denied.status_code == 403
        browser.close()


def test_admin_agent_and_workflow_pages(live_base):
    """Agent list/detail + workflow rail use real APIs; demo ≠ production graph."""
    sync_playwright = _require_playwright()

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        page = _new_admin_page(browser, live_base)

        with page.expect_response(
            lambda r: r.url.rstrip("/").endswith("/api/admin/agents")
            and r.request.method == "GET"
            and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/list")
        page.wait_for_selector("#page-body table.data tbody tr", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body table.data tbody tr").count() >= 1
        first_id = page.locator("#page-body table.data tbody tr td.mono").first.inner_text().strip()
        assert first_id.startswith("agt-") or "agt" in first_id or len(first_id) > 3

        with page.expect_response(
            lambda r: "/api/admin/agents/" in r.url
            and r.url.rstrip("/").split("/")[-1].startswith("agt")
            and r.request.method == "GET"
            and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.locator('#page-body button[data-detail]').first.click()
        page.wait_for_selector("#ag-toggle-status", timeout=_DEFAULT_TIMEOUT_MS)
        detail_text = page.locator("#page-body .panel").first.inner_text().lower()
        assert "stub" in detail_text or "agentscope" in detail_text or "execution" in detail_text

        with page.expect_response(
            lambda r: r.url.endswith("/api/admin/workflows") and r.request.method == "GET" and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/config/graph")
        page.wait_for_selector("#page-body .workflow-rail .wf-node", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body .workflow-rail .wf-node").count() >= 3
        body = page.locator("#page-body .list-page").inner_text()
        assert "langgraph" in body.lower() or "权威" in body
        assert (
            "demo" in body.lower()
            or "演示" in body
            or "ADMIN_DEMO" in body
            or "admin_demo" in body.lower()
        )
        browser.close()


def test_admin_agent_config_subpages(live_base):
    """Agent config subpages load industry-config / agents APIs (not placeholders)."""
    sync_playwright = _require_playwright()

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        page = _new_admin_page(browser, live_base)

        with page.expect_response(
            lambda r: r.url.endswith("/api/admin/industries") and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/templates")
        page.wait_for_selector("#page-body .panel", timeout=_DEFAULT_TIMEOUT_MS)
        assert "config" in page.locator("#page-body").inner_text().lower() or "话题" in page.locator(
            "#page-body"
        ).inner_text()

        with page.expect_response(
            lambda r: "/industry-config" in r.url and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/config/basic")
        page.wait_for_selector("#ac-toggle", timeout=_DEFAULT_TIMEOUT_MS)

        with page.expect_response(
            lambda r: "/industry-config" in r.url and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/config/topics")
        page.wait_for_selector("#page-body .kv", timeout=_DEFAULT_TIMEOUT_MS)
        assert "话题" in page.locator("#page-body").inner_text() or "topics" in page.locator(
            "#page-body"
        ).inner_text().lower()

        with page.expect_response(
            lambda r: "/industry-config" in r.url and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/config/prompts")
        page.wait_for_selector("#page-body pre.mono", timeout=_DEFAULT_TIMEOUT_MS)

        with page.expect_response(
            lambda r: "/industry-config" in r.url and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/config/guardrails")
        page.wait_for_selector("#page-body .kv", timeout=_DEFAULT_TIMEOUT_MS)

        page.goto(f"{live_base}/admin#/agent/config/publish")
        page.wait_for_selector("#pub-go", timeout=_DEFAULT_TIMEOUT_MS)

        with page.expect_response(
            lambda r: r.url.rstrip("/").endswith("/api/admin/knowledge-bases") and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/agent/config/kb")
        page.wait_for_selector("#kb-save", timeout=_DEFAULT_TIMEOUT_MS)

        page.goto(f"{live_base}/admin#/agent/wizard")
        page.wait_for_selector("#page-body .actions a.btn", timeout=_DEFAULT_TIMEOUT_MS)
        body = page.locator("#page-body").inner_text()
        assert "调试" in body or "列表" in body
        assert "未交付" not in body

        # Chat playground should land on a usable Agent (prefer published).
        page.goto(f"{live_base}/admin#/chat/playground")
        page.wait_for_selector("#chat-send", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#chat-send").is_enabled()
        browser.close()


def test_admin_component_and_channel_pages(live_base):
    """SPA component center + channel form call real write/test APIs (save≠connected)."""
    sync_playwright = _require_playwright()

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        page = _new_admin_page(browser, live_base)

        page.goto(f"{live_base}/admin#/system/model")
        page.wait_for_selector(".comp-test:not([disabled])", timeout=_DEFAULT_TIMEOUT_MS)
        # Prefer llm card — probe is local/stub and must not depend on field hydrate order.
        llm_test = page.locator('.panel[data-cid="llm"] .comp-test')
        target = llm_test if llm_test.count() else page.locator(".comp-test:not([disabled])").first
        with page.expect_response(
            lambda r: "/components/" in r.url
            and r.url.rstrip("/").endswith("/test-connection")
            and r.request.method == "POST"
            and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ) as probe_info:
            target.click()
        probe = probe_info.value.json()
        assert "status" in probe
        assert probe.get("ok") is not None

        page.goto(f"{live_base}/admin#/system/channel/wecom")
        page.wait_for_selector("#ch-save", timeout=_DEFAULT_TIMEOUT_MS)
        page.fill("#ch-corp_id", "ww_e2e")
        page.fill("#ch-agent_id", "1001")
        page.fill("#ch-secret", "e2e-secret-value")
        with page.expect_response(
            lambda r: "/api/admin/channels/wecom" in r.url and r.request.method == "PUT" and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ) as save_info:
            page.click("#ch-save")
        saved = save_info.value.json()
        assert saved.get("connected") is False
        assert "e2e-secret-value" not in str(saved)
        page.wait_for_selector("#ch-test", timeout=_DEFAULT_TIMEOUT_MS)

        with page.expect_response(
            lambda r: "/channels/wecom/test-connection" in r.url and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ) as ch_probe:
            page.click("#ch-test")
        ch = ch_probe.value.json()
        assert ch.get("ok") is False
        assert ch.get("status") == "protocol_not_integrated"

        uniq = f"e2e_user_{int(time.time())}"
        page.goto(f"{live_base}/admin#/iam/users/form")
        page.wait_for_selector("#u-create", timeout=_DEFAULT_TIMEOUT_MS)
        page.fill("#u-name", uniq)
        page.fill("#u-pass", "password123")
        with page.expect_response(
            lambda r: r.url.endswith("/api/admin/users") and r.request.method == "POST" and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.click("#u-create")

        # Session list + trace deep-link after a real chat turn.
        import httpx

        with httpx.Client(base_url=live_base, timeout=30.0) as http:
            login = http.post(
                "/api/auth/login",
                json={"username": "admin", "password": "changeme", "tenant_code": "demo"},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            chat = http.post(
                "/api/chat",
                headers=headers,
                json={"message": "你好，查一下退货政策"},
            )
            assert chat.status_code == 200, chat.text
            sid = chat.json().get("session_id")
            assert sid
            listed = http.get("/api/admin/sessions", headers=headers)
            assert listed.status_code == 200, listed.text
            assert any(row.get("session_id") == sid for row in (listed.json().get("items") or []))
        # Wait for sessions API from SPA (avoid matching stale .empty from prior pages).
        with page.expect_response(
            lambda r: "/api/admin/sessions" in r.url and r.request.method == "GET" and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/chat/sessions")
        page.wait_for_selector("#page-body table.data tbody tr", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body table.data tbody tr").count() >= 1
        page.goto(f"{live_base}/admin#/audit/trace?session_id={sid}")
        page.wait_for_selector("#page-body .panel, #tr-load", timeout=_DEFAULT_TIMEOUT_MS)
        browser.close()


def test_admin_shell_kit_theme_and_subnav(live_base):
    """admin-kit loads; Agent subnav + theme cards render without console errors."""
    sync_playwright = _require_playwright()

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        page = _new_admin_page(browser, live_base)
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error"
            else None,
        )

        kit_ok = page.evaluate(
            """() => !!(window.AgenTrussKit
              && typeof window.AgenTrussKit.escapeHtml === 'function'
              && typeof window.AgenTrussKit.agentConfigNav === 'function'
              && typeof window.AgenTrussKit.runBusy === 'function'
              && typeof window.AgenTrussKit.bindFilterEnter === 'function'
              && typeof window.AgenTrussKit.detailHead === 'function'
              && typeof window.AgenTrussKit.mountFailPanel === 'function'
              && typeof window.AgenTrussKit.registerPages === 'function'
              && typeof window.AgenTrussKit.createPageScope === 'function')"""
        )
        assert kit_ok, "AgenTrussKit missing or incomplete"
        modules_n = page.evaluate(
            "() => Array.isArray(window.AgenTrussPageModules) ? window.AgenTrussPageModules.length : 0"
        )
        assert modules_n >= 19, f"expected >=19 page modules after ext split, got {modules_n}"
        # Force route registry build, then assert critical paths are registered.
        page.goto(f"{live_base}/admin#/dashboard")
        page.wait_for_selector("#page-body", timeout=_DEFAULT_TIMEOUT_MS)
        routes = page.evaluate("() => window.__AgenTrussRoutes || []")
        required = {
            "/dashboard",
            "/agent/list",
            "/agent/config/kb",
            "/agent/config/publish",
            "/chat/playground",
            "/kb/base",
            "/kb/docs",
            "/kb/retrieve",
            "/tickets/risk",
            "/tickets/human",
            "/audit/executions",
            "/system/theme",
            "/system/ratelimit",
            "/backlog",
            "/iam/users",
        }
        missing = sorted(required - set(routes))
        assert not missing, f"missing registered routes: {missing}; have={len(routes)}"

        page.goto(f"{live_base}/admin#/agent/config/basic")
        page.wait_for_selector("#page-body .agent-subnav", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body .agent-subnav .subnav-link.active").count() >= 1
        assert "只读" in page.locator("#page-body .subnav-hint").inner_text()

        page.goto(f"{live_base}/admin#/system/theme")
        page.wait_for_selector("#page-body .theme-card", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body .theme-card").count() >= 3
        assert page.locator("#page-body .theme-card.active").count() == 1

        page.goto(f"{live_base}/admin#/chat/playground")
        page.wait_for_selector("#chat-send", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body .agent-subnav").count() >= 1

        page.goto(f"{live_base}/admin#/system/ratelimit")
        page.wait_for_selector("#page-body .readonly-banner", timeout=_DEFAULT_TIMEOUT_MS)
        assert page.locator("#page-body .btn:has-text('保存')").count() == 0
        assert "只读" in page.locator("#page-body .readonly-banner").inner_text()

        with page.expect_response(
            lambda r: r.url.rstrip("/").endswith("/api/admin/executions")
            and r.request.method == "GET"
            and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/audit/executions")
        page.wait_for_selector("#page-body #ex-filter", timeout=_DEFAULT_TIMEOUT_MS)

        with page.expect_response(
            lambda r: r.url.rstrip("/").endswith("/api/admin/backlog")
            and r.request.method == "GET"
            and r.ok,
            timeout=_DEFAULT_TIMEOUT_MS,
        ):
            page.goto(f"{live_base}/admin#/backlog")
        page.wait_for_selector("#page-body .backlog-filters", timeout=_DEFAULT_TIMEOUT_MS)
        page.goto(f"{live_base}/admin#/backlog?status=blocked")
        page.wait_for_selector("#page-body .chip.is-selected", timeout=_DEFAULT_TIMEOUT_MS)
        assert "blocked" in page.locator("#page-body .chip.is-selected").inner_text().lower() or "BLOCKED" in page.locator(
            "#page-body .chip.is-selected"
        ).inner_text()

        serious = [e for e in console_errors if "Failed to load resource" not in e]
        assert not serious, f"console errors: {serious[:5]}"
        browser.close()
