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
    op.drop_constraint("ck_timeline_placement_source_range", "timeline_placements", type_="check")
    op.drop_constraint("ck_timeline_placement_playback_rate", "timeline_placements", type_="check")
    op.drop_constraint("ck_timeline_placement_source_type", "timeline_placements", type_="check")
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
    op.add_column("timeline_placements", sa.Column("presentation", postgresql.JSONB()))
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
    connection = op.get_bind()
    incompatible = connection.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*) FROM production_timelines
                 WHERE schema_version = 'production_timeline_schema_v2') AS v2_timelines,
                (SELECT count(*) FROM timeline_placements
                 WHERE source_type = 'static_image') AS static_images,
                (SELECT count(*) FROM timeline_placements
                 WHERE source_type = 'video_segment') AS video_segments,
                (SELECT count(*) FROM timeline_placements
                 WHERE source_type NOT IN ('gameplay', 'static_image', 'video_segment'))
                    AS other_incompatible
            """
        )
    ).one()
    v2_timelines, static_images, video_segments, other_incompatible = (
        int(value) for value in incompatible
    )
    if any((v2_timelines, static_images, video_segments, other_incompatible)):
        raise RuntimeError(
            "Unsafe downgrade 019 -> 018 blocked before modifying data: "
            f"v2_timelines={v2_timelines}, static_image_placements={static_images}, "
            f"video_segment_placements={video_segments}, "
            f"other_incompatible_placements={other_incompatible}. "
            "V2 source meaning cannot be restored losslessly as V1 gameplay. "
            "Explicit administrative migration or data remediation is required."
        )
    op.drop_constraint("ck_timeline_placement_presentation", "timeline_placements", type_="check")
    op.drop_constraint("ck_timeline_placement_source_union", "timeline_placements", type_="check")
    op.drop_constraint("ck_timeline_placement_source_type", "timeline_placements", type_="check")
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
