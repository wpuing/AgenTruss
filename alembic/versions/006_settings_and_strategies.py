"""Persist settings_kv + chunk/retrieve strategies (PG authority).

Revision ID: 006
Revises: 005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings_kv",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "chunk_strategies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("child_size", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("overlap", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "retrieve_strategies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("vector_min_score", sa.Float(), nullable=False, server_default="0.15"),
        sa.Column("rerank_min_score", sa.Float(), nullable=False, server_default="0.15"),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("retrieve_strategies")
    op.drop_table("chunk_strategies")
    op.drop_table("settings_kv")
