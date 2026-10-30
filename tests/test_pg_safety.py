"""Unit tests for destructive PG reset guards (no live database required)."""

from __future__ import annotations

import pytest
from app.agents.checkpoint import to_psycopg_conninfo
from tests.pg_safety import assert_pg_reset_allowed, database_name_from_url


def test_database_name_from_url():
    assert (
        database_name_from_url("postgresql+psycopg://agent:x@127.0.0.1:5432/agenttruss_test")
        == "agenttruss_test"
    )


def test_to_psycopg_conninfo_strips_sqlalchemy_driver():
    assert (
        to_psycopg_conninfo("postgresql+psycopg://agent:x@127.0.0.1:5432/agenttruss_test")
        == "postgresql://agent:x@127.0.0.1:5432/agenttruss_test"
    )
    assert to_psycopg_conninfo("postgresql://a:b@h/db") == "postgresql://a:b@h/db"


def test_refuse_schema_reset_without_allow_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_PG_SCHEMA_RESET", raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_PG_SCHEMA_RESET"):
        assert_pg_reset_allowed("postgresql+psycopg://agent:x@127.0.0.1:5432/agenttruss_test")


def test_refuse_schema_reset_on_non_test_db_name(monkeypatch):
    monkeypatch.setenv("ALLOW_PG_SCHEMA_RESET", "1")
    with pytest.raises(RuntimeError, match="non-test database"):
        assert_pg_reset_allowed("postgresql+psycopg://agent:x@127.0.0.1:5432/agenttruss")


def test_allow_agenttruss_test(monkeypatch):
    monkeypatch.setenv("ALLOW_PG_SCHEMA_RESET", "1")
    assert assert_pg_reset_allowed("postgresql+psycopg://a:b@localhost/agenttruss_test") == "agenttruss_test"
