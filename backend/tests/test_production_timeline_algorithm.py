from __future__ import annotations

import copy
import uuid

import pytest

from app.services.production_timeline_algorithm import (
    DecisionInput,
    SourceWindow,
    TimelinePolicy,
    TimelineValidationError,
    build_production_timeline,
    seconds_to_us,
    validate_production_timeline,
)

PROJECT_ID = uuid.UUID("30000000-0000-4000-8000-000000000001")
SESSION_ID = uuid.UUID("30000000-0000-4000-8000-000000000002")
MEDIA_ID = uuid.UUID("30000000-0000-4000-8000-000000000003")


def _source(
    start: float,
    end: float,
    *,
    media_id: uuid.UUID = MEDIA_ID,
    strategy: str = "media_search",
    provenance: dict | None = None,
) -> SourceWindow:
    return SourceWindow(
        project_id=PROJECT_ID,
        session_id=SESSION_ID,
        media_id=media_id,
        start_us=seconds_to_us(start),
        end_us=seconds_to_us(end),
        selection_strategy=strategy,
        provenance=provenance or {},
    )


def _decision(
    start: float | None,
    end: float | None,
    source: SourceWindow | None,
    *,
    query_id: uuid.UUID | None = None,
    decision_id: uuid.UUID | None = None,
    importance: str = "preferred",
    binding_status: str = "resolved",
    source_status: str = "resolved",
    order: int = 0,
    alternatives: tuple[SourceWindow, ...] = (),
) -> DecisionInput:
    return DecisionInput(
        decision_id=decision_id or uuid.uuid4(),
        visual_query_id=query_id or uuid.uuid4(),
        block_order=0,
        query_order=order,
        importance=importance,
        binding_status=binding_status,
        source_status=source_status,
        timeline_start_us=seconds_to_us(start) if start is not None else None,
        timeline_end_us=seconds_to_us(end) if end is not None else None,
        selected=source,
        alternatives=alternatives,
    )


def _policy(**changes) -> TimelinePolicy:
    values = {
        "minimum_visual_duration_us": 500_000,
        "maximum_sources_per_query": 2,
        "maximum_visual_queries": 100,
        "gap_fill_enabled": False,
        "coalesce_contiguous": True,
    }
    values.update(changes)
    return TimelinePolicy(**values)


def test_basic_timeline_exact_fit_and_explicit_edge_gaps() -> None:
    result = build_production_timeline(
        20,
        [_decision(5, 10, _source(100, 105))],
        _policy(),
    )
    placement = result.placements[0]
    assert (placement.timeline_start_us, placement.timeline_end_us) == (
        seconds_to_us(5),
        seconds_to_us(10),
    )
    assert (placement.source_start_us, placement.source_end_us) == (
        seconds_to_us(100),
        seconds_to_us(105),
    )
    assert placement.strategy == "direct_target_fit"
    assert [(gap.timeline_start_us, gap.timeline_end_us) for gap in result.gaps] == [
        (seconds_to_us(0), seconds_to_us(5)),
        (seconds_to_us(10), seconds_to_us(20)),
    ]
    assert result.visual_coverage_us == seconds_to_us(5)
    assert result.visual_gap_us == seconds_to_us(15)
    assert result.status == "completed_with_gaps"


def test_long_source_uses_center_anchor_and_direct_start_policies() -> None:
    centered = build_production_timeline(
        10, [_decision(0, 5, _source(100, 120))], _policy()
    ).placements[0]
    assert (centered.source_start_us, centered.source_end_us) == (
        seconds_to_us(107.5),
        seconds_to_us(112.5),
    )
    assert centered.strategy == "centered_trim"

    anchored = build_production_timeline(
        10,
        [
            _decision(
                0,
                4,
                _source(
                    100,
                    120,
                    provenance={"cluster_start_seconds": 103, "cluster_end_seconds": 105},
                ),
            )
        ],
        _policy(),
    ).placements[0]
    assert (anchored.source_start_us, anchored.source_end_us) == (
        seconds_to_us(102),
        seconds_to_us(106),
    )
    assert anchored.strategy == "anchor_trim"

    preferred = build_production_timeline(
        10,
        [_decision(0, 5, _source(100, 120, strategy="preferred_provenance"))],
        _policy(),
    ).placements[0]
    assert preferred.source_start_us == seconds_to_us(100)
    assert preferred.strategy == "preferred_start_trim"


