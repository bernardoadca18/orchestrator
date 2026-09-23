"""Durable storyboard state and asset-based static image placements.

Downgrade fails explicitly while V3 artifacts exist; it never deletes production history.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "025_storyboard"
down_revision = "024_manual_edit_package"
branch_labels = None
depends_on = None

OLD_PHASES = "production_phase IS NULL OR production_phase IN ('audio','narration_alignment','manual_edit_package','visual_plan','production_timeline','render','completed')"
OLD_UNION = "(source_type IN ('gameplay', 'video_segment') AND source_session_id IS NOT NULL AND source_image_semantic_index_id IS NULL AND candidate_start_seconds >= 0 AND candidate_end_seconds > candidate_start_seconds AND source_start_seconds >= candidate_start_seconds AND source_end_seconds <= candidate_end_seconds AND source_end_seconds > source_start_seconds AND playback_rate = 1.0 AND presentation IS NULL) OR (source_type = 'static_image' AND source_session_id IS NULL AND source_image_semantic_index_id IS NOT NULL AND candidate_start_seconds IS NULL AND candidate_end_seconds IS NULL AND source_start_seconds IS NULL AND source_end_seconds IS NULL AND playback_rate IS NULL AND presentation IS NOT NULL)"
OLD_PRESENTATION = "presentation IS NULL OR (presentation->>'fit' IN ('cover','contain') AND presentation->>'background' IN ('black','blurred_fill') AND presentation->>'motion' IN ('none','slow_zoom_in','slow_zoom_out'))"
NEW_PRESENTATION = "presentation IS NULL OR (presentation->>'fit' IN ('cover','contain') AND presentation->>'background' IN ('black','blurred_fill') AND (presentation->>'motion' IN ('none','slow_zoom_in','slow_zoom_out') OR (presentation->>'policy_version' = 'static_image_presentation_v2' AND presentation ? 'transform')))"


def constraint(table, name, expression):
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, expression)


def upgrade():
    op.add_column("workflow_execucoes", sa.Column("resolved_storyboard", JSONB(), nullable=True))
    op.add_column("workflow_execucoes", sa.Column("storyboard_bindings", JSONB(), nullable=True))
    op.add_column(
        "workflow_execucoes", sa.Column("storyboard_media_project_id", UUID(), nullable=True)
    )
    constraint(
        "workflow_execucoes",
        "ck_workflow_production_phase",
        OLD_PHASES.replace("'visual_plan'", "'visual_plan','storyboard','storyboard_assets'"),
    )
    constraint(
        "timeline_placements",
        "ck_timeline_placement_source_union",
        OLD_UNION.replace("source_image_semantic_index_id IS NOT NULL AND ", ""),
    )
    constraint("timeline_placements", "ck_timeline_placement_presentation", NEW_PRESENTATION)


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM production_timelines WHERE schema_version='production_timeline_schema_v3') OR EXISTS (SELECT 1 FROM workflow_execucoes WHERE resolved_storyboard IS NOT NULL OR storyboard_media_project_id IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade storyboard production history; export/remove these workflows explicitly first"
        )
    constraint("timeline_placements", "ck_timeline_placement_source_union", OLD_UNION)
    constraint("timeline_placements", "ck_timeline_placement_presentation", OLD_PRESENTATION)
    constraint("workflow_execucoes", "ck_workflow_production_phase", OLD_PHASES)
    for column in ("storyboard_media_project_id", "storyboard_bindings", "resolved_storyboard"):
        op.drop_column("workflow_execucoes", column)
