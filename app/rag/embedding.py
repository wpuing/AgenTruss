"""Embedding provider abstraction.

MockEmbeddingProvider (default): deterministic hash vectors for pipeline tests.
NOT a semantic model — do not treat scores as real relevance beyond lexical overlap.

OpenAICompatibleEmbedding talks to OpenAI or DashScope compatible-mode HTTPS APIs.
Selecting openai/dashscope without keys fails loudly (never falls back to Mock).
Production + milvus vector store forbids mock/stub embeddings.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Bound remote batch size to avoid oversized payloads (provider-specific soft limit).
_MAX_BATCH = 64
_DEFAULT_TIMEOUT = 30.0


class EmbeddingError(RuntimeError):
    """Raised for config, transport, or vector-shape failures. Never silent-success."""


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]: ...

    @property
    def dim(self) -> int: ...


class MockEmbeddingProvider:
    """Stable SHA-256 → L2-normalized float vector. Offline, no API key.

    Same text + dim ⇒ same vector. Empty text raises. Non-finite values never returned.
    """

    def __init__(self, dim: int = 8) -> None:
        if dim < 2:
            raise EmbeddingError("embedding dim must be >= 2")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed_one(self, text: str) -> list[float]:
        if text is None:
            raise EmbeddingError("text is required")
        # Allow whitespace-only for query paths but treat as distinct empty marker.
        payload = text if isinstance(text, str) else str(text)
        digest = hashlib.sha256(payload.encode("utf-8")).digest()
        # Expand digest stream deterministically if dim > 32.
        raw = digest
        while len(raw) < self._dim:
            raw += hashlib.sha256(raw).digest()
        vals = [((raw[i] / 255.0) * 2.0) - 1.0 for i in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        out = [v / norm for v in vals]
        if any(not math.isfinite(v) for v in out):
            raise EmbeddingError("non-finite mock embedding")
        if all(abs(v) < 1e-12 for v in out):
            # Extremely unlikely after normalize; keep contract of non-zero.
            out[0] = 1.0
        return out

    def embed(self, texts: list[str]) -> list[list[float]]:
        if texts is None:
            raise EmbeddingError("texts is required")
        return [self.embed_one(t) for t in texts]


# Back-compat alias used by older tests/imports.
StubEmbedding = MockEmbeddingProvider


def _validate_vectors(vectors: list[list[float]], *, dim: int, expected_count: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingError(
            f"embedding count mismatch expected={expected_count} got={len(vectors)}"
        )
    for vec in vectors:
        if len(vec) != dim:
            raise EmbeddingError(f"embedding dim mismatch expected={dim} got={len(vec)}")
        if any(not math.isfinite(x) for x in vec):
            raise EmbeddingError("non-finite remote embedding")
    return vectors


def assert_vectors_match_configured_dim(vectors: list[list[float]], dim: int) -> None:
    """Fail closed when write/query dim drifts from configured EMBEDDING_DIM."""
    if dim < 2:
        raise EmbeddingError("embedding dim must be >= 2")
    for i, vec in enumerate(vectors):
        if len(vec) != dim:
            raise EmbeddingError(
                f"embedding dim mismatch at index={i} expected={dim} got={len(vec)}"
            )
        if any(not math.isfinite(x) for x in vec):
            raise EmbeddingError(f"non-finite embedding at index={i}")


class OpenAICompatibleEmbedding:
    """HTTPS embedding client for OpenAI / DashScope compatible-mode endpoints.

    Requires API key. Failures raise EmbeddingError — never returns Mock vectors.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dim: int,
        timeout: float = _DEFAULT_TIMEOUT,
        max_batch: int = _MAX_BATCH,
    ) -> None:
        if not api_key:
            raise EmbeddingError("api_key required")
        if dim < 2:
            raise EmbeddingError("embedding dim must be >= 2")
        if max_batch < 1:
            raise EmbeddingError("max_batch must be >= 1")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._timeout = timeout
        self._max_batch = max_batch

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if texts is None:
            raise EmbeddingError("texts is required")
        if not texts:
            return []
        if len(texts) > self._max_batch:
            raise EmbeddingError(f"embedding batch too large max={self._max_batch} got={len(texts)}")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = {"model": self._model, "input": texts}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self._base_url}/embeddings", headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            logger.error("embedding timeout model=%s", self._model)
            raise EmbeddingError("embedding request timed out") from exc
        except httpx.HTTPError as exc:
            logger.error("embedding transport failed model=%s err=%s", self._model, type(exc).__name__)
            raise EmbeddingError("embedding request failed") from exc

        if resp.status_code in {401, 403}:
            logger.error("embedding auth failed status=%s model=%s", resp.status_code, self._model)
            raise EmbeddingError("embedding authentication failed")
        if resp.status_code == 429:
            logger.error("embedding rate limited model=%s", self._model)
            raise EmbeddingError("embedding rate limited")
        if resp.status_code >= 500:
            logger.error("embedding upstream error status=%s model=%s", resp.status_code, self._model)
            raise EmbeddingError("embedding upstream error")
        if resp.status_code >= 400:
            logger.error("embedding client error status=%s model=%s", resp.status_code, self._model)
            raise EmbeddingError(f"embedding request rejected status={resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise EmbeddingError("embedding response is not JSON") from exc
        items = sorted(data.get("data") or [], key=lambda row: row.get("index", 0))
        vectors = [list(item.get("embedding") or []) for item in items]
        return _validate_vectors(vectors, dim=self._dim, expected_count=len(texts))


def _resolved_embedding_mode() -> str:
    settings = get_settings()
    # Prefer EMBEDDING_PROVIDER; fall back to legacy EMBEDDING_MODE.
    provider = (getattr(settings, "embedding_provider", None) or "").strip().lower()
    mode = (settings.embedding_mode or "").strip().lower()
    chosen = provider or mode or "mock"
    if chosen == "stub":
        return "mock"
    return chosen


def get_embedding_provider() -> EmbeddingProvider:
    """Factory: mock offline by default; openai/dashscope require keys and never silent-fallback."""
    settings = get_settings()
    mode = _resolved_embedding_mode()
    dim = settings.embedding_dim
    vector_backend = settings.effective_vector_store_backend()
    if mode == "mock":
        if settings.is_production and vector_backend == "milvus":
            raise EmbeddingError("mock/stub embedding forbidden when VECTOR_STORE_BACKEND=milvus in production")
        return MockEmbeddingProvider(dim=dim)
    if mode == "openai":
        key = (settings.openai_api_key or "").strip()
        if not key:
            raise EmbeddingError("OPENAI_API_KEY required for embedding_provider=openai")
        return OpenAICompatibleEmbedding(
            api_key=key,
            base_url="https://api.openai.com/v1",
            model=settings.embedding_model or "text-embedding-3-small",
            dim=dim,
        )
    if mode == "dashscope":
        key = (settings.dashscope_api_key or "").strip()
        if not key:
            raise EmbeddingError("DASHSCOPE_API_KEY required for embedding_provider=dashscope")
        return OpenAICompatibleEmbedding(
            api_key=key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=settings.embedding_model or "text-embedding-v3",
            dim=dim,
        )
    raise EmbeddingError(f"unknown embedding provider/mode={mode}")
