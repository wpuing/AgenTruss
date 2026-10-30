"""Add agent_configs for durable KB/RAG bindings across restarts.

Revision ID: 005
Revises: 004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_configs",
        sa.Column("agent_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column("industry", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="published"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("kb_id", sa.String(length=64), nullable=True),
        sa.Column("rag_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("enabled_tools_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "code", name="uq_agent_tenant_code"),
    )
    op.create_index("ix_agent_configs_tenant_id", "agent_configs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_configs_tenant_id", table_name="agent_configs")
    op.drop_table("agent_configs")
