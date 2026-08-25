"""Stage 13: deterministic narration alignment and VisualQuery bindings.

Revision ID: 005
Revises: 004
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "narration_alignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transcription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transcricoes_audio.id", ondelete="SET NULL"),
        ),
        sa.Column("final_audio_sha256", sa.String(64)),
        sa.Column("script_fingerprint", sa.String(64), nullable=False),
        sa.Column("transcription_fingerprint", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_visual_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_visual_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ambiguous_visual_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "not_applicable_visual_queries", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'blocked')",
            name="ck_narration_alignment_status",
        ),
        sa.CheckConstraint(
            "block_count >= 0 AND resolved_visual_queries >= 0 AND "
            "unresolved_visual_queries >= 0 AND ambiguous_visual_queries >= 0 AND "
            "not_applicable_visual_queries >= 0",
            name="ck_narration_alignment_counts",
        ),
        sa.UniqueConstraint("fingerprint", name="uq_narration_alignment_fingerprint"),
    )
    op.create_index(
        "ix_narration_alignments_workflow_execucao_id",
        "narration_alignments",
        ["workflow_execucao_id"],
    )

    op.create_table(
        "narration_block_alignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "narration_alignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narration_alignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "audio_bloco_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_blocos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("narration_start_seconds", sa.Float(), nullable=False),
        sa.Column("narration_end_seconds", sa.Float(), nullable=False),
        sa.Column("canonical_token_count", sa.Integer(), nullable=False),
        sa.Column("synthesis_token_count", sa.Integer(), nullable=False),
        sa.Column("asr_word_count", sa.Integer(), nullable=False),
        sa.Column("canonical_synthesis_coverage", sa.Float(), nullable=False),
        sa.Column("synthesis_asr_coverage", sa.Float(), nullable=False),
        sa.Column("overall_coverage", sa.Float(), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "narration_start_seconds >= 0 AND "
            "narration_end_seconds >= narration_start_seconds",
            name="ck_block_alignment_range",
        ),
        sa.CheckConstraint(
            "canonical_synthesis_coverage BETWEEN 0 AND 1",
            name="ck_block_canonical_synthesis_coverage",
        ),
        sa.CheckConstraint(
            "synthesis_asr_coverage BETWEEN 0 AND 1",
            name="ck_block_synthesis_asr_coverage",
        ),
        sa.CheckConstraint(
            "overall_coverage BETWEEN 0 AND 1", name="ck_block_overall_coverage"
        ),
        sa.UniqueConstraint(
            "narration_alignment_id", "block_order", name="uq_block_alignment_order"
        ),
    )
    op.create_index(
        "ix_narration_block_alignments_narration_alignment_id",
        "narration_block_alignments",
        ["narration_alignment_id"],
    )

    op.create_table(
        "visual_query_narration_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "narration_alignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narration_alignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "audio_bloco_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_blocos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("visual_query_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("block_order", sa.Integer(), nullable=False),
        sa.Column("query_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("narration_start_seconds", sa.Float()),
        sa.Column("narration_end_seconds", sa.Float()),
        sa.Column("excerpt_match_method", sa.String(32)),
        sa.Column("excerpt_match_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("speech_alignment_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("binding_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("canonical_start_char", sa.Integer()),
        sa.Column("canonical_end_char", sa.Integer()),
        sa.Column("reason", sa.String(255)),
        sa.Column("diagnostics", postgresql.JSONB()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('resolved', 'unresolved', 'ambiguous', 'not_applicable')",
            name="ck_visual_query_binding_status",
        ),
        sa.CheckConstraint(
            "(narration_start_seconds IS NULL AND narration_end_seconds IS NULL) OR "
            "(narration_start_seconds >= 0 AND "
            "narration_end_seconds >= narration_start_seconds)",
            name="ck_visual_query_binding_range",
        ),
        sa.CheckConstraint(
            "excerpt_match_score BETWEEN 0 AND 1 AND "
            "speech_alignment_coverage BETWEEN 0 AND 1 AND "
            "binding_confidence BETWEEN 0 AND 1",
            name="ck_visual_query_binding_scores",
        ),
        sa.UniqueConstraint(
            "narration_alignment_id", "visual_query_id", name="uq_alignment_visual_query"
        ),
    )
    op.create_index(
        "ix_visual_query_narration_bindings_narration_alignment_id",
        "visual_query_narration_bindings",
        ["narration_alignment_id"],
    )
    op.create_index(
        "ix_visual_query_narration_bindings_visual_query_id",
        "visual_query_narration_bindings",
        ["visual_query_id"],
    )


def downgrade() -> None:
    op.drop_table("visual_query_narration_bindings")
    op.drop_table("narration_block_alignments")
    op.drop_table("narration_alignments")
