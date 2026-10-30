"""Embedding provider contracts — Mock vs HTTPS adapter (HTTP mocked ≠ live model)."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from app.rag.embedding import (
    EmbeddingError,
    MockEmbeddingProvider,
    OpenAICompatibleEmbedding,
    assert_vectors_match_configured_dim,
    get_embedding_provider,
)


def test_mock_rejects_bad_dim() -> None:
    with pytest.raises(EmbeddingError):
        MockEmbeddingProvider(dim=1)


def test_openai_compatible_requires_api_key() -> None:
    with pytest.raises(EmbeddingError, match="api_key"):
        OpenAICompatibleEmbedding(api_key="", base_url="http://x", model="m", dim=8)


def test_openai_compatible_batch_limit() -> None:
    provider = OpenAICompatibleEmbedding(
        api_key="k",
        base_url="http://test.local/v1",
        model="m",
        dim=3,
        max_batch=2,
    )
    with pytest.raises(EmbeddingError, match="batch too large"):
        provider.embed(["a", "b", "c"])


def test_openai_compatible_success_via_mock_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract test with MockTransport — NOT a real model call."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content.decode())
        assert body["model"] == "demo-model"
        assert body["input"] == ["hello"]
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.0, 1.0, 0.0]}]},
        )

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret",
        base_url="http://test.local/v1",
        model="demo-model",
        dim=3,
    )
    vec = provider.embed_one("hello")
    assert vec == [0.0, 1.0, 0.0]


@pytest.mark.parametrize(
    "status,match",
    [
        (401, "authentication"),
        (429, "rate limited"),
        (503, "upstream"),
        (400, "rejected"),
    ],
)
def test_openai_compatible_http_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, match: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "x"})

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    with pytest.raises(EmbeddingError, match=match):
        provider.embed(["x"])


def test_openai_compatible_dim_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=3
    )
    with pytest.raises(EmbeddingError, match="dim mismatch"):
        provider.embed(["x"])


def test_get_provider_openai_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_DIM", "8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    # Clear settings cache if present.
    from app.core import config as cfg

    if hasattr(cfg.get_settings, "cache_clear"):
        cfg.get_settings.cache_clear()
    with pytest.raises(EmbeddingError, match="OPENAI_API_KEY"):
        get_embedding_provider()


def test_get_provider_does_not_fallback_to_mock_on_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app.core import config as cfg

    if hasattr(cfg.get_settings, "cache_clear"):
        cfg.get_settings.cache_clear()
    with pytest.raises(EmbeddingError):
        get_embedding_provider()


def test_openai_compatible_empty_batch() -> None:
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    assert provider.embed([]) == []


def test_openai_compatible_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    with pytest.raises(EmbeddingError, match="not JSON"):
        provider.embed(["x"])


def test_openai_compatible_count_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.0, 1.0]}]},
        )

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    with pytest.raises(EmbeddingError, match="count mismatch"):
        provider.embed(["a", "b"])


def test_openai_compatible_reorders_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    assert provider.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_openai_compatible_non_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"data": [{"index": 0, "embedding": [float("nan"), 0.0]}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def post(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return _FakeResp()

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _FakeClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    with pytest.raises(EmbeddingError, match="non-finite"):
        provider.embed(["x"])


def test_openai_compatible_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.rag.embedding.httpx.Client", _PatchedClient)
    provider = OpenAICompatibleEmbedding(
        api_key="secret", base_url="http://test.local/v1", model="m", dim=2
    )
    with pytest.raises(EmbeddingError, match="timed out"):
        provider.embed(["x"])


def test_assert_vectors_match_configured_dim() -> None:
    assert_vectors_match_configured_dim([[0.0, 1.0]], 2)
    with pytest.raises(EmbeddingError, match="dim mismatch"):
        assert_vectors_match_configured_dim([[0.0]], 2)


def test_get_provider_dashscope_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app.core import config as cfg

    if hasattr(cfg.get_settings, "cache_clear"):
        cfg.get_settings.cache_clear()
    with pytest.raises(EmbeddingError, match="DASHSCOPE_API_KEY"):
        get_embedding_provider()


@pytest.mark.skipif(
    not os.getenv("RUN_EMBEDDING_LIVE")
    or not (os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")),
    reason="RUN_EMBEDDING_LIVE=1 and OPENAI_API_KEY or DASHSCOPE_API_KEY required",
)
def test_live_embedding_optional() -> None:
    """Real HTTPS embedding — skipped≠PASS when credentials absent."""
    provider = get_embedding_provider()
    vec = provider.embed_one("agentruss embedding smoke")
    assert len(vec) == provider.dim
    assert all(isinstance(x, float) for x in vec)
