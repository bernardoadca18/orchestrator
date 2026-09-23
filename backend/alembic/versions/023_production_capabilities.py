"""Snapshot production capabilities for new workflows.

Revision ID: 023_production_capabilities
Revises: 022_audio_reuse_only_assembly
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "023_production_capabilities"
down_revision = "022_audio_reuse_only_assembly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_execucoes", sa.Column("production_capabilities", JSONB(), nullable=True))
    # Existing workflows retain null and use the explicitly bounded legacy fallback.


def downgrade() -> None:
    op.drop_column("workflow_execucoes", "production_capabilities")
