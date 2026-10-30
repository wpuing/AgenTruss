"""Real PostgreSQL Alembic upgrade checks (fresh head + 003→004).

DANGER: resets the ``public`` schema. Only against disposable test DBs.

Required env::

  RUN_PG_INTEGRATION=1
  ALLOW_PG_SCHEMA_RESET=1
  DATABASE_URL=postgresql+psycopg://.../agenttruss_test   # name must end with _test/_ci

Never point this at development / shared / production databases.
Skip ≠ pass — CI ``pg-integration`` fails if these tests skip.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.pg_safety import assert_pg_reset_allowed

_RUN = os.getenv("RUN_PG_INTEGRATION") == "1"
_URL = (os.getenv("DATABASE_URL") or "").strip()
_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not _RUN or not _URL.startswith("postgresql"),
    reason="set RUN_PG_INTEGRATION=1 and postgresql DATABASE_URL",
)


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _reset_public_schema(url: str) -> None:
    assert_pg_reset_allowed(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # PG15+ recreates empty public without default grants for other roles.
        conn.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
    engine.dispose()


@pytest.fixture
def isolated_db_url(monkeypatch):
    assert _URL.startswith("postgresql")
    assert_pg_reset_allowed(_URL)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", _URL)
    yield _URL


def test_alembic_upgrade_head_creates_body_text(isolated_db_url):
    _reset_public_schema(isolated_db_url)
    cfg = _alembic_cfg(isolated_db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(isolated_db_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "kb_documents" in tables
    cols = {c["name"] for c in insp.get_columns("kb_documents")}
    assert "body_text" in cols
    assert "text_preview" in cols
    # Phase5.28 Alembic 006
    assert "settings_kv" in tables
    assert "chunk_strategies" in tables
    assert "retrieve_strategies" in tables
    assert "agent_configs" in tables
    engine.dispose()


def test_alembic_upgrade_003_to_004_keeps_rows(isolated_db_url):
    _reset_public_schema(isolated_db_url)
    cfg = _alembic_cfg(isolated_db_url)
    command.upgrade(cfg, "003")
    engine = create_engine(isolated_db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO knowledge_bases (id, tenant_id, name, code, status)
                VALUES ('kb_mig', 'demo', 'mig', 'mig', 'active')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO kb_documents (id, tenant_id, kb_id, filename, status, text_preview, storage)
                VALUES ('doc_mig', 'demo', 'kb_mig', 'a.md', 'uploaded', 'hello-preview', 'memory')
                """
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT text_preview, body_text FROM kb_documents WHERE id='doc_mig'")
        ).one()
        assert row[0] == "hello-preview"
        # Historical rows: preview only — migration must NOT invent full body.
        assert row[1] is None
    engine.dispose()

