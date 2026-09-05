"""AudioChunk + AudioChunkTake for chunk-level regeneration.

Revision: 014
Revises: 013
Create Date: 2026-09-05

Enables chunk-level editing and regeneration of narration audio:
- AudioChunk: physical chunk with stable identity (chunk_id, text snapshot, seed)
- AudioChunkTake: regeneration history with candidate/active/superseded states

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create audio_chunks table
    op.create_table(
        "audio_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "audio_bloco_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_blocos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.String(100), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("synthesis_text_snapshot", sa.Text(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "current_take_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("ix_audio_chunks_audio_bloco_id", "audio_chunks", ["audio_bloco_id"])
    op.create_unique_constraint("uq_audio_chunk_index", "audio_chunks", ["audio_bloco_id", "chunk_index"])
    op.create_unique_constraint("uq_audio_chunk_chunk_id", "audio_chunks", ["chunk_id"])
    op.create_check_constraint(
        "ck_audio_chunk_status",
        "audio_chunks",
        "status IN ('pending', 'active', 'stale', 'failed')",
    )

    # Create audio_chunk_takes table
    op.create_table(
        "audio_chunk_takes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "audio_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("take_number", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            server_default="initial",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("audio_path", sa.String(500), nullable=True),
        sa.Column("audio_sha256", sa.String(64), nullable=True),
        sa.Column("audio_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("audio_sample_rate", sa.Integer(), nullable=True),
        sa.Column("audio_duration_seconds", sa.Float(), nullable=True),
        sa.Column("audio_frame_count", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index("ix_audio_chunk_takes_audio_chunk_id", "audio_chunk_takes", ["audio_chunk_id"])
    op.create_unique_constraint("uq_audio_chunk_take_number", "audio_chunk_takes", ["audio_chunk_id", "take_number"])
    op.create_check_constraint(
        "ck_audio_chunk_take_status",
        "audio_chunk_takes",
        "status IN ('queued', 'running', 'completed', 'failed', 'superseded', 'rejected')",
    )
    op.create_check_constraint(
        "ck_audio_chunk_take_kind",
        "audio_chunk_takes",
        "kind IN ('initial', 'regeneration')",
    )


def downgrade() -> None:
    op.drop_table("audio_chunk_takes")
    op.drop_table("audio_chunks")
