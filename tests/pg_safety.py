"""Guards for destructive PostgreSQL integration helpers.

Tests that DROP SCHEMA / recreate databases MUST call ``assert_pg_reset_allowed``.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse


def database_name_from_url(url: str) -> str:
    """Extract DB name from SQLAlchemy / libpq URL."""
    # sqlalchemy may use postgresql+psycopg://...
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    normalized = normalized.replace("postgresql+psycopg2://", "postgresql://", 1)
    path = urlparse(normalized).path or ""
    name = path.lstrip("/").split("?")[0]
    return name


def assert_pg_reset_allowed(url: str) -> str:
    """Refuse destructive reset unless explicit gate + test DB name.

    Returns the database name when allowed.
    """
    if os.getenv("ALLOW_PG_SCHEMA_RESET") != "1":
        raise RuntimeError(
            "refusing DROP SCHEMA / schema reset: set ALLOW_PG_SCHEMA_RESET=1 "
            "and use an isolated test database (name must end with _test or _ci)"
        )
    if not url.startswith("postgresql"):
        raise RuntimeError("schema reset requires postgresql DATABASE_URL")
    name = database_name_from_url(url)
    if not name:
        raise RuntimeError("cannot determine database name from DATABASE_URL")
    allowed = name.endswith("_test") or name.endswith("_ci") or name in {"agenttruss_test"}
    if not allowed:
        raise RuntimeError(
            f"refusing schema reset on non-test database {name!r}; "
            "name must end with _test/_ci (e.g. agenttruss_test)"
        )
    return name
