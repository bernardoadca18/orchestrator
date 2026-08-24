"""Stage 12.5E: Qwen voice provenance and production audio identity.

Revision ID: 004
Revises: 003
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_execucoes",
        sa.Column("audio_phase", sa.String(32), nullable=False, server_default="waiting"),
    )
    op.add_column("workflow_execucoes", sa.Column("audio_final_sha256", sa.String(64)))
    op.add_column("workflow_execucoes", sa.Column("audio_final_sample_rate", sa.Integer()))
    op.add_column("workflow_execucoes", sa.Column("audio_final_channels", sa.Integer()))
    op.add_column("workflow_execucoes", sa.Column("audio_final_byte_size", sa.BigInteger()))
    op.add_column("workflow_execucoes", sa.Column("mastering_config", postgresql.JSONB()))
    op.add_column("workflow_execucoes", sa.Column("mastering_metrics", postgresql.JSONB()))

    for name, column_type in (
        ("synthesis_text", sa.Text()),
        ("source_text_sha256", sa.String(64)),
        ("synthesis_text_sha256", sa.String(64)),
        ("synthesis_identity", sa.String(64)),
        ("pronunciation_glossary_identity", sa.String(64)),
        ("idempotency_key", sa.String(200)),
        ("voice_profile", sa.String(100)),
        ("voice_profile_identity", sa.String(64)),
        ("generation_profile", sa.String(100)),
        ("generation_profile_identity", sa.String(64)),
        ("resolved_seed", sa.BigInteger()),
        ("model_id", sa.String(255)),
        ("model_revision", sa.String(128)),
        ("audio_sha256", sa.String(64)),
        ("audio_sample_rate", sa.Integer()),
        ("audio_channels", sa.Integer()),
        ("audio_byte_size", sa.BigInteger()),
        ("narration_start_seconds", sa.Float()),
        ("narration_end_seconds", sa.Float()),
    ):
        op.add_column("audio_blocos", sa.Column(name, column_type, nullable=True))
    op.add_column(
        "audio_blocos",
        sa.Column("generation_attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_audio_blocos_synthesis_identity", "audio_blocos", ["synthesis_identity"])

    op.add_column("transcricoes_audio", sa.Column("audio_sha256", sa.String(64)))
    op.add_column("transcricoes_audio", sa.Column("provider", sa.String(50)))
    op.add_column("transcricoes_audio", sa.Column("model", sa.String(100)))
    op.add_column("transcricoes_audio", sa.Column("language", sa.String(50)))
    op.add_column(
        "transcricoes_audio",
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for name in ("atualizado_em", "language", "model", "provider", "audio_sha256"):
        op.drop_column("transcricoes_audio", name)
    op.drop_index("ix_audio_blocos_synthesis_identity", table_name="audio_blocos")
    for name in (
        "narration_end_seconds",
        "narration_start_seconds",
        "audio_byte_size",
        "audio_channels",
        "audio_sample_rate",
        "audio_sha256",
        "model_revision",
        "model_id",
        "generation_attempt",
        "resolved_seed",
        "generation_profile_identity",
        "generation_profile",
        "voice_profile_identity",
        "voice_profile",
        "idempotency_key",
        "pronunciation_glossary_identity",
        "synthesis_identity",
        "synthesis_text_sha256",
        "source_text_sha256",
        "synthesis_text",
    ):
        op.drop_column("audio_blocos", name)
    for name in (
        "mastering_metrics",
        "mastering_config",
        "audio_final_byte_size",
        "audio_final_channels",
        "audio_final_sample_rate",
        "audio_final_sha256",
        "audio_phase",
    ):
        op.drop_column("workflow_execucoes", name)
