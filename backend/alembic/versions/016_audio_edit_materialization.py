"""Durable batched materialization state for promoted physical chunk edits.

Revision: 016
Revises: 015
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_audio_bloco_take_kind", "audio_bloco_takes", type_="check")
    op.create_check_constraint(
        "ck_audio_bloco_take_kind",
        "audio_bloco_takes",
        "kind IN ('initial', 'quality_retry', 'manual_retry', 'manual_regeneration', "
        "'chunk_edit_reassembly')",
    )
    op.add_column(
        "workflow_execucoes",
        sa.Column("audio_edit_revision", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "workflow_execucoes",
        sa.Column(
            "audio_materialized_revision", sa.BigInteger(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "audio_blocos",
        sa.Column("audio_edit_revision", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "audio_blocos",
        sa.Column(
            "audio_materialized_revision", sa.BigInteger(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "audio_chunks",
        sa.Column("pause_after_frames", sa.BigInteger(), nullable=True),
    )

    op.create_table(
        "audio_edit_updates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "workflow_execucao_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("target_revision", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("edit_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("published_audio_sha256", sa.String(64), nullable=True),
        sa.Column("error_stage", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_audio_edit_update_status",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_audio_edit_update_idempotency_key"
        ),
    )
    op.create_index(
        "ix_audio_edit_updates_workflow_execucao_id",
        "audio_edit_updates",
        ["workflow_execucao_id"],
    )


def downgrade() -> None:
    op.drop_table("audio_edit_updates")
    op.drop_column("audio_chunks", "pause_after_frames")
    op.drop_column("audio_blocos", "audio_materialized_revision")
    op.drop_column("audio_blocos", "audio_edit_revision")
    op.drop_column("workflow_execucoes", "audio_materialized_revision")
    op.drop_column("workflow_execucoes", "audio_edit_revision")
    op.drop_constraint("ck_audio_bloco_take_kind", "audio_bloco_takes", type_="check")
    op.create_check_constraint(
        "ck_audio_bloco_take_kind",
        "audio_bloco_takes",
        "kind IN ('initial', 'quality_retry', 'manual_retry', 'manual_regeneration')",
    )
