"""Paths and runtime settings."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

_WEAK_SECRETS = frozenset(
    {
        "",
        "changeme",
        "secret",
        "jwt_secret",
        "password",
        "123456",
        "dev",
        "test",
    }
)


class Settings(BaseSettings):
    """Environment-backed settings. Secrets stay placeholders in examples."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = "changeme"
    ticket_args_secret: str = ""
    demo_password: str = "changeme"
    app_env: str = "development"
    allow_demo_users: bool | None = None
    jwt_expire_minutes: int = Field(default=480, ge=1, le=60 * 24 * 30)
    rate_limit_per_minute: int = 20
    login_rate_limit_per_minute: int = Field(default=10, ge=1, le=120)
    message_hard_limit: int = 2000
    max_upload_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)

    # memory = process dicts (dev/test); postgres = durable (required in production).
    storage_backend: str = "memory"
    database_url: str = ""
    redis_url: str = ""
    redis_required: bool = False

    # Workflow: langgraph (default when installed) | linear (legacy turn.py only).
    workflow_engine: str = "langgraph"

    agent_runtime: str = "single"
    agentscope_enabled: bool = False
    agentscope_team_config: str = ""
    agentscope_timeout_ms: int = 20_000
    agentscope_model_mode: str = "stub"

    # Admin console: demo fixtures / visualizer drafts (never silent live fallback).
    admin_demo_mode: bool = False
    # mock = allow demo visualizer drafts; langgraph = prefer real stage rail only.
    workflow_visualizer_mode: str = "mock"
    # memory | postgres — aligns with LangGraph checkpointer selection.
    checkpointer_backend: str = "memory"

    postgres_password: str = ""
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "agentruss"
    milvus_uri: str = ""
    # Legacy alias: memory|milvus. Prefer VECTOR_STORE_BACKEND.
    rag_backend: str = "mock"
    # mock (default, in-process) | milvus (real client; fails loud, no silent mock)
    vector_store_backend: str = "mock"
    # Legacy EMBEDDING_MODE; prefer EMBEDDING_PROVIDER=mock|openai|dashscope
    embedding_mode: str = "mock"  # mock|stub|openai|dashscope
    embedding_provider: str = "mock"
    embedding_model: str = "text-embedding-v3"
    embedding_dim: int = Field(default=8, ge=2, le=4096)
    openai_api_key: str = ""
    dashscope_api_key: str = ""

    @field_validator(
        "app_env",
        "storage_backend",
        "workflow_engine",
        "agentscope_model_mode",
        "rag_backend",
        "embedding_mode",
        "embedding_provider",
        "vector_store_backend",
        "workflow_visualizer_mode",
        "checkpointer_backend",
    )
    @classmethod
    def normalize_lower(cls, value: str) -> str:
        return (value or "").strip().lower()

    @field_validator("storage_backend")
    @classmethod
    def validate_storage(cls, value: str) -> str:
        if value not in {"memory", "postgres"}:
            raise ValueError("storage_backend must be memory or postgres")
        return value

    @field_validator("workflow_engine")
    @classmethod
    def validate_workflow(cls, value: str) -> str:
        if value not in {"langgraph", "linear"}:
            raise ValueError("workflow_engine must be langgraph or linear")
        return value

    @field_validator("agentscope_model_mode")
    @classmethod
    def validate_model_mode(cls, value: str) -> str:
        if value not in {"stub", "live"}:
            raise ValueError("agentscope_model_mode must be stub or live")
        return value

    @field_validator("workflow_visualizer_mode")
    @classmethod
    def validate_visualizer(cls, value: str) -> str:
        if value not in {"mock", "langgraph"}:
            raise ValueError("workflow_visualizer_mode must be mock or langgraph")
        return value

    @field_validator("checkpointer_backend")
    @classmethod
    def validate_checkpointer(cls, value: str) -> str:
        if value not in {"memory", "postgres"}:
            raise ValueError("checkpointer_backend must be memory or postgres")
        return value

    @field_validator("rag_backend")
    @classmethod
    def validate_rag_backend(cls, value: str) -> str:
        # memory/stub accepted as aliases of mock for older .env files
        if value not in {"memory", "mock", "stub", "milvus"}:
            raise ValueError("rag_backend must be mock|memory|milvus")
        if value in {"memory", "stub"}:
            return "mock"
        return value

    @field_validator("vector_store_backend")
    @classmethod
    def validate_vector_store(cls, value: str) -> str:
        if value not in {"mock", "memory", "stub", "milvus"}:
            raise ValueError("vector_store_backend must be mock or milvus")
        if value in {"memory", "stub"}:
            return "mock"
        return value

    @field_validator("embedding_mode", "embedding_provider")
    @classmethod
    def validate_embedding_mode(cls, value: str) -> str:
        if value not in {"mock", "stub", "openai", "dashscope"}:
            raise ValueError("embedding provider/mode must be mock, stub, openai, or dashscope")
        return "mock" if value == "stub" else value

    @model_validator(mode="after")
    def resolve_demo_flag(self) -> Settings:
        if self.allow_demo_users is None:
            object.__setattr__(self, "allow_demo_users", self.app_env == "development")
        # Align legacy rag_backend into vector_store_backend when unset-looking defaults collide.
        vs = self.vector_store_backend or "mock"
        if self.rag_backend == "milvus" and vs == "mock":
            # Explicit milvus via RAG_BACKEND wins if VECTOR_STORE_BACKEND left default mock
            # only when user set RAG_BACKEND=milvus in env — handled by both fields usually.
            pass
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env in {"production", "prod"}

    @property
    def is_development(self) -> bool:
        return self.app_env in {"development", "dev", "test"} or not self.is_production

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    def jwt_secret_is_weak(self) -> bool:
        secret = (self.jwt_secret or "").strip()
        if secret.lower() in _WEAK_SECRETS:
            return True
        if len(secret) < 32:
            return True
        return False

    def validate_runtime(self) -> None:
        """Fail fast when production (or misconfigured) secrets are unsafe."""
        if self.is_production:
            if self.jwt_secret_is_weak():
                raise RuntimeError(
                    "production requires a strong JWT_SECRET "
                    "(length >= 32, not a known placeholder)"
                )
            if self.allow_demo_users:
                raise RuntimeError(
                    "production forbids ALLOW_DEMO_USERS=true; "
                    "demo accounts must not run in production"
                )
            if self.admin_demo_mode:
                raise RuntimeError("production forbids ADMIN_DEMO_MODE=true")
            if self.storage_backend != "postgres":
                raise RuntimeError(
                    "production requires STORAGE_BACKEND=postgres "
                    "(silent memory fallback is forbidden)"
                )
            if not (self.database_url or "").strip():
                raise RuntimeError("production requires DATABASE_URL")
            if not (self.redis_url or "").strip():
                raise RuntimeError("production requires REDIS_URL")
            if self.postgres_password and self.postgres_password.strip().lower() in _WEAK_SECRETS:
                raise RuntimeError("production POSTGRES_PASSWORD must not be a weak placeholder")
            # Ticket arg encryption must not use a weak derived key in production.
            ticket_secret = (self.ticket_args_secret or "").strip()
            if not ticket_secret and self.jwt_secret_is_weak():
                raise RuntimeError("production requires TICKET_ARGS_SECRET (or a strong JWT_SECRET)")
            if ticket_secret and (
                ticket_secret.lower() in _WEAK_SECRETS or len(ticket_secret) < 16
            ):
                raise RuntimeError("production TICKET_ARGS_SECRET is too weak")
        if self.storage_backend == "postgres" and not (self.database_url or "").strip():
            raise RuntimeError("STORAGE_BACKEND=postgres requires DATABASE_URL")
        vector_backend = self.effective_vector_store_backend()
        emb = self.embedding_provider or self.embedding_mode or "mock"
        if vector_backend == "milvus" and not (self.milvus_uri or "").strip():
            raise RuntimeError("VECTOR_STORE_BACKEND=milvus requires MILVUS_URI (no silent Mock fallback)")
        if self.is_production and vector_backend == "milvus":
            if emb in {"mock", "stub"}:
                raise RuntimeError("production milvus forbids EMBEDDING_PROVIDER=mock/stub")
            if emb == "openai" and not (self.openai_api_key or "").strip():
                raise RuntimeError("production openai embedding requires OPENAI_API_KEY")
            if emb == "dashscope" and not (self.dashscope_api_key or "").strip():
                raise RuntimeError("production dashscope embedding requires DASHSCOPE_API_KEY")

    def effective_vector_store_backend(self) -> str:
        """Resolve VECTOR_STORE_BACKEND with legacy RAG_BACKEND=milvus override."""
        vs = (self.vector_store_backend or "mock").lower()
        rag = (self.rag_backend or "mock").lower()
        if rag == "milvus":
            return "milvus"
        if vs in {"memory", "stub"}:
            return "mock"
        return vs


def get_settings() -> Settings:
    """Return settings loaded from the environment."""
    return Settings()


def agentscope_timeout_from_env() -> bool:
    """True when AGENTSCOPE_TIMEOUT_MS is explicitly present in the process env."""
    return "AGENTSCOPE_TIMEOUT_MS" in os.environ
