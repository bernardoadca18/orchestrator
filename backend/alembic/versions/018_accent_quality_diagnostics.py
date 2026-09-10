"""Persist independent accent-quality diagnostics.

Revision ID: 018
Revises: 017
Create Date: 2026-09-10
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_bloco_takes", sa.Column("accent_quality", postgresql.JSONB()))
    op.add_column("audio_chunk_takes", sa.Column("accent_quality", postgresql.JSONB()))
    op.add_column(
        "audio_chunk_takes",
        sa.Column("quality_rejection_reason", sa.String(length=100)),
    )


def downgrade() -> None:
    op.drop_column("audio_chunk_takes", "quality_rejection_reason")
    op.drop_column("audio_chunk_takes", "accent_quality")
    op.drop_column("audio_bloco_takes", "accent_quality")
