"""Password hashing helpers (PBKDF2-SHA256). No plaintext compare in production paths."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SCHEME = "pbkdf2_sha256"
_ITERATIONS = 210_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return a salted PBKDF2 hash string."""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"{_SCHEME}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify password against hash. Legacy plaintext only in non-production."""
    if not stored:
        return False
    if stored.startswith(f"{_SCHEME}$"):
        try:
            _, iters_s, salt, hexdigest = stored.split("$", 3)
            iters = int(iters_s)
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iters)
        return hmac.compare_digest(digest.hex(), hexdigest)
    # Legacy plaintext — forbidden in production; development migration only.
    from app.core.config import get_settings

    if get_settings().is_production:
        return False
    return hmac.compare_digest(password, stored)
