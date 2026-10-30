"""API-level Knowledge main flow (Mock RAG, memory or durable sqlite)."""

from __future__ import annotations

from tests.conftest import auth_header, login


def test_kb_upload_parse_chunk_vectorize_retrieve_logout(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=headers,
        json={"name": "flow", "code": "flow1"},
    ).json()
    body = ("退货政策：七天无理由。" * 80) + "\n" + ("补充条款。" * 40)
    files = {"file": ("policy.md", body.encode("utf-8"), "text/markdown")}
    doc = client.post(f"/api/admin/kb/{kb['id']}/documents", headers=headers, files=files).json()
    assert doc["status"] == "uploaded"
    detail = client.get(f"/api/admin/documents/{doc['id']}", headers=headers).json()
    assert "七天无理由" in detail["text"]
    assert len(detail["text"]) == len(body)
    assert len(detail["text"]) > 500
    assert client.post(f"/api/admin/documents/{doc['id']}/parse", headers=headers).status_code == 200
    assert client.post(
        f"/api/admin/documents/{doc['id']}/chunk", headers=headers, json={}
    ).status_code == 200
    assert client.post(f"/api/admin/documents/{doc['id']}/vectorize", headers=headers).status_code == 200
    retrieved = client.post(
        "/api/admin/kb/retrieve-test",
        headers=headers,
        json={"kb_id": kb["id"], "query": "退货政策"},
    ).json()
    assert "provider_mode" in retrieved
    assert "mock" in retrieved["provider_mode"]
    assert retrieved["hits"]
    logout = client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    # Token revoked — further admin call should fail.
    denied = client.get("/api/admin/knowledge-bases", headers=headers)
    assert denied.status_code in {401, 403}


def test_delete_document_via_facade(client):
    token = login(client, "admin", "demo")
    headers = auth_header(token)
    kb = client.post(
        "/api/admin/knowledge-bases", headers=headers, json={"name": "d", "code": "del1"}
    ).json()
    doc = client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=headers,
        files={"file": ("a.md", b"hello delete me", "text/markdown")},
    ).json()
    assert client.delete(f"/api/admin/documents/{doc['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/admin/documents/{doc['id']}", headers=headers).status_code == 404


def test_cross_tenant_kb_document_denied(client):
    """Tenant A resources are invisible to tenant B (API + JWT identity)."""
    demo = auth_header(login(client, "admin", "demo"))
    other = auth_header(login(client, "finadmin", "finance"))
    kb = client.post(
        "/api/admin/knowledge-bases",
        headers=demo,
        json={"name": "iso", "code": "iso1"},
    ).json()
    doc = client.post(
        f"/api/admin/kb/{kb['id']}/documents",
        headers=demo,
        files={"file": ("secret.md", b"tenant-a-only-body", "text/markdown")},
    ).json()
    assert client.get(f"/api/admin/documents/{doc['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/admin/documents/{doc['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/admin/knowledge-bases/{kb['id']}", headers=other).status_code == 404
    # Owner still sees the document.
    assert client.get(f"/api/admin/documents/{doc['id']}", headers=demo).status_code == 200
