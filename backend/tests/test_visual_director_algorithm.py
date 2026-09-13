from __future__ import annotations

import uuid

import pytest

from app.services.visual_director_algorithm import (
    StaticImageCandidate,
    VisualCandidate,
    deduplicate_candidates,
    direct_candidate,
    fuse_hybrid_candidates,
    normalize_query,
    overlap_ratio,
    rank_candidates,
)


def _image(score: float, rank: int = 1) -> StaticImageCandidate:
    return StaticImageCandidate(
        project_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        media_id=uuid.uuid4(),
        image_semantic_index_id=uuid.uuid4(),
        semantic_score=score,
        rank=rank,
        media_filename="official.png",
        content_type="image/png",
        byte_size=10,
        sha256="a" * 64,
        width=1920,
        height=1080,
        format_name="PNG",
        provenance={},
    )


def _candidate(
    start: float,
    end: float,
    score: float,
    *,
    session_id: uuid.UUID | None = None,
    media_id: uuid.UUID | None = None,
    matched_note_ids: tuple[uuid.UUID, ...] = (),
) -> VisualCandidate:
    return VisualCandidate(
        project_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        session_id=session_id or uuid.UUID("00000000-0000-4000-8000-000000000002"),
        media_id=media_id or uuid.UUID("00000000-0000-4000-8000-000000000003"),
        start_seconds=start,
        end_seconds=end,
        semantic_score=score,
        rank=1,
        session_label="Session",
        media_filename="001.mp4",
        matched_note_ids=matched_note_ids,
    )


def test_query_normalization_is_structural_and_deterministic() -> None:
    assert normalize_query("  Geralt\n learns   Aard ") == "Geralt learns Aard"
    with pytest.raises(ValueError, match="empty"):
        normalize_query(" \n ")


def test_overlapping_candidates_are_deduplicated_but_distant_events_remain() -> None:
    candidates = [
        _candidate(100, 120, 0.8),
        _candidate(102, 118, 0.9),
        _candidate(200, 220, 0.7),
    ]
    kept = deduplicate_candidates(candidates)
    assert [(item.start_seconds, item.end_seconds) for item in kept] == [
        (102, 118),
        (200, 220),
    ]


def test_scoring_has_exact_documented_components() -> None:
    session_id = uuid.uuid4()
    note_id = uuid.uuid4()
    ranked = rank_candidates(
        [_candidate(100, 120, 0.7, session_id=session_id, matched_note_ids=(note_id,))],
        preferred_session_id=session_id,
        source_note_id=note_id,
        target_duration_seconds=20,
        already_selected=[],
    )
    assert ranked[0].selection_score == 0.8
    assert ranked[0].score_components == {
        "semantic": 0.7,
        "preferred_session_bonus": 0.05,
        "source_note_bonus": 0.03,
        "duration_fit_bonus": 0.02,
        "reuse_overlap": 0.0,
        "reuse_penalty": 0.0,
        "exact_reuse_count": 0,
        "exact_reuse_penalty": 0.0,
        "consecutive_reuse_penalty": 0.0,
        "source_note_anchored": True,
        "semantic_band_penalty": 0.0,
    }


def test_reuse_penalty_prefers_a_distinct_similarly_relevant_window() -> None:
    previous = _candidate(100, 120, 0.9)
    ranked = rank_candidates(
        [_candidate(102, 118, 0.8), _candidate(200, 220, 0.79)],
        preferred_session_id=None,
        source_note_id=None,
        target_duration_seconds=20,
        already_selected=[previous],
    )
    assert ranked[0].start_seconds == 200
    assert ranked[1].score_components["reuse_penalty"] == 0.38


def test_exact_reuse_loses_to_semantically_close_distinct_candidate() -> None:
    previous = _candidate(100, 120, 0.50)
    ranked = rank_candidates(
        [_candidate(100, 120, 0.51), _candidate(200, 220, 0.48)],
        preferred_session_id=None,
        source_note_id=None,
        target_duration_seconds=None,
        already_selected=[previous],
    )
    assert ranked[0].start_seconds == 200


def test_evidence_query_does_not_trade_away_semantic_band_for_diversity() -> None:
    previous = _candidate(100, 120, 0.8)
    ranked = rank_candidates(
        [_candidate(100, 120, 0.70), _candidate(200, 220, 0.67)],
        preferred_session_id=None,
        source_note_id=None,
        target_duration_seconds=None,
        already_selected=[previous],
        intent="evidence",
    )
    assert ranked[0].start_seconds == 100
    assert ranked[1].score_components["semantic_band_penalty"] == 0.25


def test_trusted_provenance_is_hierarchical_and_not_diversity_penalized() -> None:
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()
    media_id = uuid.uuid4()
    trusted = direct_candidate(
        project_id=project_id,
        session_id=session_id,
        media_id=media_id,
        start_seconds=105,
        end_seconds=115,
        session_label="trusted",
        media_filename="trusted.mp4",
    )
    previous = _candidate(100, 120, 0.99, session_id=session_id, media_id=media_id)
    assert trusted.selection_strategy == "preferred_provenance"
    assert trusted.selection_score == 1.0
    assert overlap_ratio(trusted, previous) == 1.0


def test_hybrid_rrf_is_deterministic_and_does_not_compare_raw_cross_modal_scores() -> None:
    video = _candidate(0, 5, 0.10)
    image = _image(0.99)
    first = fuse_hybrid_candidates(
        [video],
        [image],
        already_selected=[],
        preferred_session_id=None,
        source_note_id=None,
        rrf_k=60,
        video_weight=1,
        image_weight=1,
        strategy_version="hybrid_visual_fusion_v1",
    )
    repeated = fuse_hybrid_candidates(
        [video],
        [image],
        already_selected=[],
        preferred_session_id=None,
        source_note_id=None,
        rrf_k=60,
        video_weight=1,
        image_weight=1,
        strategy_version="hybrid_visual_fusion_v1",
    )
    assert first == repeated
    assert isinstance(first[0], VisualCandidate)
    assert first[0].score_components["rrf_score"] == first[1].score_components["rrf_score"]


def test_hybrid_rrf_weights_and_reuse_penalty_allow_each_modality_to_win() -> None:
    video = _candidate(0, 5, 0.9)
    image = _image(0.1)
    image_first = fuse_hybrid_candidates(
        [video],
        [image],
        already_selected=[],
        preferred_session_id=None,
        source_note_id=None,
        rrf_k=60,
        video_weight=1,
        image_weight=2,
        strategy_version="hybrid_visual_fusion_v1",
    )
    assert isinstance(image_first[0], StaticImageCandidate)
    video_first = fuse_hybrid_candidates(
        [video],
        [image],
        already_selected=[image],
        preferred_session_id=None,
        source_note_id=None,
        rrf_k=60,
        video_weight=1,
        image_weight=1,
        strategy_version="hybrid_visual_fusion_v1",
    )
    assert isinstance(video_first[0], VisualCandidate)
