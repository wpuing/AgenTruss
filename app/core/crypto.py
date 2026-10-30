"""Symmetric encryption for sensitive payloads (tool args at rest).

Key material comes from TICKET_ARGS_SECRET (preferred) or JWT_SECRET.
Production must not fall back to weak/empty secrets for plaintext storage.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _secret_material() -> bytes:
    settings = get_settings()
    raw = (settings.ticket_args_secret or "").strip()
    if not raw:
        raw = (settings.jwt_secret or "").strip()
    if settings.is_production:
        if not (settings.ticket_args_secret or "").strip() and settings.jwt_secret_is_weak():
            raise RuntimeError("production requires TICKET_ARGS_SECRET or strong JWT_SECRET for arg encryption")
        if not raw or len(raw) < 16:
            raise RuntimeError("production ticket encryption secret too weak")
    if not raw:
        raw = "dev-only-ticket-args-secret"
    return raw.encode("utf-8")


def _fernet() -> Fernet:
    digest = hashlib.sha256(_secret_material()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(payload: dict[str, Any]) -> str:
    """Encrypt a JSON-serializable dict; returns url-safe token string."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _fernet().encrypt(blob).decode("utf-8")


def decrypt_json(token: str) -> dict[str, Any]:
    """Decrypt token produced by encrypt_json."""
    try:
        raw = _fernet().decrypt(token.encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("cannot decrypt payload") from exc
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload must be object")
    return data


def pack_secret_value(value: Any) -> str:
    """Encrypt a scalar secret for settings_kv / channel overlays (wrapper around encrypt_json)."""
    return encrypt_json({"v": value})


def unpack_secret_value(token: str) -> Any:
    """Decrypt pack_secret_value output; raises ValueError on bad token/shape."""
    data = decrypt_json(token)
    if "v" not in data:
        raise ValueError("secret payload missing v")
    return data.get("v")


def mask_tool_args(args: dict[str, Any]) -> dict[str, Any]:
    """Delegate to unified redaction (kept for backward imports)."""
    from app.security.redaction import mask_tool_args as _mask

    return _mask(args)


def args_integrity_digest(args_encrypted: str | None) -> str:
    """HMAC digest binding approved ciphertext — detect post-approval tampering."""
    import hmac

    material = _secret_material()
    blob = (args_encrypted or "").encode("utf-8")
    return hmac.new(material, blob, hashlib.sha256).hexdigest()


def verify_args_integrity(args_encrypted: str | None, digest: str | None) -> bool:
    """Constant-time compare of stored digest vs recomputed ciphertext digest."""
    import hmac

    if not digest:
        return False
    expected = args_integrity_digest(args_encrypted)
    return hmac.compare_digest(expected, digest)
