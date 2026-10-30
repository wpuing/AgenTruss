"""SQLAlchemy engine/session for PostgreSQL persistence."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy 2 declarative root (typed for mypy)."""

_engine = None
_SessionLocal: sessionmaker | None = None


def init_db() -> None:
    """Create engine when DATABASE_URL is configured."""
    global _engine, _SessionLocal
    settings = get_settings()
    url = (settings.database_url or "").strip()
    if not url:
        if settings.storage_backend == "postgres":
            raise RuntimeError("DATABASE_URL required for postgres storage")
        return
    _engine = create_engine(url, pool_pre_ping=True, future=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    logger.info("database engine ready")


def create_all() -> None:
    """Create tables (dev/test). Prefer Alembic migrations in production."""
    if _engine is None:
        return
    from app.infra import models  # noqa: F401

    Base.metadata.create_all(_engine)


def dispose_db() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("database not initialized")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_healthy() -> bool:
    if _engine is None:
        return False
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def get_engine():
    return _engine
