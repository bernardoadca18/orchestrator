"""Persist manual edit package and allow its production phase.

Revision ID: 024_manual_edit_package
Revises: 023_production_capabilities
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "024_manual_edit_package"
down_revision = "023_production_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_execucoes", sa.Column("manual_edit_package", JSONB(), nullable=True))
    op.drop_constraint("ck_workflow_production_phase", "workflow_execucoes", type_="check")
    op.create_check_constraint(
        "ck_workflow_production_phase",
        "workflow_execucoes",
        "production_phase IS NULL OR production_phase IN "
        "('audio', 'narration_alignment', 'manual_edit_package', 'visual_plan', "
        "'production_timeline', 'render', 'completed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workflow_production_phase", "workflow_execucoes", type_="check")
    op.create_check_constraint(
        "ck_workflow_production_phase",
        "workflow_execucoes",
        "production_phase IS NULL OR production_phase IN "
        "('audio', 'narration_alignment', 'visual_plan', "
        "'production_timeline', 'render', 'completed')",
    )
    op.drop_column("workflow_execucoes", "manual_edit_package")
