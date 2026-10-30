"""Admin config write/read routes: IAM users, components, channels, strategies.

Kept separate from the large http.py surface so authz + schema stay local
without changing URL paths or response contracts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import map_admin_write_error, need, need_any, platform_dep
from app.core.platform import Platform
from app.core.security import AuthContext
from app.infra.entity_repo import ConflictError

router = APIRouter()


class ChunkStrategyPatch(BaseModel):
    name: str | None = None
    chunk_size: int | None = Field(default=None, ge=1, le=5000)
    child_size: int | None = Field(default=None, ge=1, le=5000)
    overlap: int | None = Field(default=None, ge=0, le=1000)
    expected_version: int | None = Field(default=None, ge=1)


class RetrieveStrategyPatch(BaseModel):
    name: str | None = None
    vector_min_score: float | None = Field(default=None, ge=0, le=1)
    rerank_min_score: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    expected_version: int | None = Field(default=None, ge=1)


class ComponentSaveIn(BaseModel):
    """Partial component fields; empty secret strings keep existing values."""

    model_config = {"extra": "forbid"}

    agentscope_model_mode: str | None = None
    openai_api_key: str | None = None
    dashscope_api_key: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = Field(default=None, ge=2, le=4096)
    vector_store_backend: str | None = None
    milvus_uri: str | None = None
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str | None = None
    redis_url: str | None = None
    agent_runtime: str | None = None
    agentscope_enabled: bool | None = None
    workflow_engine: str | None = None
    workflow_visualizer_mode: str | None = None
    checkpointer_backend: str | None = None
    login_rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10_000)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10_000)


class IamUserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    roles: list[str] = Field(min_length=1, max_length=8)


class IamUserPatchIn(BaseModel):
    status: str | None = None
    roles: list[str] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class ChannelSaveIn(BaseModel):
    model_config = {"extra": "forbid"}

    corp_id: str | None = None
    agent_id: str | None = None
    secret: str | None = None
    callback_token: str | None = None
    app_id: str | None = None
    app_secret: str | None = None
    token: str | None = None
    enabled: bool | None = None


@router.get("/api/admin/users")
def list_users(auth: AuthContext = Depends(need("iam:user:view")), platform: Platform = Depends(platform_dep)) -> dict:
    from app.infra.ticket_repo import persistence_enabled

    if persistence_enabled():
        from app.infra.entity_repo import list_users as list_users_db

        return {"items": list_users_db(auth.tenant_id), "source": "postgres"}
    rows = [
        {
            "id": row["id"],
            "username": row["username"],
            "tenant_id": row["tenant_id"],
            "roles": row["roles"],
            "status": row["status"],
        }
        for row in platform.users.values()
        if row["tenant_id"] == auth.tenant_id
    ]
    return {"items": rows, "source": "memory"}


@router.get("/api/admin/users/{user_id}")
def get_user(
    user_id: str,
    auth: AuthContext = Depends(need("iam:user:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.iam_users import get_user as iam_get_user

    try:
        return iam_get_user(platform, auth, user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc


@router.post("/api/admin/users")
def create_user(
    body: IamUserCreateIn,
    auth: AuthContext = Depends(need("iam:user:create")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.iam_users import create_user as iam_create_user

    try:
        return iam_create_user(platform, auth, body.model_dump())
    except (PermissionError, ValueError) as exc:
        raise map_admin_write_error(exc, not_found="user not found") from exc


@router.patch("/api/admin/users/{user_id}")
def patch_user(
    user_id: str,
    body: IamUserPatchIn,
    auth: AuthContext = Depends(need("iam:user:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.iam_users import update_user as iam_update_user

    try:
        return iam_update_user(platform, auth, user_id, body.model_dump(exclude_none=True))
    except (KeyError, PermissionError, ValueError) as exc:
        raise map_admin_write_error(exc, not_found="user not found") from exc


@router.get("/api/admin/components")
def admin_components(
    auth: AuthContext = Depends(need_any("sys:basic:view", "sys:model:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    """Component config cards — secrets never returned."""
    del auth, platform
    from app.admin.components import list_components

    return list_components()


@router.get("/api/admin/components/{component_id}")
def admin_component_detail(
    component_id: str,
    auth: AuthContext = Depends(need_any("sys:model:view", "sys:basic:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    del auth
    from app.admin.component_config import get_component_detail

    try:
        return get_component_detail(platform, component_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="component not found") from exc


@router.put("/api/admin/components/{component_id}")
def admin_component_save(
    component_id: str,
    body: ComponentSaveIn,
    auth: AuthContext = Depends(need("sys:model:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.component_config import save_component_config

    payload = body.model_dump(exclude_none=True)
    try:
        return save_component_config(platform, auth, component_id, payload)
    except (KeyError, ValueError) as exc:
        raise map_admin_write_error(exc, not_found="component not found") from exc


@router.post("/api/admin/components/{component_id}/test-connection")
def admin_component_test(
    component_id: str,
    auth: AuthContext = Depends(need("sys:model:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.component_config import test_component_connection

    return test_component_connection(platform, auth, component_id)


@router.get("/api/admin/channels")
def admin_channels(
    auth: AuthContext = Depends(need("sys:channel:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import list_channels
    from app.admin.channel_config import enrich_channel_items

    del auth
    base = list_channels()
    items = enrich_channel_items(platform, list(base.get("items") or []))
    return {**base, "items": items}


@router.get("/api/admin/channels/{channel_id}")
def admin_channel_detail(
    channel_id: str,
    auth: AuthContext = Depends(need("sys:channel:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    del auth
    from app.admin.channel_config import get_channel_detail

    try:
        return get_channel_detail(platform, channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="channel not found") from exc


@router.put("/api/admin/channels/{channel_id}")
def admin_channel_save(
    channel_id: str,
    body: ChannelSaveIn,
    auth: AuthContext = Depends(need("sys:channel:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.channel_config import save_channel_config

    try:
        return save_channel_config(platform, auth, channel_id, body.model_dump(exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise map_admin_write_error(exc, not_found="channel not found") from exc


@router.post("/api/admin/channels/{channel_id}/test-connection")
def admin_channel_test(
    channel_id: str,
    auth: AuthContext = Depends(need("sys:channel:update")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.channel_config import test_channel_connection

    try:
        return test_channel_connection(platform, auth, channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="channel not found") from exc


@router.patch("/api/admin/chunk-strategies/{strategy_id}")
def patch_chunk_strategy(
    strategy_id: str,
    body: ChunkStrategyPatch,
    auth: AuthContext = Depends(need_any("kb:chunk_strategy:update", "kb:chunk_strategy:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import patch_strategy

    try:
        return patch_strategy(
            platform,
            "chunk",
            strategy_id,
            body.model_dump(exclude_none=True),
            actor_id=auth.user_id,
        )
    except (KeyError, ConflictError) as exc:
        raise map_admin_write_error(exc, not_found="strategy not found") from exc


@router.patch("/api/admin/retrieve-strategies/{strategy_id}")
def patch_retrieve_strategy(
    strategy_id: str,
    body: RetrieveStrategyPatch,
    auth: AuthContext = Depends(need_any("kb:retrieve_strategy:update", "kb:retrieve_strategy:view")),
    platform: Platform = Depends(platform_dep),
) -> dict:
    from app.admin.catalog import patch_strategy

    try:
        return patch_strategy(
            platform,
            "retrieve",
            strategy_id,
            body.model_dump(exclude_none=True),
            actor_id=auth.user_id,
        )
    except (KeyError, ConflictError) as exc:
        raise map_admin_write_error(exc, not_found="strategy not found") from exc
