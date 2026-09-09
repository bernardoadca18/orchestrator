"""Canonical block assembly and physical take provenance.

Revision: 017
Revises: 016
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audio_blocos", sa.Column("assembly_manifest_identity", sa.String(64), nullable=True)
    )
    op.add_column(
        "audio_blocos", sa.Column("assembly_manifest", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "audio_bloco_takes",
        sa.Column("assembly_manifest_identity", sa.String(64), nullable=True),
    )
    op.add_column(
        "audio_bloco_takes", sa.Column("assembly_manifest", postgresql.JSONB(), nullable=True)
    )
    op.add_column("audio_chunk_takes", sa.Column("audio_channels", sa.Integer(), nullable=True))
    op.add_column("audio_chunk_takes", sa.Column("audio_subtype", sa.String(32), nullable=True))
    op.add_column("audio_chunk_takes", sa.Column("source_job_id", sa.String(64), nullable=True))
    op.add_column(
        "audio_chunk_takes", sa.Column("artifact_integrity", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "audio_chunk_takes", sa.Column("provider_metadata", postgresql.JSONB(), nullable=True)
    )
    op.add_column("audio_chunk_takes", sa.Column("speaker_similarity", sa.Float(), nullable=True))
    op.add_column(
        "audio_chunk_takes",
        sa.Column("speaker_similarity_reference_identity", sa.String(64), nullable=True),
    )
    op.add_column(
        "audio_chunk_takes",
        sa.Column("speaker_similarity_policy_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "audio_chunk_takes", sa.Column("speaker_identity_status", sa.String(32), nullable=True)
    )
    op.create_index(
        "ix_audio_chunk_takes_source_job_id", "audio_chunk_takes", ["source_job_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audio_chunk_takes_source_job_id", table_name="audio_chunk_takes")
    op.drop_column("audio_chunk_takes", "speaker_identity_status")
    op.drop_column("audio_chunk_takes", "speaker_similarity_policy_version")
    op.drop_column("audio_chunk_takes", "speaker_similarity_reference_identity")
    op.drop_column("audio_chunk_takes", "speaker_similarity")
    op.drop_column("audio_chunk_takes", "provider_metadata")
    op.drop_column("audio_chunk_takes", "artifact_integrity")
    op.drop_column("audio_chunk_takes", "source_job_id")
    op.drop_column("audio_chunk_takes", "audio_subtype")
    op.drop_column("audio_chunk_takes", "audio_channels")
    op.drop_column("audio_bloco_takes", "assembly_manifest")
    op.drop_column("audio_bloco_takes", "assembly_manifest_identity")
    op.drop_column("audio_blocos", "assembly_manifest")
    op.drop_column("audio_blocos", "assembly_manifest_identity")
