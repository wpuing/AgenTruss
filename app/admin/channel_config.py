"""Channel config persist (wecom/mp). Save ≠ connected; no fake message IO."""

from __future__ import annotations

import logging
from typing import Any

from app.admin.audit_events import record_admin_action
from app.admin.meta import envelope, utc_now_iso
from app.core.crypto import pack_secret_value, unpack_secret_value
from app.core.platform import Platform
from app.core.security import AuthContext

logger = logging.getLogger(__name__)

_CHANNEL_FIELDS: dict[str, frozenset[str]] = {
    "wecom": frozenset({"corp_id", "agent_id", "secret", "callback_token", "enabled"}),
    "mp": frozenset({"app_id", "app_secret", "token", "enabled"}),
}
_SECRET_FIELDS = frozenset({"secret", "app_secret", "callback_token", "token"})
_KV_PREFIX = "channel."


def _overlay(platform: Platform) -> dict[str, Any]:
    store = getattr(platform, "channel_overrides", None)
    if store is None:
        platform.channel_overrides = {}
        return platform.channel_overrides
    return store


def _kv_key(channel_id: str, field: str) -> str:
    return f"{_KV_PREFIX}{channel_id}.{field}"


def hydrate_channel_overrides(
    platform: Platform,
    rows: list[dict[str, Any]] | None = None,
) -> int:
    """Load channel.* keys from settings_kv into platform.channel_overrides.

    Optional ``rows`` shares one list_settings_kv() with component hydrate.
    """
    from app.infra.ticket_repo import persistence_enabled

    if not persistence_enabled():
        return 0
    if rows is None:
        from app.infra.entity_repo import list_settings_kv

        rows = list_settings_kv()
    overlay = _overlay(platform)
    n = 0
    for row in rows:
        key = row["key"]
        if not key.startswith(_KV_PREFIX):
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            continue
        _, channel_id, field = parts
        raw = row["value"]
        if row.get("is_secret"):
            try:
                value = unpack_secret_value(str(raw))
            except Exception as exc:  # noqa: BLE001
                logger.warning("channel kv decrypt failed key=%s err=%s", key, type(exc).__name__)
                continue
        else:
            value = raw
        bucket = overlay.setdefault(channel_id, {})
        bucket[field] = {"value": value, "is_secret": field in _SECRET_FIELDS, "version": row.get("version", 1)}
        n += 1
    return n


def _channel_plain(platform: Platform, channel_id: str) -> dict[str, Any]:
    overlay = _overlay(platform)
    bucket = overlay.get(channel_id) or {}
    return {k: v.get("value") for k, v in bucket.items() if isinstance(v, dict)}


def enrich_channel_items(platform: Platform, items: list[dict]) -> list[dict]:
    """Merge saved overlay into catalog channel cards (never claim live connected)."""
    out: list[dict] = []
    for item in items:
        row = dict(item)
        cid = row.get("id")
        if cid in _CHANNEL_FIELDS:
            plain = _channel_plain(platform, str(cid))
            required = {"corp_id", "agent_id"} if cid == "wecom" else {"app_id"}
            secrets_ok = any(
                plain.get(s) for s in (_SECRET_FIELDS & _CHANNEL_FIELDS[str(cid)])
            )
            configured = all(plain.get(k) for k in required) and secrets_ok
            row["configured"] = configured
            row["enabled"] = bool(plain.get("enabled")) if "enabled" in plain else bool(row.get("enabled"))
            if configured:
                row["mode"] = "config_saved_not_connected"
                row["status"] = "saved_unverified"
            else:
                row["mode"] = "not_configured"
                row["status"] = "not_configured"
            row["connected"] = False
            row["note"] = (
                "配置可保存；无真实企微/公众号协议与回调，禁止显示已接通。"
                if cid != "web"
                else row.get("note")
            )
            # Safe field summary for UI
            fields: dict[str, Any] = {}
            for key in sorted(_CHANNEL_FIELDS[str(cid)]):
                if key in _SECRET_FIELDS:
                    present = bool(plain.get(key))
                    fields[key] = {"configured": present, "masked": "********" if present else ""}
                else:
                    fields[key] = plain.get(key)
            row["fields"] = fields
        out.append(row)
    return out


