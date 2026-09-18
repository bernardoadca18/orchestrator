"""Durable lateral thumbnail packages; no video lifecycle changes."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_thumbnail_packages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("creative_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_snapshot", pg.JSONB(), nullable=False),
        sa.Column("result", pg.JSONB()),
        sa.Column("selected_candidate_id", sa.String(1)),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("selected_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "workflow_execucao_id", "creative_revision", name="uq_thumbnail_revision"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')", name="ck_thumbnail_status"
        ),
    )
    op.create_index(
        "ix_workflow_thumbnail_packages_workflow_execucao_id",
        "workflow_thumbnail_packages",
        ["workflow_execucao_id"],
    )


def downgrade():
    op.drop_table("workflow_thumbnail_packages")
