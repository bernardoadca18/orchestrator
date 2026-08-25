"""Stage 15: deterministic Production Timeline edit-decision artifact.

Revision ID: 007
Revises: 006
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_timelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "narration_alignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narration_alignments.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "visual_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visual_plans.id", ondelete="CASCADE"),
        ),
        sa.Column("final_audio_sha256", sa.String(64)),
        sa.Column("narration_sample_rate", sa.Integer()),
        sa.Column("narration_channels", sa.Integer()),
        sa.Column("narration_byte_size", sa.BigInteger()),
        sa.Column("script_fingerprint", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("validator_version", sa.String(64), nullable=False),
        sa.Column("policy", postgresql.JSONB(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("visual_coverage_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("visual_gap_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("visual_coverage_ratio", sa.Float(), nullable=False, server_default="0"),
        sa.Column("placement_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cut_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unplaced_query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_gap_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("preferred_gap_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("optional_gap_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("background_gap_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_shot_duration_seconds", sa.Float()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("diagnostics", postgresql.JSONB()),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', "
            "'completed_with_gaps', 'blocked', 'failed')",
            name="ck_production_timeline_status",
        ),
        sa.CheckConstraint(
            "duration_seconds >= 0 AND visual_coverage_seconds >= 0 AND "
            "visual_gap_seconds >= 0 AND visual_coverage_ratio >= 0 AND "
            "visual_coverage_ratio <= 1",
            name="ck_production_timeline_coverage",
        ),
        sa.CheckConstraint(
            "placement_count >= 0 AND gap_count >= 0 AND cut_count >= 0 AND "
            "unplaced_query_count >= 0 AND required_gap_seconds >= 0 AND "
            "preferred_gap_seconds >= 0 AND optional_gap_seconds >= 0 AND "
            "background_gap_seconds >= 0",
            name="ck_production_timeline_counts",
        ),
        sa.UniqueConstraint(
            "request_fingerprint", name="uq_production_timeline_request_fingerprint"
        ),
    )
    op.create_index(
        "ix_production_timelines_workflow_execucao_id",
        "production_timelines",
        ["workflow_execucao_id"],
    )
    op.create_index(
        "ix_production_timelines_narration_alignment_id",
        "production_timelines",
        ["narration_alignment_id"],
    )
    op.create_index(
        "ix_production_timelines_visual_plan_id",
        "production_timelines",
        ["visual_plan_id"],
    )

    op.create_table(
        "timeline_placements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_timeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_timelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column(
            "visual_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visual_decisions.id", ondelete="SET NULL"),
        ),
        sa.Column("visual_query_ids", postgresql.JSONB(), nullable=False),
        sa.Column("visual_decision_ids", postgresql.JSONB(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="gameplay"),
        sa.Column("source_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeline_start_seconds", sa.Float(), nullable=False),
        sa.Column("timeline_end_seconds", sa.Float(), nullable=False),
        sa.Column("candidate_start_seconds", sa.Float(), nullable=False),
        sa.Column("candidate_end_seconds", sa.Float(), nullable=False),
        sa.Column("source_start_seconds", sa.Float(), nullable=False),
        sa.Column("source_end_seconds", sa.Float(), nullable=False),
        sa.Column("playback_rate", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("transition", sa.String(24), nullable=False, server_default="cut"),
        sa.Column("strategy", sa.String(40), nullable=False),
        sa.Column("importance", sa.String(24), nullable=False),
        sa.Column("source_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "timeline_start_seconds >= 0 AND timeline_end_seconds > timeline_start_seconds",
            name="ck_timeline_placement_timeline_range",
        ),
        sa.CheckConstraint(
            "candidate_start_seconds >= 0 AND "
            "candidate_end_seconds > candidate_start_seconds AND "
            "source_start_seconds >= candidate_start_seconds AND "
            "source_end_seconds <= candidate_end_seconds AND "
            "source_end_seconds > source_start_seconds",
            name="ck_timeline_placement_source_range",
        ),
        sa.CheckConstraint("playback_rate = 1.0", name="ck_timeline_placement_playback_rate"),
        sa.CheckConstraint("transition = 'cut'", name="ck_timeline_placement_transition"),
        sa.CheckConstraint(
            "source_type = 'gameplay'", name="ck_timeline_placement_source_type"
        ),
        sa.CheckConstraint(
            "importance IN ('required', 'preferred', 'optional')",
            name="ck_timeline_placement_importance",
        ),
        sa.UniqueConstraint(
            "production_timeline_id", "order", name="uq_timeline_placement_order"
        ),
    )
    op.create_index(
        "ix_timeline_placements_production_timeline_id",
        "timeline_placements",
        ["production_timeline_id"],
    )

    op.create_table(
        "timeline_gaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_timeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_timelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("timeline_start_seconds", sa.Float(), nullable=False),
        sa.Column("timeline_end_seconds", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("related_visual_query_ids", postgresql.JSONB(), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "timeline_start_seconds >= 0 AND timeline_end_seconds > timeline_start_seconds",
            name="ck_timeline_gap_range",
        ),
        sa.CheckConstraint(
            "severity IN ('required', 'preferred', 'optional', 'background')",
            name="ck_timeline_gap_severity",
        ),
        sa.UniqueConstraint("production_timeline_id", "order", name="uq_timeline_gap_order"),
    )
    op.create_index(
        "ix_timeline_gaps_production_timeline_id",
        "timeline_gaps",
        ["production_timeline_id"],
    )


def downgrade() -> None:
    op.drop_table("timeline_gaps")
    op.drop_table("timeline_placements")
    op.drop_table("production_timelines")
