"""add risk_tickets.args_digest for post-approval integrity

Revision ID: 002
Revises: 001
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("risk_tickets", sa.Column("args_digest", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("risk_tickets", "args_digest")
