"""Runtime selection and adapter errors."""

from __future__ import annotations


class RuntimeConfigError(ValueError):
    """Invalid runtime id or conflicting feature flags."""


class RuntimeUnavailableError(RuntimeError):
    """Requested runtime is configured but not installed / not ready."""
