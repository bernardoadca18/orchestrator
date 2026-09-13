"""Pure deterministic Stage 15 planning and validation policies."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MICROSECONDS = 1_000_000
IMPORTANCE_ORDER = {"required": 0, "preferred": 1, "optional": 2}


class TimelinePlanningError(ValueError):
    pass


class TimelineValidationError(ValueError):
    pass


def seconds_to_us(value: float | int | str, field_name: str = "seconds") -> int:
    if isinstance(value, bool):
        raise TimelinePlanningError(f"{field_name} must be finite")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TimelinePlanningError(f"{field_name} must be finite") from exc
    if not parsed.is_finite():
        raise TimelinePlanningError(f"{field_name} must be finite")
    return int((parsed * MICROSECONDS).to_integral_value(rounding=ROUND_HALF_UP))


def us_to_seconds(value: int) -> float:
    return float(Decimal(value) / MICROSECONDS)


@dataclass(frozen=True, slots=True)
class TimelinePolicy:
    minimum_visual_duration_us: int = 500_000
    maximum_sources_per_query: int = 2
    maximum_visual_queries: int = 100
    gap_fill_enabled: bool = True
    coalesce_contiguous: bool = True
    default_transition: str = "cut"
    hybrid_enabled: bool = False
    static_image_minimum_hold_us: int = 2_000_000
    static_image_preferred_hold_us: int = 5_000_000
    static_image_maximum_hold_us: int = 8_000_000
    static_image_maximum_consecutive_us: int = 15_000_000

    def __post_init__(self) -> None:
        if self.minimum_visual_duration_us <= 0:
            raise TimelinePlanningError("minimum visual duration must be positive")
        if self.maximum_sources_per_query <= 0:
            raise TimelinePlanningError("maximum sources per query must be positive")
        if self.maximum_visual_queries <= 0:
            raise TimelinePlanningError("maximum visual queries must be positive")
        if self.default_transition != "cut":
            raise TimelinePlanningError("production timeline only supports cut")
        if not (
            0 < self.static_image_minimum_hold_us
            <= self.static_image_preferred_hold_us
            <= self.static_image_maximum_hold_us
            <= self.static_image_maximum_consecutive_us
        ):
            raise TimelinePlanningError("static-image hold policy is invalid")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "minimum_visual_duration_seconds": us_to_seconds(self.minimum_visual_duration_us),
            "maximum_sources_per_query": self.maximum_sources_per_query,
            "maximum_visual_queries": self.maximum_visual_queries,
            "gap_fill_enabled": self.gap_fill_enabled,
            "coalesce_contiguous": self.coalesce_contiguous,
            "default_transition": self.default_transition,
            "numeric_precision": "integer_microseconds",
        }
        if self.hybrid_enabled:
            result["static_image_holds_seconds"] = {
                "minimum": us_to_seconds(self.static_image_minimum_hold_us),
                "preferred": us_to_seconds(self.static_image_preferred_hold_us),
                "maximum": us_to_seconds(self.static_image_maximum_hold_us),
                "maximum_consecutive": us_to_seconds(
                    self.static_image_maximum_consecutive_us
                ),
            }
        return result


@dataclass(frozen=True, slots=True)
class SourceWindow:
    project_id: uuid.UUID
    session_id: uuid.UUID
    media_id: uuid.UUID
    start_us: int
    end_us: int
    selection_strategy: str
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us


@dataclass(frozen=True, slots=True)
class StaticImageSource:
    project_id: uuid.UUID
    media_id: uuid.UUID
    image_semantic_index_id: uuid.UUID
    sha256: str
    byte_size: int
    content_type: str
    width: int
    height: int
    selection_strategy: str
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionInput:
    decision_id: uuid.UUID
    visual_query_id: uuid.UUID
    block_order: int
    query_order: int
    importance: str
    binding_status: str
    source_status: str
    timeline_start_us: int | None
    timeline_end_us: int | None
    selected: SourceWindow | StaticImageSource | None
    alternatives: tuple[SourceWindow | StaticImageSource, ...] = ()


@dataclass(slots=True)
class PlacementDraft:
    visual_decision_id: uuid.UUID
    visual_decision_ids: list[uuid.UUID]
    visual_query_ids: list[uuid.UUID]
    timeline_start_us: int
    timeline_end_us: int
    source: SourceWindow | StaticImageSource
    source_start_us: int | None
    source_end_us: int | None
    strategy: str
    importance: str
    source_provenance: dict[str, Any]
    allowed_source_ranges: list[tuple[int, int]]
    playback_rate: float = 1.0
    transition: str = "cut"
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_us(self) -> int:
        return self.timeline_end_us - self.timeline_start_us


@dataclass(frozen=True, slots=True)
class GapDraft:
    timeline_start_us: int
    timeline_end_us: int
    reason: str
    severity: str
    related_visual_query_ids: tuple[uuid.UUID, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_us(self) -> int:
        return self.timeline_end_us - self.timeline_start_us


@dataclass(frozen=True, slots=True)
class QueryPlacementOutcome:
    visual_decision_id: uuid.UUID
    visual_query_id: uuid.UUID
    importance: str
    requested_start_us: int | None
    requested_end_us: int | None
    requested_duration_us: int
    placed_duration_us: int
    coverage_ratio: float
    status: str
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_decision_id": str(self.visual_decision_id),
            "visual_query_id": str(self.visual_query_id),
            "importance": self.importance,
            "requested_start_seconds": us_to_seconds(self.requested_start_us)
            if self.requested_start_us is not None
            else None,
            "requested_end_seconds": us_to_seconds(self.requested_end_us)
            if self.requested_end_us is not None
            else None,
            "requested_duration_seconds": us_to_seconds(self.requested_duration_us),
            "placed_duration_seconds": us_to_seconds(self.placed_duration_us),
            "coverage_ratio": self.coverage_ratio,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TimelinePlanResult:
    duration_us: int
    placements: tuple[PlacementDraft, ...]
    gaps: tuple[GapDraft, ...]
    outcomes: tuple[QueryPlacementOutcome, ...]
    visual_coverage_us: int
    visual_gap_us: int
    coverage_ratio: float
    required_gap_us: int
    preferred_gap_us: int
    optional_gap_us: int
    background_gap_us: int
    cut_count: int
    average_shot_duration_seconds: float | None
    status: str


def _validate_source(source: SourceWindow | StaticImageSource) -> None:
    if isinstance(source, SourceWindow) and (
        source.start_us < 0 or source.end_us <= source.start_us
    ):
        raise TimelinePlanningError("Stage 14 source window is invalid")


def _free_intervals(start: int, end: int, occupied: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cursor = start
    result: list[tuple[int, int]] = []
    for occupied_start, occupied_end in occupied:
        if occupied_end <= cursor:
            continue
        if occupied_start >= end:
            break
        if occupied_start > cursor:
            result.append((cursor, min(occupied_start, end)))
        cursor = max(cursor, occupied_end)
        if cursor >= end:
            break
    if cursor < end:
        result.append((cursor, end))
    return result


def _add_occupied(occupied: list[tuple[int, int]], start: int, end: int) -> None:
    occupied.append((start, end))
    occupied.sort()


def _semantic_anchor_us(source: SourceWindow) -> int | None:
    raw_start = source.provenance.get("cluster_start_seconds")
    raw_end = source.provenance.get("cluster_end_seconds")
    if raw_start is None or raw_end is None:
        return None
    try:
        start = seconds_to_us(raw_start, "cluster_start_seconds")
        end = seconds_to_us(raw_end, "cluster_end_seconds")
    except TimelinePlanningError:
        return None
    if end <= start or end <= source.start_us or start >= source.end_us:
        return None
    return (max(start, source.start_us) + min(end, source.end_us)) // 2


def _trim_source(source: SourceWindow, duration_us: int) -> tuple[int, int, str]:
    _validate_source(source)
    if duration_us <= 0 or duration_us > source.duration_us:
        raise TimelinePlanningError("requested trim does not fit source window")
    if duration_us == source.duration_us:
        return source.start_us, source.end_us, "direct_target_fit"
    if source.selection_strategy == "preferred_provenance":
        start = source.start_us
        return start, start + duration_us, "preferred_start_trim"
    anchor = _semantic_anchor_us(source)
    if anchor is not None:
        start = max(source.start_us, min(anchor - duration_us // 2, source.end_us - duration_us))
        return start, start + duration_us, "anchor_trim"
    start = source.start_us + (source.duration_us - duration_us) // 2
    return start, start + duration_us, "centered_trim"


def _importance_key(value: str) -> int:
    try:
        return IMPORTANCE_ORDER[value]
    except KeyError as exc:
        raise TimelinePlanningError(f"unsupported VisualQuery importance: {value}") from exc


def _decision_priority(item: DecisionInput) -> tuple[int, int, int, str]:
    return (
        _importance_key(item.importance),
        item.block_order,
        item.query_order,
        str(item.visual_query_id),
    )


def _plan_decision_sources(
    decision: DecisionInput,
    start_us: int,
    end_us: int,
    policy: TimelinePolicy,
) -> list[PlacementDraft]:
    sources = ([decision.selected] if decision.selected is not None else []) + list(
        decision.alternatives
    )
    placements: list[PlacementDraft] = []
    cursor = start_us
    for source_index, source in enumerate(sources[: policy.maximum_sources_per_query]):
        if cursor >= end_us:
            break
        assert source is not None
        _validate_source(source)
        remaining = end_us - cursor
        if isinstance(source, StaticImageSource):
            duration = min(
                remaining,
                policy.static_image_maximum_hold_us
                if source_index == len(sources[: policy.maximum_sources_per_query]) - 1
                else policy.static_image_preferred_hold_us,
            )
            if duration < max(
                policy.minimum_visual_duration_us, policy.static_image_minimum_hold_us
            ):
                continue
            source_start = None
            source_end = None
            trim_strategy = "static_image_hold"
            allowed_ranges: list[tuple[int, int]] = []
        else:
            duration = min(remaining, source.duration_us)
            if duration < policy.minimum_visual_duration_us:
                continue
            source_start, source_end, trim_strategy = _trim_source(source, duration)
            allowed_ranges = [(source.start_us, source.end_us)]
        placements.append(
            PlacementDraft(
                visual_decision_id=decision.decision_id,
                visual_decision_ids=[decision.decision_id],
                visual_query_ids=[decision.visual_query_id],
                timeline_start_us=cursor,
                timeline_end_us=cursor + duration,
                source=source,
                source_start_us=source_start,
                source_end_us=source_end,
                strategy=(
                    "alternative_fill"
                    if source_index > 0
                    else "source_window_exhausted"
                    if duration < remaining
                    else trim_strategy
                ),
                importance=decision.importance,
                source_provenance=dict(source.provenance),
                allowed_source_ranges=allowed_ranges,
                diagnostics={
                    "source_index": source_index,
                    "trim_strategy": trim_strategy,
                    "target_start_seconds": us_to_seconds(start_us),
                    "target_end_seconds": us_to_seconds(end_us),
                },
            )
        )
        cursor += duration
    return placements


def _complement(duration_us: int, placements: list[PlacementDraft]) -> list[tuple[int, int]]:
    cursor = 0
    gaps: list[tuple[int, int]] = []
    for placement in sorted(placements, key=lambda item: item.timeline_start_us):
        if placement.timeline_start_us > cursor:
            gaps.append((cursor, placement.timeline_start_us))
        cursor = max(cursor, placement.timeline_end_us)
    if cursor < duration_us:
        gaps.append((cursor, duration_us))
    return gaps


def _mark_extension(placement: PlacementDraft, direction: str, duration_us: int) -> None:
    extensions = placement.diagnostics.setdefault("fallback_extensions", [])
    extensions.append({"direction": direction, "duration_seconds": us_to_seconds(duration_us)})


def _fill_visual_gaps(duration_us: int, placements: list[PlacementDraft]) -> None:
    if not placements:
        return
    placements.sort(key=lambda item: (item.timeline_start_us, item.timeline_end_us))
    gaps = _complement(duration_us, placements)
    for gap_start, gap_end in gaps:
        previous = next(
            (item for item in reversed(placements) if item.timeline_end_us == gap_start), None
        )
        following = next((item for item in placements if item.timeline_start_us == gap_end), None)
        cursor = gap_start
        if previous is not None and isinstance(previous.source, SourceWindow):
            assert previous.source_end_us is not None
            amount = min(gap_end - cursor, previous.source.end_us - previous.source_end_us)
            if amount > 0:
                previous.timeline_end_us += amount
                previous.source_end_us += amount
                cursor += amount
                _mark_extension(previous, "forward", amount)
        if (
            cursor < gap_end
            and following is not None
            and isinstance(following.source, SourceWindow)
        ):
            assert following.source_start_us is not None
            amount = min(gap_end - cursor, following.source_start_us - following.source.start_us)
            if amount > 0:
                following.timeline_start_us -= amount
                following.source_start_us -= amount
                cursor += amount
                _mark_extension(following, "backward", amount)


def _can_coalesce(left: PlacementDraft, right: PlacementDraft) -> bool:
    return (
        isinstance(left.source, SourceWindow)
        and isinstance(right.source, SourceWindow)
        and
        left.timeline_end_us == right.timeline_start_us
        and left.playback_rate == right.playback_rate
        and left.transition == right.transition
        and left.source.project_id == right.source.project_id
        and left.source.session_id == right.source.session_id
        and left.source.media_id == right.source.media_id
        and left.source_end_us == right.source_start_us
    )


def _coalesce(placements: list[PlacementDraft]) -> list[PlacementDraft]:
    if not placements:
        return []
    ordered = sorted(placements, key=lambda item: (item.timeline_start_us, item.timeline_end_us))
    result = [ordered[0]]
    for current in ordered[1:]:
        previous = result[-1]
        if not _can_coalesce(previous, current):
            result.append(current)
            continue
        previous.timeline_end_us = current.timeline_end_us
        previous.source_end_us = current.source_end_us
        previous.visual_decision_ids = sorted(
            set(previous.visual_decision_ids + current.visual_decision_ids), key=str
        )
        previous.visual_query_ids = sorted(
            set(previous.visual_query_ids + current.visual_query_ids), key=str
        )
        previous.strategy = "coalesced"
        previous.allowed_source_ranges = sorted(
            set(previous.allowed_source_ranges + current.allowed_source_ranges)
        )
        if _importance_key(current.importance) < _importance_key(previous.importance):
            previous.importance = current.importance
        assert isinstance(previous.source, SourceWindow)
        assert isinstance(current.source, SourceWindow)
        previous.source = SourceWindow(
            project_id=previous.source.project_id,
            session_id=previous.source.session_id,
            media_id=previous.source.media_id,
            start_us=min(previous.source.start_us, current.source.start_us),
            end_us=max(previous.source.end_us, current.source.end_us),
            selection_strategy=previous.source.selection_strategy,
            provenance=previous.source.provenance,
        )
        previous.diagnostics.setdefault("coalesced_strategies", []).append(current.strategy)
    return result


def _apply_static_hold_policy(
    placements: list[PlacementDraft], policy: TimelinePolicy
) -> list[PlacementDraft]:
    result: list[PlacementDraft] = []
    consecutive_us = 0
    previous_end: int | None = None
    for placement in placements:
        if isinstance(placement.source, SourceWindow):
            consecutive_us = 0
            previous_end = placement.timeline_end_us
            result.append(placement)
            continue
        if previous_end != placement.timeline_start_us:
            consecutive_us = 0
        allowed = min(
            placement.duration_us,
            policy.static_image_maximum_hold_us,
            policy.static_image_maximum_consecutive_us - consecutive_us,
        )
        if allowed < max(
            policy.minimum_visual_duration_us, policy.static_image_minimum_hold_us
        ):
            previous_end = placement.timeline_start_us
            continue
        if allowed < placement.duration_us:
            placement.timeline_end_us = placement.timeline_start_us + allowed
            placement.diagnostics["static_hold_truncated"] = True
        consecutive_us += allowed
        previous_end = placement.timeline_end_us
        result.append(placement)
    return result


def _overlap_us(start: int, end: int, other_start: int, other_end: int) -> int:
    return max(0, min(end, other_end) - max(start, other_start))


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _query_outcomes(
    decisions: list[DecisionInput], placements: list[PlacementDraft], reasons: dict[uuid.UUID, str]
) -> list[QueryPlacementOutcome]:
    outcomes: list[QueryPlacementOutcome] = []
    for decision in sorted(
        decisions,
        key=lambda item: (item.block_order, item.query_order, str(item.visual_query_id)),
    ):
        start = decision.timeline_start_us
        end = decision.timeline_end_us
        requested = end - start if start is not None and end is not None else 0
        placed = 0
        if start is not None and end is not None:
            placed = sum(
                _overlap_us(start, end, item.timeline_start_us, item.timeline_end_us)
                for item in placements
                if decision.visual_query_id in item.visual_query_ids
            )
            placed = min(requested, placed)
        if decision.binding_status == "not_applicable":
            status = "not_applicable"
        elif requested <= 0 or placed == 0:
            status = "unplaced"
        elif placed >= requested:
            status = "fully_placed"
        else:
            status = "partially_placed"
        ratio = round(placed / requested, 6) if requested > 0 else 0.0
        outcomes.append(
            QueryPlacementOutcome(
                visual_decision_id=decision.decision_id,
                visual_query_id=decision.visual_query_id,
                importance=decision.importance,
                requested_start_us=start,
                requested_end_us=end,
                requested_duration_us=requested,
                placed_duration_us=placed,
                coverage_ratio=ratio,
                status=status,
                reason=None if status == "fully_placed" else reasons.get(decision.decision_id),
            )
        )
    return outcomes


def _gap_details(
    start: int,
    end: int,
    decisions: list[DecisionInput],
    reasons: dict[uuid.UUID, str],
) -> tuple[str, str, tuple[uuid.UUID, ...]]:
    related = [
        item
        for item in decisions
        if item.timeline_start_us is not None
        and item.timeline_end_us is not None
        and _overlap_us(start, end, item.timeline_start_us, item.timeline_end_us) > 0
    ]
    if not related:
        return "no_visual_query", "background", ()
    related.sort(key=_decision_priority)
    severity = related[0].importance
    reason_values = {reasons.get(item.decision_id) for item in related}
    for reason in (
        "source_unresolved",
        "narration_unresolved",
        "source_too_short",
        "conflict",
        "below_minimum_duration",
    ):
        if reason in reason_values:
            return reason, severity, tuple(item.visual_query_id for item in related)
    return "source_too_short", severity, tuple(item.visual_query_id for item in related)


def _annotated_gaps(
    ranges: list[tuple[int, int]],
    decisions: list[DecisionInput],
    reasons: dict[uuid.UUID, str],
) -> list[GapDraft]:
    gaps: list[GapDraft] = []
    for start, end in ranges:
        boundaries = {start, end}
        for decision in decisions:
            if decision.timeline_start_us is not None and start < decision.timeline_start_us < end:
                boundaries.add(decision.timeline_start_us)
            if decision.timeline_end_us is not None and start < decision.timeline_end_us < end:
                boundaries.add(decision.timeline_end_us)
        ordered = sorted(boundaries)
        for index in range(len(ordered) - 1):
            item_start = ordered[index]
            item_end = ordered[index + 1]
            gaps.append(
                GapDraft(
                    item_start,
                    item_end,
                    *_gap_details(item_start, item_end, decisions, reasons),
                )
            )
    return gaps


def build_production_timeline(
    duration_seconds: float,
    decisions: list[DecisionInput],
    policy: TimelinePolicy,
) -> TimelinePlanResult:
    duration_us = seconds_to_us(duration_seconds, "final narration duration")
    if duration_us <= 0:
        raise TimelinePlanningError("final narration duration must be positive")
    if len(decisions) > policy.maximum_visual_queries:
        raise TimelinePlanningError(
            f"VisualDecision count exceeds configured limit ({policy.maximum_visual_queries})"
        )
    occupied: list[tuple[int, int]] = []
    placements: list[PlacementDraft] = []
    reasons: dict[uuid.UUID, str] = {}

    for decision in sorted(decisions, key=_decision_priority):
        _importance_key(decision.importance)
        if decision.binding_status != "resolved":
            reasons[decision.decision_id] = (
                "narration_not_applicable"
                if decision.binding_status == "not_applicable"
                else "narration_unresolved"
            )
            continue
        if decision.timeline_start_us is None or decision.timeline_end_us is None:
            raise TimelinePlanningError("resolved narration binding has no range")
        start = decision.timeline_start_us
        end = decision.timeline_end_us
        if start < 0 or end <= start or end > duration_us:
            raise TimelinePlanningError("narration binding is outside final duration")
        if end - start < policy.minimum_visual_duration_us:
            reasons[decision.decision_id] = "below_minimum_duration"
            continue
        if decision.source_status != "resolved" or decision.selected is None:
            reasons[decision.decision_id] = "source_unresolved"
            continue
        free = _free_intervals(start, end, occupied)
        if not free:
            reasons[decision.decision_id] = "conflict"
            continue
        allocated_start, allocated_end = sorted(
            free, key=lambda value: (-(value[1] - value[0]), value[0])
        )[0]
        if allocated_end - allocated_start < policy.minimum_visual_duration_us:
            reasons[decision.decision_id] = "conflict"
            continue
        produced = _plan_decision_sources(decision, allocated_start, allocated_end, policy)
        if not produced:
            reasons[decision.decision_id] = "source_too_short"
            continue
        for placement in produced:
            placements.append(placement)
            _add_occupied(occupied, placement.timeline_start_us, placement.timeline_end_us)
        if produced[-1].timeline_end_us < allocated_end:
            reasons[decision.decision_id] = "source_too_short"
        elif allocated_start != start or allocated_end != end:
            reasons[decision.decision_id] = "conflict"

    if policy.gap_fill_enabled:
        _fill_visual_gaps(duration_us, placements)
    placements.sort(key=lambda item: (item.timeline_start_us, item.timeline_end_us))
    if policy.coalesce_contiguous:
        placements = _coalesce(placements)
    if policy.hybrid_enabled:
        placements = _apply_static_hold_policy(placements, policy)

    outcomes = _query_outcomes(decisions, placements, reasons)
    gap_ranges = _complement(duration_us, placements)
    gaps = _annotated_gaps(gap_ranges, decisions, reasons)
    covered = sum(item.duration_us for item in placements)
    gap_duration = sum(item.duration_us for item in gaps)
    coverage_ratio = round(covered / duration_us, 6)
    severity_totals = {
        severity: sum(item.duration_us for item in gaps if item.severity == severity)
        for severity in ("required", "preferred", "optional", "background")
    }
    cut_count = max(0, len(placements) - 1)
    average = round(us_to_seconds(covered) / len(placements), 6) if placements else None
    has_query_gap = any(item.status not in {"fully_placed", "not_applicable"} for item in outcomes)
    status = "completed" if not gaps and not has_query_gap else "completed_with_gaps"
    result = TimelinePlanResult(
        duration_us=duration_us,
        placements=tuple(placements),
        gaps=tuple(gaps),
        outcomes=tuple(outcomes),
        visual_coverage_us=covered,
        visual_gap_us=gap_duration,
        coverage_ratio=coverage_ratio,
        required_gap_us=severity_totals["required"],
        preferred_gap_us=severity_totals["preferred"],
        optional_gap_us=severity_totals["optional"],
        background_gap_us=severity_totals["background"],
        cut_count=cut_count,
        average_shot_duration_seconds=average,
        status=status,
    )
    validate_production_timeline(result)
    return result


def validate_production_timeline(result: TimelinePlanResult) -> None:
    if result.duration_us <= 0:
        raise TimelineValidationError("timeline duration must be positive")
    previous_end = 0
    for placement in result.placements:
        values = (placement.timeline_start_us, placement.timeline_end_us)
        if any(not isinstance(value, int) for value in values):
            raise TimelineValidationError("timeline precision is not integral microseconds")
        if placement.timeline_start_us < previous_end:
            raise TimelineValidationError("primary visual placements overlap")
        if (
            placement.timeline_start_us < 0
            or placement.timeline_end_us <= placement.timeline_start_us
            or placement.timeline_end_us > result.duration_us
        ):
            raise TimelineValidationError("placement is outside timeline duration")
        if isinstance(placement.source, SourceWindow):
            if placement.source_start_us is None or placement.source_end_us is None:
                raise TimelineValidationError("video source trim is missing")
            if (
                placement.source_start_us < placement.source.start_us
                or placement.source_end_us > placement.source.end_us
                or placement.source_end_us <= placement.source_start_us
            ):
                raise TimelineValidationError("source trim is outside Stage 14 candidate")
            allowed = _merge_ranges(placement.allowed_source_ranges)
            if not any(
                start <= placement.source_start_us and end >= placement.source_end_us
                for start, end in allowed
            ):
                raise TimelineValidationError(
                    "source trim is not covered by contributing Stage 14 candidates"
                )
            if placement.duration_us != placement.source_end_us - placement.source_start_us:
                raise TimelineValidationError("playback_rate=1.0 duration identity is invalid")
            if not math.isfinite(placement.playback_rate) or placement.playback_rate != 1.0:
                raise TimelineValidationError("video source requires playback_rate=1.0")
        else:
            if placement.source_start_us is not None or placement.source_end_us is not None:
                raise TimelineValidationError("static image cannot carry temporal source fields")
            if placement.allowed_source_ranges:
                raise TimelineValidationError("static image cannot carry candidate ranges")
        if placement.transition != "cut":
            raise TimelineValidationError("production timeline requires cut transitions")
        previous_end = placement.timeline_end_us

    expected_gaps = _complement(result.duration_us, list(result.placements))
    actual_gaps = [(item.timeline_start_us, item.timeline_end_us) for item in result.gaps]
    previous_gap_end = 0
    for gap in result.gaps:
        if (
            gap.timeline_start_us < previous_gap_end
            or gap.timeline_start_us < 0
            or gap.timeline_end_us <= gap.timeline_start_us
            or gap.timeline_end_us > result.duration_us
        ):
            raise TimelineValidationError("explicit gap range is invalid")
        previous_gap_end = gap.timeline_end_us
    if _merge_ranges(actual_gaps) != expected_gaps:
        raise TimelineValidationError("explicit gaps do not match visual complement")
    if result.visual_coverage_us != sum(item.duration_us for item in result.placements):
        raise TimelineValidationError("visual coverage metric is inconsistent")
    if result.visual_gap_us != sum(item.duration_us for item in result.gaps):
        raise TimelineValidationError("visual gap metric is inconsistent")
    if result.visual_coverage_us + result.visual_gap_us != result.duration_us:
        raise TimelineValidationError("coverage and gaps do not span narration")
    if not math.isclose(
        result.coverage_ratio,
        result.visual_coverage_us / result.duration_us,
        abs_tol=0.000001,
    ):
        raise TimelineValidationError("coverage ratio is inconsistent")
    severity_total = (
        result.required_gap_us
        + result.preferred_gap_us
        + result.optional_gap_us
        + result.background_gap_us
    )
    if severity_total != result.visual_gap_us:
        raise TimelineValidationError("gap severity metrics are inconsistent")
    for outcome in result.outcomes:
        if not 0 <= outcome.coverage_ratio <= 1:
            raise TimelineValidationError("query coverage ratio is invalid")
