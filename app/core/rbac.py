"""Role → permission expansion (single source for seed, hydrate, IAM writes)."""

from __future__ import annotations

from app.core.platform import ALL_PERMS, AUDITOR_PERMS

ROLE_PERMS: dict[str, frozenset[str]] = {
    "tenant_admin": frozenset(ALL_PERMS),
    "auditor": frozenset(AUDITOR_PERMS),
}


def perms_for_roles(roles: list[str] | tuple[str, ...] | set[str]) -> set[str]:
    """Expand role codes to the current permission catalog.

    Unknown roles raise ValueError so IAM writes fail closed.
    """
    perms: set[str] = set()
    for role in roles:
        mapped = ROLE_PERMS.get(str(role))
        if mapped is None:
            raise ValueError(f"unknown role: {role}")
        perms |= set(mapped)
    return perms


def perms_for_roles_lenient(roles: list[str] | tuple[str, ...] | set[str]) -> set[str]:
    """Hydrate path: ignore unknown legacy role codes; never invent perms."""
    perms: set[str] = set()
    for role in roles:
        mapped = ROLE_PERMS.get(str(role))
        if mapped is not None:
            perms |= set(mapped)
    return perms


def known_role_codes() -> list[str]:
    return sorted(ROLE_PERMS.keys())
