"""First-class video segments and static-image timeline placements.

Revision ID: 019
Revises: 018
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_timeline_placement_source_range", "timeline_placements", type_="check"
    )
    op.drop_constraint(
        "ck_timeline_placement_playback_rate", "timeline_placements", type_="check"
    )
    op.drop_constraint(
        "ck_timeline_placement_source_type", "timeline_placements", type_="check"
    )
    for column in (
        "source_session_id",
        "candidate_start_seconds",
        "candidate_end_seconds",
        "source_start_seconds",
        "source_end_seconds",
        "playback_rate",
    ):
        op.alter_column("timeline_placements", column, nullable=True)
    op.add_column(
        "timeline_placements",
        sa.Column("source_image_semantic_index_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "timeline_placements", sa.Column("presentation", postgresql.JSONB())
    )
    op.create_check_constraint(
        "ck_timeline_placement_source_type",
        "timeline_placements",
        "source_type IN ('gameplay', 'video_segment', 'static_image')",
    )
    op.create_check_constraint(
        "ck_timeline_placement_source_union",
        "timeline_placements",
        "(source_type IN ('gameplay', 'video_segment') AND "
        "source_session_id IS NOT NULL AND source_image_semantic_index_id IS NULL AND "
        "candidate_start_seconds >= 0 AND candidate_end_seconds > candidate_start_seconds AND "
        "source_start_seconds >= candidate_start_seconds AND "
        "source_end_seconds <= candidate_end_seconds AND source_end_seconds > source_start_seconds "
        "AND playback_rate = 1.0 AND presentation IS NULL) OR "
        "(source_type = 'static_image' AND source_session_id IS NULL AND "
        "source_image_semantic_index_id IS NOT NULL AND candidate_start_seconds IS NULL AND "
        "candidate_end_seconds IS NULL AND source_start_seconds IS NULL AND "
        "source_end_seconds IS NULL AND playback_rate IS NULL AND presentation IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_timeline_placement_presentation",
        "timeline_placements",
        "presentation IS NULL OR (presentation->>'fit' IN ('cover', 'contain') AND "
        "presentation->>'background' IN ('black', 'blurred_fill') AND "
        "presentation->>'motion' IN ('none', 'slow_zoom_in', 'slow_zoom_out'))",
    )


def downgrade() -> None:
    op.execute("DELETE FROM timeline_placements WHERE source_type = 'static_image'")
    op.execute(
        "UPDATE timeline_placements SET source_type = 'gameplay' "
        "WHERE source_type = 'video_segment'"
    )
    op.drop_constraint(
        "ck_timeline_placement_presentation", "timeline_placements", type_="check"
    )
    op.drop_constraint(
        "ck_timeline_placement_source_union", "timeline_placements", type_="check"
    )
    op.drop_constraint(
        "ck_timeline_placement_source_type", "timeline_placements", type_="check"
    )
    op.drop_column("timeline_placements", "presentation")
    op.drop_column("timeline_placements", "source_image_semantic_index_id")
    for column in (
        "source_session_id",
        "candidate_start_seconds",
        "candidate_end_seconds",
        "source_start_seconds",
        "source_end_seconds",
        "playback_rate",
    ):
        op.alter_column("timeline_placements", column, nullable=False)
    op.create_check_constraint(
        "ck_timeline_placement_source_range",
        "timeline_placements",
        "candidate_start_seconds >= 0 AND candidate_end_seconds > candidate_start_seconds AND "
        "source_start_seconds >= candidate_start_seconds AND "
        "source_end_seconds <= candidate_end_seconds AND source_end_seconds > source_start_seconds",
    )
    op.create_check_constraint(
        "ck_timeline_placement_playback_rate", "timeline_placements", "playback_rate = 1.0"
    )
    op.create_check_constraint(
        "ck_timeline_placement_source_type", "timeline_placements", "source_type = 'gameplay'"
    )
