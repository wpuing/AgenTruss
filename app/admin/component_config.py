"""Component config persist + test-connect (save ≠ connected).

Non-secret modes and encrypted secrets land in settings_kv when postgres is on;
memory mode keeps an in-process overlay only (honest for tests).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.admin.audit_events import record_admin_action
from app.admin.meta import envelope, utc_now_iso
from app.core.config import get_settings
from app.core.crypto import pack_secret_value, unpack_secret_value
from app.core.platform import Platform
from app.core.security import AuthContext

logger = logging.getLogger(__name__)

_SECRET_KEYS = frozenset(
    {
        "openai_api_key",
        "dashscope_api_key",
        "minio_secret_key",
        "minio_access_key",
        "redis_url",
    }
)

_COMPONENT_FIELDS: dict[str, frozenset[str]] = {
    "llm": frozenset({"agentscope_model_mode", "openai_api_key", "dashscope_api_key"}),
    "embedding": frozenset(
        {
            "embedding_provider",
            "embedding_model",
            "embedding_dim",
            "openai_api_key",
            "dashscope_api_key",
        }
    ),
    "vector": frozenset({"vector_store_backend", "milvus_uri"}),
    "object_storage": frozenset(
        {"minio_endpoint", "minio_access_key", "minio_secret_key", "minio_bucket"}
    ),
    "redis": frozenset({"redis_url"}),
    "agent_runtime": frozenset({"agent_runtime", "agentscope_enabled"}),
    "workflow": frozenset({"workflow_engine", "workflow_visualizer_mode", "checkpointer_backend"}),
    "security": frozenset({"login_rate_limit_per_minute", "rate_limit_per_minute"}),
    "postgres": frozenset(),
    "jobs": frozenset(),
}

_ENV_MAP = {
    "agentscope_model_mode": "AGENTSCOPE_MODEL_MODE",
    "openai_api_key": "OPENAI_API_KEY",
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    "embedding_provider": "EMBEDDING_PROVIDER",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_dim": "EMBEDDING_DIM",
    "vector_store_backend": "VECTOR_STORE_BACKEND",
    "milvus_uri": "MILVUS_URI",
    "minio_endpoint": "MINIO_ENDPOINT",
    "minio_access_key": "MINIO_ACCESS_KEY",
    "minio_secret_key": "MINIO_SECRET_KEY",
    "minio_bucket": "MINIO_BUCKET",
    "redis_url": "REDIS_URL",
    "agent_runtime": "AGENT_RUNTIME",
    "agentscope_enabled": "AGENTSCOPE_ENABLED",
    "workflow_engine": "WORKFLOW_ENGINE",
    "workflow_visualizer_mode": "WORKFLOW_VISUALIZER_MODE",
    "checkpointer_backend": "CHECKPOINTER_BACKEND",
    "login_rate_limit_per_minute": "LOGIN_RATE_LIMIT_PER_MINUTE",
    "rate_limit_per_minute": "RATE_LIMIT_PER_MINUTE",
}

_ALLOWED_MODES = {
    "agentscope_model_mode": {"stub", "live"},
    "embedding_provider": {"mock", "openai", "dashscope"},
    "vector_store_backend": {"mock", "milvus"},
    "agent_runtime": {"single", "agentscope_team"},
    "workflow_engine": {"langgraph", "linear"},
    "workflow_visualizer_mode": {"mock", "langgraph"},
    "checkpointer_backend": {"memory", "postgres"},
}


def _overlay(platform: Platform) -> dict[str, Any]:
    store = getattr(platform, "component_overrides", None)
    if store is None:
        platform.component_overrides = {}
        return platform.component_overrides
    return store


def apply_overrides_to_env(overrides: dict[str, Any]) -> None:
    """Push overrides into process env so get_settings() sees them."""
    for key, value in overrides.items():
        env_name = _ENV_MAP.get(key)
        if not env_name or value is None:
            continue
        if isinstance(value, bool):
            os.environ[env_name] = "true" if value else "false"
        else:
            os.environ[env_name] = str(value)


def hydrate_component_overrides(
    platform: Platform,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    """Load settings_kv into platform overlay + env. Returns loaded (non-channel) count.

    Pass ``rows`` from a shared list_settings_kv() call to avoid a second DB scan
    when hydrating channels in the same startup path.
    """
    from app.infra.ticket_repo import persistence_enabled

    if not persistence_enabled():
        return 0
    if rows is None:
        from app.infra.entity_repo import list_settings_kv

        rows = list_settings_kv()
    overlay = _overlay(platform)
    plain: dict[str, Any] = {}
    loaded = 0
    for row in rows:
        key = row["key"]
        # Channel overlays use channel.* prefix — keep them out of component env map.
        if key.startswith("channel."):
            continue
        raw = row["value"]
        if row.get("is_secret"):
            try:
                plain[key] = unpack_secret_value(str(raw))
            except Exception as exc:  # noqa: BLE001
                logger.warning("settings_kv decrypt failed key=%s err=%s", key, type(exc).__name__)
                continue
        else:
            plain[key] = raw
        overlay[key] = {
            "value": plain[key],
            "is_secret": key in _SECRET_KEYS,
            "version": row.get("version", 1),
        }
        loaded += 1
    apply_overrides_to_env(plain)
    return loaded


def get_component_detail(platform: Platform, component_id: str) -> dict:
    """Safe detail for one component (secrets masked)."""
    from app.admin.components import list_components

    cards = list_components()
    item = next((c for c in cards.get("items") or [] if c.get("id") == component_id), None)
    if not item:
        raise KeyError(component_id)
    overlay = _overlay(platform)
    settings = get_settings()
    fields: dict[str, Any] = {}
    for key in sorted(_COMPONENT_FIELDS.get(component_id, frozenset())):
        if key in _SECRET_KEYS:
            present = bool(overlay.get(key) and overlay[key].get("value")) or bool(
                getattr(settings, key, None)
            )
            fields[key] = {"configured": present, "masked": "********" if present else ""}
        elif key in overlay and overlay[key].get("value") is not None:
            fields[key] = overlay[key]["value"]
        else:
            fields[key] = getattr(settings, key, None)
    out = dict(item)
    out["fields"] = fields
    out["writable"] = sorted(_COMPONENT_FIELDS.get(component_id, frozenset()) - _SECRET_KEYS)
    out["writable_secrets"] = sorted(_COMPONENT_FIELDS.get(component_id, frozenset()) & _SECRET_KEYS)
    return envelope(out, source="settings+overrides", is_demo=False)


def save_component_config(
    platform: Platform,
    auth: AuthContext,
    component_id: str,
    body: dict[str, Any],
) -> dict:
    """Persist allowed fields. Empty secret string = keep existing."""
    allowed = _COMPONENT_FIELDS.get(component_id)
    if allowed is None:
        raise KeyError(component_id)
    if not allowed:
        raise ValueError(f"component {component_id} is status-only (not writable)")

    overlay = _overlay(platform)
    changed: list[str] = []
    for key, value in body.items():
        if key not in allowed:
            raise ValueError(f"field not allowed for {component_id}: {key}")
        if key in _SECRET_KEYS:
            if value is None or (isinstance(value, str) and value.strip() == ""):
                continue
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError(f"invalid secret for {key}")
            _store_value(platform, overlay, key, value, is_secret=True, actor=auth.user_id)
            changed.append(key)
            continue
        if key in _ALLOWED_MODES and value not in _ALLOWED_MODES[key]:
            raise ValueError(f"invalid {key}: {value}")
        if key in {"embedding_dim", "login_rate_limit_per_minute", "rate_limit_per_minute"}:
            value = int(value)
            if key == "embedding_dim" and not (2 <= value <= 4096):
                raise ValueError("embedding_dim out of range")
            if "rate_limit" in key and not (1 <= value <= 10_000):
                raise ValueError(f"{key} out of range")
        if key == "agentscope_enabled":
            value = bool(value)
        if key in {"milvus_uri", "minio_endpoint", "minio_bucket", "embedding_model"} and value is not None:
            value = str(value).strip()[:512]
        _store_value(platform, overlay, key, value, is_secret=False, actor=auth.user_id)
        changed.append(key)

    plain = {k: overlay[k]["value"] for k in overlay if overlay[k].get("value") is not None}
    apply_overrides_to_env(plain)
    record_admin_action(
        platform,
        auth,
        "component_config_save",
        {"component_id": component_id, "changed": changed},
    )
    detail = get_component_detail(platform, component_id)
    detail["saved"] = True
    detail["changed"] = changed
    detail["note"] = "saved≠connected；请单独执行测试连接"
    return detail


def _store_value(
    platform: Platform,
    overlay: dict[str, Any],
    key: str,
    value: Any,
    *,
    is_secret: bool,
    actor: str,
) -> None:
    del platform
    version = int((overlay.get(key) or {}).get("version") or 0) + 1
    overlay[key] = {"value": value, "is_secret": is_secret, "version": version}
    from app.infra.ticket_repo import persistence_enabled

    if not persistence_enabled():
        return
    from app.infra.entity_repo import upsert_settings_kv

    stored: Any = pack_secret_value(value) if is_secret else value
    upsert_settings_kv(key, stored, is_secret=is_secret, version=version, updated_by=actor)


def test_component_connection(platform: Platform, auth: AuthContext, component_id: str) -> dict:
    """Run a minimal, side-effect-light probe. Never fake success."""
    settings = get_settings()
    checked_at = utc_now_iso()
    result: dict[str, Any] = {
        "component_id": component_id,
        "checked_at": checked_at,
        "ok": False,
        "status": "not_run",
        "detail": "",
    }

    try:
        if component_id == "llm":
            if settings.agentscope_model_mode == "stub":
                result.update(ok=True, status="stub_ok", detail="stub 模式无需外呼")
            elif not (settings.openai_api_key or settings.dashscope_api_key):
                result.update(status="not_configured", detail="live 需要 API Key")
            else:
                result.update(
                    ok=False,
                    status="credentials_present_unverified",
                    detail="密钥已配置；完整 Live 需 RUN_AGENTSCOPE_LIVE=1，本探针不伪造成功",
                )
        elif component_id == "embedding":
            emb = settings.embedding_provider or settings.embedding_mode or "mock"
            if emb in {"mock", "stub"}:
                from app.rag.embedding import get_embedding_provider

                vec = get_embedding_provider().embed_one("ping")
                result.update(ok=True, status="mock_ok", detail=f"mock embed dim={len(vec)}")
            elif not (settings.openai_api_key or settings.dashscope_api_key):
                result.update(status="not_configured", detail="缺少 API Key")
            else:
                from app.rag.embedding import get_embedding_provider

                try:
                    vec = get_embedding_provider().embed_one("connectivity-probe")
                    dim = len(vec)
                    expected = settings.embedding_dim
                    if expected and dim != expected:
                        result.update(ok=False, status="dim_mismatch", detail=f"got={dim} expected={expected}")
                    else:
                        result.update(ok=True, status="live_ok", detail=f"embed dim={dim}")
                except Exception as exc:  # noqa: BLE001
                    result.update(ok=False, status="connect_failed", detail=str(exc)[:200])
        elif component_id == "vector":
            if settings.effective_vector_store_backend() == "mock":
                result.update(ok=True, status="mock_ok", detail="in-process mock vector store")
            else:
                from app.storage.milvus_store import milvus_healthy

                ok = bool(milvus_healthy())
                result.update(ok=ok, status="ready" if ok else "unreachable", detail="milvus health probe")
        elif component_id == "postgres":
            if settings.storage_backend != "postgres":
                result.update(ok=True, status="memory_mode", detail="STORAGE_BACKEND=memory")
            else:
                from app.infra.db import db_healthy

                ok = bool(db_healthy())
                result.update(ok=ok, status="ready" if ok else "unreachable", detail="SELECT 1")
        elif component_id == "redis":
            if not settings.redis_url:
                result.update(status="not_configured", detail="REDIS_URL 未配置")
            else:
                from app.infra.redis_client import redis_healthy

                ok = bool(redis_healthy())
                result.update(ok=ok, status="ready" if ok else "unreachable", detail="PING")
        elif component_id == "object_storage":
            if not settings.minio_endpoint or not settings.minio_access_key:
                result.update(status="not_configured", detail="MINIO_ENDPOINT / 凭证未配置")
            else:
                from app.storage.minio_store import minio_healthy

                ok = bool(minio_healthy(timeout_sec=5.0))
                result.update(
                    ok=ok,
                    status="ready" if ok else "unreachable",
                    detail="bucket_exists 只读探针（无 put/delete）",
                )
        elif component_id in {"agent_runtime", "workflow", "security"}:
            result.update(ok=True, status="local_ok", detail="本地配置有效")
        elif component_id == "jobs":
            result.update(status="not_integrated", detail="无独立任务队列")
        else:
            result.update(status="unknown_component", detail=component_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("component test-connect failed id=%s", component_id)
        result.update(ok=False, status="error", detail=str(exc)[:200])

    record_admin_action(
        platform,
        auth,
        "component_test_connect",
        {"component_id": component_id, "status": result.get("status"), "ok": result.get("ok")},
    )
    return envelope(result, source="component_probe", is_demo=False)
