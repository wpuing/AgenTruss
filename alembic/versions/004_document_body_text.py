"""Add kb_documents.body_text for durable full document bodies without MinIO.

Revision ID: 004
Revises: 003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kb_documents", sa.Column("body_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("kb_documents", "body_text")