def test_short_source_uses_bounded_alternative_without_slowdown_or_loop() -> None:
    result = build_production_timeline(
        12,
        [_decision(0, 10, _source(100, 104), alternatives=(_source(200, 206),))],
        _policy(),
    )
    assert len(result.placements) == 2
    assert [item.duration_us for item in result.placements] == [
        seconds_to_us(4),
        seconds_to_us(6),
    ]
    assert result.placements[0].strategy == "source_window_exhausted"
    assert result.placements[1].strategy == "alternative_fill"
    assert all(
        item.duration_us == item.source_end_us - item.source_start_us for item in result.placements
    )
    assert result.outcomes[0].status == "fully_placed"


def test_short_source_without_alternative_leaves_explicit_required_remainder() -> None:
    decision = _decision(0, 10, _source(100, 104), importance="required")
    result = build_production_timeline(10, [decision], _policy())
    assert result.placements[0].timeline_end_us == seconds_to_us(4)
    assert result.placements[0].playback_rate == 1.0
    assert result.outcomes[0].status == "partially_placed"
    assert result.outcomes[0].reason == "source_too_short"
    assert len(result.gaps) == 1
    assert result.gaps[0].reason == "source_too_short"
    assert result.gaps[0].severity == "required"


def test_required_query_wins_overlap_and_optional_remains_diagnosable() -> None:
    required = _decision(10, 20, _source(100, 110), importance="required", order=1)
    optional = _decision(15, 25, _source(200, 210), importance="optional", order=0)
    result = build_production_timeline(30, [optional, required], _policy())
    assert [(item.timeline_start_us, item.timeline_end_us) for item in result.placements] == [
        (seconds_to_us(10), seconds_to_us(20)),
        (seconds_to_us(20), seconds_to_us(25)),
    ]
    outcomes = {item.visual_query_id: item for item in result.outcomes}
    assert outcomes[required.visual_query_id].status == "fully_placed"
    assert outcomes[optional.visual_query_id].status == "partially_placed"
    assert outcomes[optional.visual_query_id].reason == "conflict"


def test_two_required_overlaps_use_stable_order_and_neither_disappears() -> None:
    first = _decision(10, 20, _source(100, 110), importance="required", order=0)
    second = _decision(15, 25, _source(200, 210), importance="required", order=1)
    result = build_production_timeline(30, [second, first], _policy())
    outcomes = {item.visual_query_id: item for item in result.outcomes}
    assert outcomes[first.visual_query_id].status == "fully_placed"
    assert outcomes[second.visual_query_id].status == "partially_placed"
    assert outcomes[second.visual_query_id].placed_duration_us == seconds_to_us(5)


def test_gap_fill_extends_only_inside_known_candidate_window() -> None:
    result = build_production_timeline(
        20,
        [_decision(5, 10, _source(100, 120))],
        _policy(gap_fill_enabled=True),
    )
    assert len(result.placements) == 1
    placement = result.placements[0]
    assert (placement.timeline_start_us, placement.timeline_end_us) == (
        seconds_to_us(0),
        seconds_to_us(17.5),
    )
    assert (placement.source_start_us, placement.source_end_us) == (
        seconds_to_us(102.5),
        seconds_to_us(120),
    )
    assert [(item.timeline_start_us, item.timeline_end_us) for item in result.gaps] == [
        (seconds_to_us(17.5), seconds_to_us(20))
    ]
    assert result.coverage_ratio == 0.875


def test_whole_video_gap_and_unresolved_required_query_are_explicit() -> None:
    decision = _decision(
        5,
        10,
        None,
        importance="required",
        source_status="unresolved",
    )
    result = build_production_timeline(20, [decision], _policy(gap_fill_enabled=True))
    assert result.placements == ()
    assert len(result.gaps) == 3
    assert [item.severity for item in result.gaps] == [
        "background",
        "required",
        "background",
    ]
    assert result.gaps[1].reason == "source_unresolved"
    assert result.required_gap_us == seconds_to_us(5)
    assert result.background_gap_us == seconds_to_us(15)
    assert result.outcomes[0].status == "unplaced"


