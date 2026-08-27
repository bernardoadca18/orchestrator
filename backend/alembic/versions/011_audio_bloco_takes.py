"""Persist semantic voice takes and explicit regeneration requests.

Revision ID: 011
Revises: 010
Create Date: 2026-08-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audio_bloco_takes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audio_bloco_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("request_key", sa.String(length=200), nullable=True),
        sa.Column("job_id_voz", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("requested_seed", sa.BigInteger(), nullable=True),
        sa.Column("resolved_seed", sa.BigInteger(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("model_revision", sa.String(length=128), nullable=True),
        sa.Column("voice_profile", sa.String(length=100), nullable=True),
        sa.Column("voice_profile_identity", sa.String(length=64), nullable=True),
        sa.Column("generation_profile", sa.String(length=100), nullable=True),
        sa.Column("generation_profile_identity", sa.String(length=64), nullable=True),
        sa.Column("voice_chunking_identity", sa.String(length=64), nullable=True),
        sa.Column("chunking_identity", sa.String(length=64), nullable=True),
        sa.Column("synthesis_identity", sa.String(length=64), nullable=True),
        sa.Column("error_category", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_diagnostics", postgresql.JSONB(), nullable=True),
        sa.Column("audio_path", sa.String(length=500), nullable=True),
        sa.Column("audio_duration_seconds", sa.Float(), nullable=True),
        sa.Column("audio_sha256", sa.String(length=64), nullable=True),
        sa.Column("audio_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["audio_bloco_id"], ["audio_blocos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audio_bloco_id", "attempt_index", name="uq_audio_bloco_take_attempt"),
        sa.UniqueConstraint("request_key", name="uq_audio_bloco_take_request_key"),
        sa.CheckConstraint(
            "kind IN ('initial', 'quality_retry', 'manual_retry', 'manual_regeneration')",
            name="ck_audio_bloco_take_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'superseded')",
            name="ck_audio_bloco_take_status",
        ),
    )
    op.create_index("ix_audio_bloco_takes_audio_bloco_id", "audio_bloco_takes", ["audio_bloco_id"])
    op.add_column(
        "audio_blocos",
        sa.Column("current_take_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "audio_blocos",
        sa.Column("requested_take_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "audio_blocos",
        sa.Column("voice_chunking_identity", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_blocos",
        sa.Column("pronunciation_replacements", postgresql.JSONB(), nullable=True),
    )
    op.add_column("audio_blocos", sa.Column("normalized_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audio_blocos", "voice_chunking_identity")
    op.drop_column("audio_blocos", "pronunciation_replacements")
    op.drop_column("audio_blocos", "normalized_text")
    op.drop_column("audio_blocos", "requested_take_id")
    op.drop_column("audio_blocos", "current_take_id")
    op.drop_index("ix_audio_bloco_takes_audio_bloco_id", table_name="audio_bloco_takes")
    op.drop_table("audio_bloco_takes")
