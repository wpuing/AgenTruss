"""Quality phase: shared RBAC, secret pack/unpack, hydrate isolation, strategy CAS memory safety."""

from __future__ import annotations

import pytest

from app.core.crypto import pack_secret_value, unpack_secret_value
from app.core.platform import Platform
from app.core.rbac import ROLE_PERMS, perms_for_roles, perms_for_roles_lenient


def test_perms_for_roles_matches_catalog_and_rejects_unknown():
    admin = perms_for_roles(["tenant_admin"])
    assert admin == set(ROLE_PERMS["tenant_admin"])
    auditor = perms_for_roles(["auditor"])
    assert auditor == set(ROLE_PERMS["auditor"])
    assert auditor.issubset(admin)
    with pytest.raises(ValueError, match="unknown role"):
        perms_for_roles(["superuser"])
    assert perms_for_roles_lenient(["auditor", "legacy_role"]) == set(ROLE_PERMS["auditor"])


def test_pack_unpack_secret_roundtrip_and_missing_v():
    token = pack_secret_value("sk-test-value")
    assert unpack_secret_value(token) == "sk-test-value"
    assert "sk-test-value" not in token
    from app.core.crypto import encrypt_json

    bad = encrypt_json({"x": 1})
    with pytest.raises(ValueError, match="missing v"):
        unpack_secret_value(bad)


def test_component_hydrate_skips_channel_keys(monkeypatch):
    platform = Platform.seed()
    rows = [
        {"key": "embedding_provider", "value": "mock", "is_secret": False, "version": 2},
        {"key": "channel.wecom.secret", "value": pack_secret_value("corp-secret"), "is_secret": True, "version": 1},
        {"key": "openai_api_key", "value": pack_secret_value("sk-keep"), "is_secret": True, "version": 1},
    ]
    monkeypatch.setattr("app.infra.ticket_repo.persistence_enabled", lambda: True)
    calls = {"n": 0}

    def _list():
        calls["n"] += 1
        return rows

    monkeypatch.setattr("app.infra.entity_repo.list_settings_kv", _list)

    from app.admin.channel_config import hydrate_channel_overrides
    from app.admin.component_config import hydrate_component_overrides

    shared = _list()
    n = hydrate_component_overrides(platform, shared)
    ch = hydrate_channel_overrides(platform, shared)
    overlay = platform.component_overrides
    assert n == 2
    assert ch == 1
    assert calls["n"] == 1
    assert "channel.wecom.secret" not in overlay
    assert overlay["embedding_provider"]["value"] == "mock"
    assert overlay["openai_api_key"]["value"] == "sk-keep"
    assert platform.channel_overrides["wecom"]["secret"]["value"] == "corp-secret"


def test_strategy_conflict_does_not_dirty_memory_registry(monkeypatch):
    platform = Platform.seed()
    sid = next(iter(platform.chunk_strategies))
    before = dict(platform.chunk_strategies[sid])
    monkeypatch.setattr("app.infra.ticket_repo.persistence_enabled", lambda: True)

    from app.infra.entity_repo import ConflictError

    def boom(_payload):
        raise ConflictError("forced conflict")

    monkeypatch.setattr("app.infra.entity_repo.upsert_chunk_strategy", boom)

    from app.admin.catalog import patch_strategy

    with pytest.raises(ConflictError):
        patch_strategy(
            platform,
            "chunk",
            sid,
            {"chunk_size": 999, "expected_version": before.get("version", 1)},
            actor_id="usr-test",
        )
    after = platform.chunk_strategies[sid]
    assert after["chunk_size"] == before["chunk_size"]
    assert after["version"] == before["version"]
