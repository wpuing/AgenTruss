"""Detect optional agentscope install without making it a hard dependency."""

from __future__ import annotations

from app.runtime.exceptions import RuntimeUnavailableError

_REQUIRED = "2.0.10"


def agentscope_available() -> bool:
    try:
        import agentscope  # noqa: F401

        return True
    except ImportError:
        return False


def require_agentscope():
    """Import and return the agentscope module, or raise RuntimeUnavailableError."""
    try:
        import agentscope
    except ImportError as exc:
        raise RuntimeUnavailableError(
            f"agentscope {_REQUIRED} is not installed; "
            'run: pip install -e ".[agentscope]"'
        ) from exc
    version = getattr(agentscope, "__version__", "unknown")
    if version != _REQUIRED:
        # Soft warn path: still allow if major.minor matches 2.0.x later; Phase 2 pins exact.
        if not str(version).startswith("2.0."):
            raise RuntimeUnavailableError(
                f"agentscope {version} is not supported; expected {_REQUIRED}"
            )
    return agentscope
