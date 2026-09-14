"""Operator Console V1 human review and durable music override.

Revision ID: 020
Revises: 019
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_execucoes", sa.Column("music_override_asset_id", sa.String(96)))
    op.add_column(
        "workflow_execucoes",
        sa.Column("music_override_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workflow_execucoes",
        sa.Column("music_override_last_idempotency_key", sa.String(200)),
    )
    op.create_table(
        "workflow_human_reviews",
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
            sa.ForeignKey("workflow_renders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("render_fingerprint", sa.String(64), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column(
            "qa_report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_qa_reports.id", ondelete="SET NULL"),
        ),
        sa.Column("qa_request_fingerprint", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("operator_label", sa.String(100)),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('approved', 'changes_requested', 'rejected')",
            name="ck_workflow_human_review_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_human_review_idempotency_key"),
    )
    op.create_index(
        "ix_workflow_human_reviews_workflow_execucao_id",
        "workflow_human_reviews",
        ["workflow_execucao_id"],
    )
    op.create_index(
        "ix_workflow_human_reviews_workflow_render_id",
        "workflow_human_reviews",
        ["workflow_render_id"],
    )
    op.create_index(
        "ix_workflow_human_reviews_workflow_created",
        "workflow_human_reviews",
        ["workflow_execucao_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("workflow_human_reviews")
    op.drop_column("workflow_execucoes", "music_override_last_idempotency_key")
    op.drop_column("workflow_execucoes", "music_override_revision")
    op.drop_column("workflow_execucoes", "music_override_asset_id")
