"""Narration preprocessing and retry provenance.

Revision ID: 010
Revises: 009
Create Date: 2026-08-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_blocos", sa.Column("text_normalizer_version", sa.String(64)))
    op.add_column("audio_blocos", sa.Column("text_normalizer_identity", sa.String(64)))
    op.add_column("audio_blocos", sa.Column("initial_resolved_seed", sa.BigInteger()))
    op.add_column("audio_blocos", sa.Column("retry_seed_strategy", sa.String(64)))
    op.add_column("audio_blocos", sa.Column("generation_attempt_history", postgresql.JSONB()))


def downgrade() -> None:
    for name in (
        "generation_attempt_history",
        "retry_seed_strategy",
        "initial_resolved_seed",
        "text_normalizer_identity",
        "text_normalizer_version",
    ):
        op.drop_column("audio_blocos", name)