def test_continuous_source_coalesces_and_discontinuous_source_does_not() -> None:
    first = _decision(0, 5, _source(100, 105), order=0)
    second = _decision(5, 10, _source(105, 110), order=1)
    continuous = build_production_timeline(10, [first, second], _policy())
    assert len(continuous.placements) == 1
    assert continuous.placements[0].strategy == "coalesced"
    assert set(continuous.placements[0].visual_query_ids) == {
        first.visual_query_id,
        second.visual_query_id,
    }
    assert continuous.placements[0].allowed_source_ranges == [
        (seconds_to_us(100), seconds_to_us(105)),
        (seconds_to_us(105), seconds_to_us(110)),
    ]

    discontinuous = build_production_timeline(
        10,
        [first, _decision(5, 10, _source(200, 205), order=1)],
        _policy(),
    )
    assert len(discontinuous.placements) == 2


def test_gameplay_time_never_becomes_final_timeline_time() -> None:
    result = build_production_timeline(
        400,
        [_decision(320, 328, _source(1200, 1240))],
        _policy(),
    )
    placement = result.placements[0]
    assert (placement.timeline_start_us, placement.timeline_end_us) == (
        seconds_to_us(320),
        seconds_to_us(328),
    )
    assert seconds_to_us(1200) <= placement.source_start_us
    assert placement.source_end_us <= seconds_to_us(1240)


def test_tiny_binding_is_not_turned_into_pathological_shot() -> None:
    decision = _decision(1, 1.05, _source(10, 20), importance="required")
    result = build_production_timeline(5, [decision], _policy())
    assert result.placements == ()
    assert result.outcomes[0].reason == "below_minimum_duration"


def test_unresolved_binding_never_invents_precise_placement() -> None:
    decision = _decision(
        None,
        None,
        _source(10, 20),
        importance="required",
        binding_status="ambiguous",
    )
    result = build_production_timeline(5, [decision], _policy())
    assert result.placements == ()
    assert result.outcomes[0].status == "unplaced"
    assert result.outcomes[0].reason == "narration_unresolved"


def test_validator_rejects_overlap_and_source_escape() -> None:
    result = build_production_timeline(
        10,
        [
            _decision(0, 5, _source(100, 105), order=0),
            _decision(5, 10, _source(200, 205), order=1),
        ],
        _policy(coalesce_contiguous=False),
    )
    escaped = copy.deepcopy(result)
    escaped.placements[0].source_start_us -= 1
    with pytest.raises(TimelineValidationError, match="outside Stage 14 candidate"):
        validate_production_timeline(escaped)

    overlapping = copy.deepcopy(result)
    overlapping.placements[1].timeline_start_us -= 1
    overlapping.placements[1].source_start_us -= 1
    with pytest.raises(TimelineValidationError, match="overlap"):
        validate_production_timeline(overlapping)

    invalid_rate = copy.deepcopy(result)
    invalid_rate.placements[0].playback_rate = 0.5
    with pytest.raises(TimelineValidationError, match="playback_rate=1.0"):
        validate_production_timeline(invalid_rate)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_seconds_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        seconds_to_us(value)


def test_repeated_planning_is_deterministic() -> None:
    decisions = [
        _decision(0, 4, _source(100, 110), order=0),
        _decision(4, 8, _source(200, 210), order=1),
    ]
    first = build_production_timeline(10, decisions, _policy())
    second = build_production_timeline(10, decisions, _policy())
    assert first == second


def test_query_count_guard_rejects_unbounded_planning() -> None:
    decisions = [
        _decision(index, index + 1, _source(100 + index, 101 + index), order=index)
        for index in range(3)
    ]
    with pytest.raises(ValueError, match="configured limit"):
        build_production_timeline(
            10,
            decisions,
            _policy(maximum_visual_queries=2),
        )
