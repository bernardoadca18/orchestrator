"""Pure deterministic policies used by the Stage 14 Visual Director."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    project_id: UUID
    session_id: UUID
    media_id: UUID
    start_seconds: float
    end_seconds: float
    semantic_score: float | None
    rank: int | None
    session_label: str
    media_filename: str
    temporal_index_id: UUID | None = None
    retrieval_candidate_id: UUID | None = None
    transcript_preview: str | None = None
    matched_note_ids: tuple[UUID, ...] = ()
    search_strategy: str | None = None
    provenance: dict | None = None
    selection_strategy: str = "media_search"
    selection_score: float = 0.0
    score_components: dict | None = None

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def normalize_query(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("visual query text must not be empty")
    if len(normalized) > 2_000:
        raise ValueError("visual query text exceeds 2000 characters")
    return normalized


def overlap_ratio(left: VisualCandidate, right: VisualCandidate) -> float:
    if left.session_id != right.session_id or left.media_id != right.media_id:
        return 0.0
    overlap = min(left.end_seconds, right.end_seconds) - max(
        left.start_seconds, right.start_seconds
    )
    if overlap <= 0:
        return 0.0
    return max(overlap / left.duration_seconds, overlap / right.duration_seconds)


def deduplicate_candidates(
    candidates: list[VisualCandidate], overlap_threshold: float = 0.5
) -> list[VisualCandidate]:
    """Keep the best of substantially overlapping ranges, deterministically."""
    ordered = sorted(
        candidates,
        key=lambda item: (
            -(item.semantic_score or 0.0),
            str(item.session_id),
            str(item.media_id),
            item.start_seconds,
            item.end_seconds,
        ),
    )
    kept: list[VisualCandidate] = []
    for candidate in ordered:
        if candidate.duration_seconds <= 0:
            continue
        if any(overlap_ratio(candidate, existing) > overlap_threshold for existing in kept):
            continue
        kept.append(candidate)
    return kept


def score_candidate(
    candidate: VisualCandidate,
    *,
    preferred_session_id: UUID | None,
    source_note_id: UUID | None,
    target_duration_seconds: float | None,
    already_selected: list[VisualCandidate],
) -> VisualCandidate:
    """Apply the documented weak bonuses and diversity penalty."""
    semantic = candidate.semantic_score or 0.0
    preferred_session_bonus = 0.05 if candidate.session_id == preferred_session_id else 0.0
    source_note_bonus = (
        0.03 if source_note_id is not None and source_note_id in candidate.matched_note_ids else 0.0
    )
    duration_fit = 0.0
    if target_duration_seconds and target_duration_seconds > 0 and candidate.duration_seconds > 0:
        ratio = min(
            candidate.duration_seconds / target_duration_seconds,
            target_duration_seconds / candidate.duration_seconds,
        )
        duration_fit = 0.02 * ratio
    reuse_overlap = max(
        (overlap_ratio(candidate, previous) for previous in already_selected), default=0.0
    )
    reuse_penalty = 0.08 * reuse_overlap
    score = max(
        0.0,
        min(
            1.0,
            semantic + preferred_session_bonus + source_note_bonus + duration_fit - reuse_penalty,
        ),
    )
    return replace(
        candidate,
        selection_score=round(score, 6),
        score_components={
            "semantic": round(semantic, 6),
            "preferred_session_bonus": round(preferred_session_bonus, 6),
            "source_note_bonus": round(source_note_bonus, 6),
            "duration_fit_bonus": round(duration_fit, 6),
            "reuse_overlap": round(reuse_overlap, 6),
            "reuse_penalty": round(reuse_penalty, 6),
        },
    )


def rank_candidates(
    candidates: list[VisualCandidate],
    *,
    preferred_session_id: UUID | None,
    source_note_id: UUID | None,
    target_duration_seconds: float | None,
    already_selected: list[VisualCandidate],
) -> list[VisualCandidate]:
    scored = [
        score_candidate(
            candidate,
            preferred_session_id=preferred_session_id,
            source_note_id=source_note_id,
            target_duration_seconds=target_duration_seconds,
            already_selected=already_selected,
        )
        for candidate in deduplicate_candidates(candidates)
    ]
    return sorted(
        scored,
        key=lambda item: (
            -item.selection_score,
            -(item.semantic_score or 0.0),
            str(item.session_id),
            str(item.media_id),
            item.start_seconds,
            item.end_seconds,
        ),
    )


def direct_candidate(
    *,
    project_id: UUID,
    session_id: UUID,
    media_id: UUID,
    start_seconds: float,
    end_seconds: float,
    session_label: str,
    media_filename: str,
) -> VisualCandidate:
    return VisualCandidate(
        project_id=project_id,
        session_id=session_id,
        media_id=media_id,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        semantic_score=None,
        rank=None,
        session_label=session_label,
        media_filename=media_filename,
        selection_strategy="preferred_provenance",
        selection_score=1.0,
        score_components={"trusted_preferred_provenance": 1.0},
        provenance={"source": "production_script"},
    )
