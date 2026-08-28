"""Snapshot pronunciation provenance on every durable audio take.

Revision ID: 012
Revises: 011
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_bloco_takes", sa.Column("canonical_text", sa.Text(), nullable=True))
    op.add_column("audio_bloco_takes", sa.Column("normalized_text", sa.Text(), nullable=True))
    op.add_column("audio_bloco_takes", sa.Column("synthesis_text", sa.Text(), nullable=True))
    op.add_column(
        "audio_bloco_takes",
        sa.Column("pronunciation_glossary_identity", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_bloco_takes",
        sa.Column("pronunciation_replacements", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "audio_bloco_takes",
        sa.Column("text_normalizer_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_bloco_takes",
        sa.Column("text_normalizer_identity", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    for name in (
        "text_normalizer_identity",
        "text_normalizer_version",
        "pronunciation_replacements",
        "pronunciation_glossary_identity",
        "synthesis_text",
        "normalized_text",
        "canonical_text",
    ):
        op.drop_column("audio_bloco_takes", name)
