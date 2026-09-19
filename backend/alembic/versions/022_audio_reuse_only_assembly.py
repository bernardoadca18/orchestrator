"""Add reuse_only_assembly flag for zero-synthesis rebuild.

Revision ID: 022_audio_reuse_only_assembly
Revises: 021
Create Date: 2026-09-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "022_audio_reuse_only_assembly"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_execucoes",
        sa.Column(
            "reuse_only_assembly",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_execucoes", "reuse_only_assembly")
