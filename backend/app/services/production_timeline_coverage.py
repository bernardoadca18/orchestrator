"""Temporal coverage gates, independent of visual query fulfillment."""

import math
from typing import Any

# The planner quantizes time to integer microseconds.
TIME_EPSILON_SECONDS = 0.000001
READY_STATUSES = {"completed", "completed_with_gaps"}


def timeline_is_gap_free(timeline: Any) -> bool:
    if timeline is None or timeline.status not in READY_STATUSES:
        return False
    gap_seconds = getattr(timeline, "visual_gap_seconds", None)
    coverage = getattr(timeline, "visual_coverage_ratio", None)
    duration = getattr(timeline, "duration_seconds", None)
    if any(
        value is None or not math.isfinite(value) for value in (gap_seconds, coverage, duration)
    ):
        return False
    return (
        timeline.gap_count == 0
        and duration > 0
        and abs(gap_seconds) <= TIME_EPSILON_SECONDS
        and abs(1.0 - coverage) * duration <= TIME_EPSILON_SECONDS
        and not getattr(timeline, "gaps", ())
    )


def timeline_has_temporal_gaps(timeline: Any) -> bool:
    if timeline is None or timeline.status not in READY_STATUSES:
        return False
    return (
        timeline.gap_count > 0
        or getattr(timeline, "visual_gap_seconds", 0) > TIME_EPSILON_SECONDS
        or (1.0 - getattr(timeline, "visual_coverage_ratio", 1)) * timeline.duration_seconds
        > TIME_EPSILON_SECONDS
        or bool(getattr(timeline, "gaps", ()))
    )


def can_repair_production_timeline(workflow: Any, timeline: Any) -> bool:
    return (
        workflow.production_status == "blocked"
        and workflow.production_phase == "production_timeline"
        and workflow.production_error_category == "production_timeline_has_gaps"
        and timeline_has_temporal_gaps(timeline)
    )


def temporal_gap_details(timeline: Any) -> dict:
    return {
        "gap_count": timeline.gap_count,
        "total_gap_seconds": timeline.visual_gap_seconds,
        "coverage_ratio": timeline.visual_coverage_ratio,
        "gaps": [
            {
                "start_seconds": gap.timeline_start_seconds,
                "end_seconds": gap.timeline_end_seconds,
                "severity": gap.severity,
                "reason": gap.reason,
                "related_visual_query_ids": [str(qid) for qid in gap.related_visual_query_ids],
            }
            for gap in timeline.gaps
        ],
    }
