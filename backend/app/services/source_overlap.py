"""Shared temporal relationship for source windows."""

from __future__ import annotations

from enum import IntEnum


class SourceRelation(IntEnum):
    DISTINCT_MEDIA = 0
    SAME_MEDIA_DISTINCT_WINDOW = 1
    PARTIAL_OVERLAP = 2
    STRONG_OVERLAP = 3
    EXACT_OR_NEAR_EXACT = 4


def temporal_overlap_ratio(
    left_session: object,
    left_media: object,
    left_start: float,
    left_end: float,
    right_session: object,
    right_media: object,
    right_start: float,
    right_end: float,
) -> float:
    if left_session != right_session or left_media != right_media:
        return 0.0
    overlap = min(left_end, right_end) - max(left_start, right_start)
    if overlap <= 0:
        return 0.0
    shorter = min(left_end - left_start, right_end - right_start)
    return min(1.0, overlap / shorter) if shorter > 0 else 0.0


def classify_source_windows(
    left_session: object,
    left_media: object,
    left_start: float,
    left_end: float,
    right_session: object,
    right_media: object,
    right_start: float,
    right_end: float,
) -> SourceRelation:
    if left_session != right_session or left_media != right_media:
        return SourceRelation.DISTINCT_MEDIA
    overlap = temporal_overlap_ratio(
        left_session,
        left_media,
        left_start,
        left_end,
        right_session,
        right_media,
        right_start,
        right_end,
    )
    if overlap >= 0.95:
        return SourceRelation.EXACT_OR_NEAR_EXACT
    if overlap >= 0.8:
        return SourceRelation.STRONG_OVERLAP
    if overlap > 0:
        return SourceRelation.PARTIAL_OVERLAP
    return SourceRelation.SAME_MEDIA_DISTINCT_WINDOW
