"""Stage 14: deterministic Visual Director and source-media decisions.

Revision ID: 006
Revises: 005
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visual_plans",
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
            sa.ForeignKey("narration_alignments.id", ondelete="SET NULL"),
        ),
        sa.Column("script_fingerprint", sa.String(64), nullable=False),
        sa.Column("media_scope", postgresql.JSONB()),
        sa.Column("media_scope_fingerprint", sa.String(64), nullable=False),
        sa.Column("media_index_provenance", postgresql.JSONB()),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("total_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_gaps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("optional_gaps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("direct_provenance_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("media_search_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reused_source_count", sa.Integer(), nullable=False, server_default="0"),
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
            name="ck_visual_plan_status",
        ),
        sa.CheckConstraint(
            "total_queries >= 0 AND resolved_queries >= 0 AND unresolved_queries >= 0 "
            "AND failed_queries >= 0 AND required_gaps >= 0 AND optional_gaps >= 0 "
            "AND direct_provenance_count >= 0 AND media_search_count >= 0 "
            "AND reused_source_count >= 0",
            name="ck_visual_plan_counts",
        ),
        sa.UniqueConstraint(
            "request_fingerprint", name="uq_visual_plan_request_fingerprint"
        ),
    )
    op.create_index(
        "ix_visual_plans_workflow_execucao_id",
        "visual_plans",
        ["workflow_execucao_id"],
    )
    op.create_index(
        "ix_visual_plans_narration_alignment_id",
        "visual_plans",
        ["narration_alignment_id"],
    )

    op.create_table(
        "visual_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "visual_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visual_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("visual_query_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "audio_bloco_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_blocos.id", ondelete="SET NULL"),
        ),
        sa.Column("block_order", sa.Integer(), nullable=False),
        sa.Column("query_order", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("importance", sa.String(24), nullable=False),
        sa.Column("source_note_id", postgresql.UUID(as_uuid=True)),
        sa.Column("desired_duration_seconds", sa.Float()),
        sa.Column("narration_binding_status", sa.String(24), nullable=False),
        sa.Column("narration_start_seconds", sa.Float()),
        sa.Column("narration_end_seconds", sa.Float()),
        sa.Column("target_narration_duration_seconds", sa.Float()),
        sa.Column("source_resolution_status", sa.String(24), nullable=False),
        sa.Column("selection_strategy", sa.String(40)),
        sa.Column("selected_source", postgresql.JSONB()),
        sa.Column("alternatives", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("selection_score", sa.Float()),
        sa.Column("score_components", postgresql.JSONB()),
        sa.Column("reason", sa.String(255)),
        sa.Column("diagnostics", postgresql.JSONB()),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "narration_binding_status IN "
            "('resolved', 'unresolved', 'ambiguous', 'not_applicable')",
            name="ck_visual_decision_binding_status",
        ),
        sa.CheckConstraint(
            "source_resolution_status IN "
            "('resolved', 'unresolved', 'partial', 'unsupported', 'not_required', 'failed')",
            name="ck_visual_decision_source_status",
        ),
        sa.CheckConstraint(
            "(narration_start_seconds IS NULL AND narration_end_seconds IS NULL) OR "
            "(narration_start_seconds >= 0 AND narration_end_seconds >= narration_start_seconds)",
            name="ck_visual_decision_narration_range",
        ),
        sa.CheckConstraint(
            "selection_score IS NULL OR (selection_score BETWEEN 0 AND 1)",
            name="ck_visual_decision_score",
        ),
        sa.UniqueConstraint("visual_plan_id", "visual_query_id", name="uq_plan_visual_query"),
        sa.UniqueConstraint(
            "visual_plan_id", "block_order", "query_order", name="uq_plan_decision_order"
        ),
    )
    op.create_index(
        "ix_visual_decisions_visual_plan_id", "visual_decisions", ["visual_plan_id"]
    )
    op.create_index(
        "ix_visual_decisions_visual_query_id", "visual_decisions", ["visual_query_id"]
    )


def downgrade() -> None:
    op.drop_table("visual_decisions")
    op.drop_table("visual_plans")
