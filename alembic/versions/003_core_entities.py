"""sessions, knowledge bases, documents, segments

Revision ID: 003
Revises: 002
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_turns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("assistant_text", sa.Text(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_turns_session_id", "chat_turns", ["session_id"])
    op.create_index("ix_chat_turns_tenant_id", "chat_turns", ["tenant_id"])

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("default_chunk_strategy_id", sa.String(64), nullable=True),
        sa.Column("default_retrieve_strategy_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "code", name="uq_kb_tenant_code"),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("text_preview", sa.Text(), nullable=True),
        sa.Column("object_key", sa.String(512), nullable=True),
        sa.Column("storage", sa.String(32), nullable=False),
        sa.Column("uploader_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_documents_tenant_id", "kb_documents", ["tenant_id"])
    op.create_index("ix_kb_documents_kb_id", "kb_documents", ["kb_id"])

    op.create_table(
        "kb_segments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("kb_id", sa.String(64), nullable=False),
        sa.Column("doc_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column("seg_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("vector_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_segments_tenant_id", "kb_segments", ["tenant_id"])
    op.create_index("ix_kb_segments_kb_id", "kb_segments", ["kb_id"])
    op.create_index("ix_kb_segments_doc_id", "kb_segments", ["doc_id"])


def downgrade() -> None:
    op.drop_table("kb_segments")
    op.drop_table("kb_documents")
    op.drop_table("knowledge_bases")
    op.drop_table("chat_turns")
    op.drop_table("chat_sessions")
