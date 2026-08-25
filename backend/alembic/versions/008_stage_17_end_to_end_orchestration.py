"""Stage 17: durable end-to-end production orchestration.

Revision ID: 008
Revises: 007
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_execucoes", sa.Column("production_status", sa.String(32)))
    op.add_column("workflow_execucoes", sa.Column("production_phase", sa.String(32)))
    op.add_column(
        "workflow_execucoes", sa.Column("production_error_category", sa.String(100))
    )
    op.add_column("workflow_execucoes", sa.Column("production_error_message", sa.Text()))
    op.add_column(
        "workflow_execucoes", sa.Column("production_started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "workflow_execucoes", sa.Column("production_completed_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_workflow_production_status",
        "workflow_execucoes",
        "production_status IS NULL OR production_status IN "
        "('queued', 'running', 'blocked', 'failed', 'completed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_workflow_production_phase",
        "workflow_execucoes",
        "production_phase IS NULL OR production_phase IN "
        "('audio', 'narration_alignment', 'visual_plan', 'production_timeline', "
        "'render', 'completed')",
    )

    op.execute("DROP INDEX IF EXISTS uq_workflow_roteiro_ativo")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_workflow_roteiro_ativo
        ON workflow_execucoes (roteiro_id)
        WHERE production_status IN ('queued', 'running', 'blocked')
           OR (production_status IS NULL
               AND status IN ('agendada', 'gerando_audio', 'audio_concluido'))
        """
    )

    op.create_table(
        "workflow_renders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "production_timeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_timelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("production_timeline_fingerprint", sa.String(64), nullable=False),
        sa.Column("render_profile", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="final"),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("composer_render_id", sa.String(64)),
        sa.Column("render_profile_identity", sa.String(64)),
        sa.Column("render_fingerprint", sa.String(64)),
        sa.Column("manifest_fingerprint", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("phase", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("frames_rendered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_frames", sa.Integer()),
        sa.Column("output_sha256", sa.String(64)),
        sa.Column("output_byte_size", sa.BigInteger()),
        sa.Column("output_duration_seconds", sa.Float()),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("output_fps", sa.Float()),
        sa.Column("output_frame_count", sa.Integer()),
        sa.Column("output_video_codec", sa.String(50)),
        sa.Column("output_audio_codec", sa.String(50)),
        sa.Column("output_audio_sample_rate", sa.Integer()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("error_category", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("mode IN ('final', 'preview')", name="ck_workflow_render_mode"),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'preparing_assets', 'rendering', "
            "'validating', 'completed', 'failed')",
            name="ck_workflow_render_status",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 1", name="ck_workflow_render_progress"
        ),
        sa.CheckConstraint(
            "frames_rendered >= 0 AND (total_frames IS NULL OR total_frames > 0)",
            name="ck_workflow_render_frames",
        ),
        sa.UniqueConstraint(
            "request_fingerprint", name="uq_workflow_render_request_fingerprint"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_render_idempotency_key"),
        sa.UniqueConstraint(
            "composer_render_id", name="uq_workflow_render_composer_render_id"
        ),
    )
    op.create_index(
        "ix_workflow_renders_workflow_execucao_id",
        "workflow_renders",
        ["workflow_execucao_id"],
    )
    op.create_index(
        "ix_workflow_renders_production_timeline_id",
        "workflow_renders",
        ["production_timeline_id"],
    )


def downgrade() -> None:
    op.drop_table("workflow_renders")
    op.execute("DROP INDEX IF EXISTS uq_workflow_roteiro_ativo")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_workflow_roteiro_ativo
        ON workflow_execucoes (roteiro_id)
        WHERE status IN ('agendada', 'gerando_audio', 'audio_concluido')
        """
    )
    op.drop_constraint(
        "ck_workflow_production_phase", "workflow_execucoes", type_="check"
    )
    op.drop_constraint(
        "ck_workflow_production_status", "workflow_execucoes", type_="check"
    )
    op.drop_column("workflow_execucoes", "production_completed_at")
    op.drop_column("workflow_execucoes", "production_started_at")
    op.drop_column("workflow_execucoes", "production_error_message")
    op.drop_column("workflow_execucoes", "production_error_category")
    op.drop_column("workflow_execucoes", "production_phase")
    op.drop_column("workflow_execucoes", "production_status")
