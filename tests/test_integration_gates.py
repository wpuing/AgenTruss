"""Gated integration tests — skip (not pass) when services/credentials are absent."""

from __future__ import annotations

import os
from typing import Any

import pytest


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


@pytest.mark.skipif(not _flag("RUN_REDIS_INTEGRATION"), reason="RUN_REDIS_INTEGRATION not set")
def test_redis_login_rate_limit_shared():
    from app.core.config import get_settings
    from app.infra.redis_client import get_redis

    settings = get_settings()
    assert settings.redis_url, "REDIS_URL required for Redis integration"
    client = get_redis()
    assert client is not None
    key = "agentruss:itest:rl"
    client.delete(key)
    assert client.incr(key) == 1
    client.expire(key, 30)
    assert int(client.get(key) or 0) == 1


@pytest.mark.skipif(not _flag("RUN_MINIO_INTEGRATION"), reason="RUN_MINIO_INTEGRATION not set")
def test_minio_put_get_roundtrip():
    from app.storage.minio_store import get_bytes, put_bytes

    key = "itest/demo/roundtrip.txt"
    put_bytes(key, b"hello-agentruss", content_type="text/plain")
    assert get_bytes(key) == b"hello-agentruss"


@pytest.mark.skipif(not _flag("RUN_MILVUS_INTEGRATION"), reason="RUN_MILVUS_INTEGRATION not set")
def test_milvus_upsert_search_tenant_filter(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "milvus")
    monkeypatch.setenv("EMBEDDING_MODE", "stub")
    monkeypatch.setenv("EMBEDDING_DIM", "8")
    from app.rag.embedding import StubEmbedding
    from app.storage import milvus_store as ms

    emb = StubEmbedding(dim=8)
    vecs = emb.embed(["tenant-a policy", "other"])
    ms.upsert_segments(
        tenant_id="demo",
        kb_id="kb1",
        doc_id="doc1",
        rows=[{"segment_id": "seg1", "text": "tenant-a policy"}],
        vectors=[vecs[0]],
    )
    hits = ms.search(tenant_id="demo", kb_id="kb1", query_vector=vecs[0], top_k=3)
    assert hits
    foreign = ms.search(tenant_id="other", kb_id="kb1", query_vector=vecs[0], top_k=3)
    assert foreign == []


@pytest.mark.skipif(
    not _flag("RUN_AGENTSCOPE_LIVE"),
    reason="RUN_AGENTSCOPE_LIVE not set — Live E2E not executed (BLOCKED ≠ PASS)",
)
def test_agentscope_live_e2e():
    """Requires AGENTSCOPE_MODEL_MODE=live + real API key. Flag without key ⇒ FAIL (not skip)."""
    import asyncio
    import os

    from app.core.config import get_settings
    from app.runtime.agentscope_ext.live_model import build_live_chat_model

    settings = get_settings()
    assert settings.agentscope_model_mode == "live", (
        "set AGENTSCOPE_MODEL_MODE=live when RUN_AGENTSCOPE_LIVE=1"
    )
    key = (settings.dashscope_api_key or settings.openai_api_key or "").strip()
    assert key, "live e2e requires DASHSCOPE_API_KEY or OPENAI_API_KEY"

    provider = "dashscope" if (settings.dashscope_api_key or "").strip() else "openai"
    model_name = os.getenv("AGENTSCOPE_LIVE_MODEL") or (
        "qwen-plus" if provider == "dashscope" else "gpt-4o-mini"
    )

    class _Gw:
        def __init__(self) -> None:
            self.allowed_models: set[str] = set()
            self.calls = 0

        def before_call(self, _name: str) -> None:
            self.calls += 1

    gw = _Gw()
    model = build_live_chat_model(provider=provider, model_name=model_name, gateway=gw)

    async def _once() -> Any:
        # AgentScope 2.x chat models are async callables.
        return await model([{"role": "user", "content": "Reply with exactly: PONG"}])

    result = asyncio.run(_once())
    assert result is not None
    assert gw.calls >= 1
    assert getattr(model, "live_claimed", False) is True
    assert getattr(model, "test_mode", True) is False
