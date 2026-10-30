"""System component cards: mode, configured?, connected?, never silent-healthy."""

from __future__ import annotations

from app.admin.demo_provider import demo_enabled
from app.admin.meta import envelope, utc_now_iso
from app.core.config import get_settings


def _mask_flag(configured: bool) -> str:
    return "configured" if configured else "not_configured"


def list_components() -> dict:
    settings = get_settings()
    checked_at = utc_now_iso()
    vector = settings.effective_vector_store_backend()
    emb = settings.embedding_provider or settings.embedding_mode or "mock"

    postgres_status = "not_configured"
    postgres_ok = None
    if settings.storage_backend == "postgres":
        from app.infra.db import db_healthy

        postgres_ok = db_healthy()
        postgres_status = "ready" if postgres_ok else "unreachable"
    elif settings.storage_backend == "memory":
        postgres_status = "memory_mode"

    redis_status = "not_configured"
    redis_ok = None
    if settings.redis_url:
        from app.infra.redis_client import redis_healthy

        redis_ok = redis_healthy()
        redis_status = "ready" if redis_ok else "unreachable"

    milvus_status = "not_configured"
    milvus_ok = None
    if vector == "milvus":
        from app.storage.milvus_store import milvus_healthy

        milvus_ok = milvus_healthy()
        milvus_status = "ready" if milvus_ok else "unreachable"
    elif vector == "mock":
        milvus_status = "mock_in_process"

    minio_status = "not_configured"
    if settings.minio_endpoint:
        minio_status = "configured_unverified"
    else:
        minio_status = "memory_or_local_disk"

    checkpointer = getattr(settings, "checkpointer_backend", None) or (
        "postgres" if settings.storage_backend == "postgres" else "memory"
    )

    cards = [
        {
            "id": "llm",
            "title": "大模型服务",
            "mode": settings.agentscope_model_mode,
            "configured": bool(settings.openai_api_key or settings.dashscope_api_key),
            "configured_label": _mask_flag(bool(settings.openai_api_key or settings.dashscope_api_key)),
            "connected": None,
            "status": "stub" if settings.agentscope_model_mode == "stub" else "live_credentials_masked",
            "note": "密钥不回显；live 未配密钥时对话不得假装成功",
        },
        {
            "id": "agent_runtime",
            "title": "Agent 运行时",
            "mode": settings.agent_runtime,
            "configured": True,
            "connected": True,
            "status": "agentscope" if settings.agentscope_enabled else settings.agent_runtime,
            "note": f"agentscope_enabled={settings.agentscope_enabled}",
        },
        {
            "id": "workflow",
            "title": "工作流引擎与 Checkpointer",
            "mode": settings.workflow_engine,
            "configured": True,
            "connected": None,
            "status": f"checkpointer={checkpointer}",
            "visualizer_mode": getattr(settings, "workflow_visualizer_mode", "mock"),
            "note": "Checkpointer 崩溃恢复未在本环境宣称通过",
        },
        {
            "id": "embedding",
            "title": "Embedding 服务",
            "mode": emb,
            "configured": emb not in {"mock", "stub"} and bool(
                settings.openai_api_key or settings.dashscope_api_key
            ),
            "connected": None if emb not in {"mock", "stub"} else False,
            "status": "mock" if emb in {"mock", "stub"} else "provider_configured",
            "note": "Mock Embedding 非真实语义",
        },
        {
            "id": "vector",
            "title": "向量数据库",
            "mode": vector,
            "configured": vector == "milvus" and bool(settings.milvus_uri),
            "connected": milvus_ok,
            "status": milvus_status,
            "note": "milvus 失败不会静默回 Mock",
        },
        {
            "id": "object_storage",
            "title": "对象存储",
            "mode": "minio" if settings.minio_endpoint else "local_or_memory",
            "configured": bool(settings.minio_endpoint),
            "connected": None,
            "status": minio_status,
            "note": "configured≠healthy；未测连通性时不显示健康",
        },
        {
            "id": "postgres",
            "title": "PostgreSQL",
            "mode": settings.storage_backend,
            "configured": settings.storage_backend == "postgres",
            "connected": postgres_ok,
            "status": postgres_status,
        },
        {
            "id": "redis",
            "title": "Redis",
            "mode": "configured" if settings.redis_url else "off",
            "configured": bool(settings.redis_url),
            "connected": redis_ok,
            "status": redis_status,
        },
        {
            "id": "jobs",
            "title": "任务执行与后台作业",
            "mode": "in_process",
            "configured": False,
            "connected": False,
            "status": "not_integrated",
            "note": "无独立任务队列；预留配置",
        },
        {
            "id": "security",
            "title": "安全策略与登录限流",
            "mode": "enabled",
            "configured": True,
            "connected": True,
            "status": f"login_rl={settings.login_rate_limit_per_minute}/min",
            "note": "JWT/脱敏/工单加密运行中；密钥仅掩码",
        },
    ]
    return envelope(
        {
            "items": cards,
            "checked_at": checked_at,
            "admin_demo_mode": demo_enabled(),
            "app_env": settings.app_env,
        },
        source="settings+health_probes",
        is_demo=False,
        note="未配置服务显示 not_configured/未接入，不显示健康",
    )
