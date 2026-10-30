"""Unfinished backlog menu + API."""

from __future__ import annotations

from tests.conftest import auth_header, login


def test_backlog_is_last_top_level_menu_and_detailed(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    menus = client.get("/api/admin/me/menus", headers=headers).json()["menus"]
    assert menus, "menus empty"
    last = menus[-1]
    assert last["code"] == "backlog"
    assert last["title"] == "未完成代办"
    kids = last.get("children") or []
    assert any(c.get("path") == "/backlog" for c in kids), last
    # Also discoverable under 系统配置
    codes = []
    def walk(nodes):
        for n in nodes:
            codes.append(n.get("code"))
            walk(n.get("children") or [])
    walk(menus)
    assert "sys.backlog" in codes

    data = client.get("/api/admin/backlog", headers=headers).json()
    assert data.get("title") == "未完成代办"
    assert data.get("counts", {}).get("blocked", 0) >= 1
    groups = data.get("groups") or []
    assert len(groups) >= 4
    # Must not claim Live items as done
    live = next(g for g in groups if g["id"] == "live_external")
    emb = next(i for i in live["items"] if i["id"] == "embedding-live")
    assert emb["status"] == "blocked"
    assert emb.get("done_when")
    assert "OPENAI_API_KEY" in " ".join(emb["done_when"])

    # SPA page module exports handler (pages-ext-rbac after modular split).
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app/static/admin"
    rbac = (root / "pages-ext-rbac.js").read_text(encoding="utf-8")
    assert "pageBacklog" in rbac
    assert '"/backlog": pageBacklog' in rbac
    index = (root / "index.html").read_text(encoding="utf-8")
    assert "pages-ext-rbac.js" in index
