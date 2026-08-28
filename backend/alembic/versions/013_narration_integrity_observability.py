"""Persist physical narration integrity and quality diagnostics.

Revision ID: 013
Revises: 012
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_execucoes", sa.Column("audio_final_frame_count", sa.BigInteger()))
    op.add_column("workflow_execucoes", sa.Column("audio_final_subtype", sa.String(length=32)))
    op.add_column("workflow_execucoes", sa.Column("audio_integrity", postgresql.JSONB()))
    op.add_column("workflow_execucoes", sa.Column("narration_diagnostics", postgresql.JSONB()))

    op.add_column("audio_blocos", sa.Column("audio_frame_count", sa.BigInteger()))
    op.add_column("audio_blocos", sa.Column("audio_subtype", sa.String(length=32)))
    op.add_column("audio_blocos", sa.Column("artifact_integrity", postgresql.JSONB()))
    op.add_column("audio_blocos", sa.Column("pause_diagnostics", postgresql.JSONB()))

    op.add_column("audio_bloco_takes", sa.Column("audio_sample_rate", sa.Integer()))
    op.add_column("audio_bloco_takes", sa.Column("audio_channels", sa.Integer()))
    op.add_column("audio_bloco_takes", sa.Column("audio_frame_count", sa.BigInteger()))
    op.add_column("audio_bloco_takes", sa.Column("audio_subtype", sa.String(length=32)))
    op.add_column("audio_bloco_takes", sa.Column("quality_status", sa.String(length=32)))
    op.add_column("audio_bloco_takes", sa.Column("artifact_integrity", postgresql.JSONB()))
    op.add_column("audio_bloco_takes", sa.Column("provider_metadata", postgresql.JSONB()))
    op.add_column("audio_bloco_takes", sa.Column("content_quality", postgresql.JSONB()))
    op.add_column("audio_bloco_takes", sa.Column("pause_diagnostics", postgresql.JSONB()))


def downgrade() -> None:
    for name in (
        "pause_diagnostics",
        "content_quality",
        "provider_metadata",
        "artifact_integrity",
        "quality_status",
        "audio_subtype",
        "audio_frame_count",
        "audio_channels",
        "audio_sample_rate",
    ):
        op.drop_column("audio_bloco_takes", name)
    for name in (
        "pause_diagnostics",
        "artifact_integrity",
        "audio_subtype",
        "audio_frame_count",
    ):
        op.drop_column("audio_blocos", name)
    for name in (
        "narration_diagnostics",
        "audio_integrity",
        "audio_final_subtype",
        "audio_final_frame_count",
    ):
        op.drop_column("workflow_execucoes", name)
