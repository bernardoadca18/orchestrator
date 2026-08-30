"""Separate active production errors from recovered error history.

Revision ID: 014
Revises: 013
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_execucoes",
        sa.Column("production_error_history", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_execucoes", "production_error_history")
