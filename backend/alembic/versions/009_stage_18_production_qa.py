"""Stage 18: deterministic production QA and release gate.

Revision ID: 009
Revises: 008
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_qa_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_render_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_renders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "production_timeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_timelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visual_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visual_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "narration_alignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narration_alignments.id", ondelete="SET NULL"),
        ),
        sa.Column("final_audio_sha256", sa.String(64), nullable=False),
        sa.Column("render_output_sha256", sa.String(64), nullable=False),
        sa.Column("qa_mode", sa.String(16), nullable=False),
        sa.Column("policy_name", sa.String(100), nullable=False),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("policy_identity", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(50), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "publication_ready", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "checks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("error_category", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "qa_mode IN ('standard', 'deep')", name="ck_production_qa_mode"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'passed_with_warnings', "
            "'failed', 'blocked')",
            name="ck_production_qa_status",
        ),
        sa.CheckConstraint(
            "check_count >= 0 AND passed_count >= 0 AND warning_count >= 0 AND "
            "failed_count >= 0 AND blocked_count >= 0",
            name="ck_production_qa_counts",
        ),
        sa.UniqueConstraint(
            "request_fingerprint", name="uq_production_qa_request_fingerprint"
        ),
    )
    op.create_index(
        "ix_production_qa_reports_workflow_execucao_id",
        "production_qa_reports",
        ["workflow_execucao_id"],
    )
    op.create_index(
        "ix_production_qa_reports_workflow_render_id",
        "production_qa_reports",
        ["workflow_render_id"],
    )
    op.create_index(
        "ix_production_qa_reports_production_timeline_id",
        "production_qa_reports",
        ["production_timeline_id"],
    )
    op.create_index(
        "ix_production_qa_reports_workflow_mode_created",
        "production_qa_reports",
        ["workflow_execucao_id", "qa_mode", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("production_qa_reports")