def get_channel_detail(platform: Platform, channel_id: str) -> dict:
    from app.admin.catalog import list_channels

    base = list_channels()
    items = enrich_channel_items(platform, list(base.get("items") or []))
    row = next((c for c in items if c.get("id") == channel_id), None)
    if not row:
        raise KeyError(channel_id)
    row = dict(row)
    row["writable"] = sorted(_CHANNEL_FIELDS.get(channel_id, frozenset()) - _SECRET_FIELDS)
    row["writable_secrets"] = sorted(_CHANNEL_FIELDS.get(channel_id, frozenset()) & _SECRET_FIELDS)
    return envelope(row, source="channel_overrides", is_demo=False)


def save_channel_config(
    platform: Platform,
    auth: AuthContext,
    channel_id: str,
    body: dict[str, Any],
) -> dict:
    allowed = _CHANNEL_FIELDS.get(channel_id)
    if allowed is None:
        raise KeyError(channel_id)
    if channel_id == "web":
        raise ValueError("web channel is built-in and not writable here")

    overlay = _overlay(platform)
    bucket = overlay.setdefault(channel_id, {})
    changed: list[str] = []
    for key, value in body.items():
        if key not in allowed:
            raise ValueError(f"field not allowed: {key}")
        if key in _SECRET_FIELDS:
            if value is None or (isinstance(value, str) and value.strip() == ""):
                continue
            if not isinstance(value, str) or len(value) > 512:
                raise ValueError(f"invalid secret {key}")
            _store(platform, bucket, channel_id, key, value, is_secret=True, actor=auth.user_id)
            changed.append(key)
            continue
        if key == "enabled":
            value = bool(value)
        elif value is not None:
            value = str(value).strip()[:256]
        _store(platform, bucket, channel_id, key, value, is_secret=False, actor=auth.user_id)
        changed.append(key)

    record_admin_action(
        platform,
        auth,
        "channel_config_save",
        {"channel_id": channel_id, "changed": changed},
    )
    detail = get_channel_detail(platform, channel_id)
    detail["saved"] = True
    detail["changed"] = changed
    detail["note"] = "saved≠connected；企微/公众号消息收发未接入"
    return detail


def test_channel_connection(platform: Platform, auth: AuthContext, channel_id: str) -> dict:
    """Honest probe: never claim live messaging works."""
    detail = get_channel_detail(platform, channel_id)
    configured = bool(detail.get("configured"))
    if channel_id == "web":
        result = {
            "channel_id": channel_id,
            "ok": True,
            "status": "live_api",
            "detail": "Web/Admin 已由本进程提供",
            "checked_at": utc_now_iso(),
        }
    elif not configured:
        result = {
            "channel_id": channel_id,
            "ok": False,
            "status": "not_configured",
            "detail": "缺少必要配置或密钥",
            "checked_at": utc_now_iso(),
        }
    else:
        result = {
            "channel_id": channel_id,
            "ok": False,
            "status": "protocol_not_integrated",
            "detail": "配置已保存，但无真实渠道 API/回调实现，测连不伪造成功",
            "checked_at": utc_now_iso(),
        }
    record_admin_action(
        platform,
        auth,
        "channel_test_connect",
        {"channel_id": channel_id, "status": result["status"], "ok": result["ok"]},
    )
    return envelope(result, source="channel_probe", is_demo=False)


def _store(
    platform: Platform,
    bucket: dict[str, Any],
    channel_id: str,
    key: str,
    value: Any,
    *,
    is_secret: bool,
    actor: str,
) -> None:
    version = int((bucket.get(key) or {}).get("version") or 0) + 1
    bucket[key] = {"value": value, "is_secret": is_secret, "version": version}
    from app.infra.ticket_repo import persistence_enabled

    if not persistence_enabled():
        return
    from app.infra.entity_repo import upsert_settings_kv

    stored: Any = pack_secret_value(value) if is_secret else value
    upsert_settings_kv(
        _kv_key(channel_id, key),
        stored,
        is_secret=is_secret,
        version=version,
        updated_by=actor,
    )
