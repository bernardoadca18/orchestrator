"""Shared deterministic visual-source diversity metrics and gate."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VisualSourceWindow:
    session_id: str
    media_id: str
    start_seconds: float
    end_seconds: float
    timeline_duration_seconds: float = 1.0
    explicitly_anchored: bool = False
    source_type: str = "gameplay"

    @property
    def identity(self) -> tuple:
        if self.source_type == "static_image":
            return ("static_image", self.media_id)
        return (
            "video_segment",
            self.session_id,
            self.media_id,
            round(self.start_seconds, 3),
            round(self.end_seconds, 3),
        )


@dataclass(frozen=True, slots=True)
class VisualDiversityPolicy:
    version: str = "visual_diversity_policy_v1"
    minimum_window_count: int = 4
    maximum_reuse_ratio: float = 0.75
    maximum_dominant_window_ratio: float = 0.75
    maximum_dominant_static_ratio: float = 1.0
    maximum_static_reuse_count: int = 1_000_000
    maximum_consecutive_static_seconds: float = 1_000_000.0


@dataclass(frozen=True, slots=True)
class VisualDiversityEvaluation:
    status: str
    reasons: tuple[str, ...]
    metrics: dict[str, int | float]
    policy: VisualDiversityPolicy

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "metrics": self.metrics,
            "policy": asdict(self.policy),
        }


def _union_duration(windows: Iterable[VisualSourceWindow]) -> float:
    grouped: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for item in windows:
        if item.source_type == "static_image":
            continue
        grouped[(item.session_id, item.media_id)].append((item.start_seconds, item.end_seconds))
    total = 0.0
    for ranges in grouped.values():
        current_start: float | None = None
        current_end: float | None = None
        for start, end in sorted(ranges):
            if current_start is None:
                current_start, current_end = start, end
            elif start <= (current_end or start):
                current_end = max(current_end or end, end)
            else:
                total += (current_end or current_start) - current_start
                current_start, current_end = start, end
        if current_start is not None and current_end is not None:
            total += current_end - current_start
    return total


def evaluate_visual_diversity(
    windows: list[VisualSourceWindow],
    policy: VisualDiversityPolicy = VisualDiversityPolicy(),
) -> VisualDiversityEvaluation:
    count = len(windows)
    identities = Counter(item.identity for item in windows)
    unique_count = len(identities)
    max_use = max(identities.values(), default=0)
    reuse_count = max(0, count - unique_count)
    reuse_ratio = reuse_count / count if count else 0.0
    dominant_count_ratio = max_use / count if count else 0.0
    total_timeline = sum(max(0.0, item.timeline_duration_seconds) for item in windows)
    duration_by_identity: dict[tuple, float] = defaultdict(float)
    for item in windows:
        duration_by_identity[item.identity] += max(0.0, item.timeline_duration_seconds)
    dominant_duration_ratio = (
        max(duration_by_identity.values(), default=0.0) / total_timeline
        if total_timeline
        else dominant_count_ratio
    )
    video = [item for item in windows if item.source_type != "static_image"]
    images = [item for item in windows if item.source_type == "static_image"]
    video_duration = sum(max(0.0, item.timeline_duration_seconds) for item in video)
    image_duration = sum(max(0.0, item.timeline_duration_seconds) for item in images)
    image_counts = Counter(item.media_id for item in images)
    image_duration_by_asset: dict[str, float] = defaultdict(float)
    for item in images:
        image_duration_by_asset[item.media_id] += max(0.0, item.timeline_duration_seconds)
    longest_consecutive_static = 0.0
    current_static = 0.0
    longest_same_image = 0.0
    current_same_image = 0.0
    previous_image: str | None = None
    for item in windows:
        duration = max(0.0, item.timeline_duration_seconds)
        if item.source_type == "static_image":
            current_static += duration
            if previous_image == item.media_id:
                current_same_image += duration
            else:
                current_same_image = duration
            previous_image = item.media_id
        else:
            current_static = 0.0
            current_same_image = 0.0
            previous_image = None
        longest_consecutive_static = max(longest_consecutive_static, current_static)
        longest_same_image = max(longest_same_image, current_same_image)
    metrics: dict[str, int | float] = {
        "resolved_query_count": count,
        "unique_source_window_count": unique_count,
        "reused_source_count": reuse_count,
        "reuse_ratio": round(reuse_ratio, 6),
        "max_window_use_count": max_use,
        "dominant_window_use_ratio": round(dominant_count_ratio, 6),
        "dominant_window_timeline_ratio": round(dominant_duration_ratio, 6),
        "unique_media_count": len({item.media_id for item in windows}),
        "unique_session_count": len({item.session_id for item in windows}),
        "unique_source_duration_seconds": round(_union_duration(windows), 6),
        "unique_video_window_count": len({item.identity for item in video}),
        "video_window_reuse_count": max(0, len(video) - len({item.identity for item in video})),
        "unique_static_image_count": len(image_counts),
        "static_image_reuse_count": max(0, len(images) - len(image_counts)),
        "maximum_static_image_use_count": max(image_counts.values(), default=0),
        "video_duration_seconds": round(video_duration, 6),
        "static_image_duration_seconds": round(image_duration, 6),
        "video_duration_ratio": round(video_duration / total_timeline, 6)
        if total_timeline
        else 0.0,
        "static_image_duration_ratio": round(image_duration / total_timeline, 6)
        if total_timeline
        else 0.0,
        "dominant_static_asset_ratio": round(
            max(image_duration_by_asset.values(), default=0.0) / total_timeline, 6
        )
        if total_timeline
        else 0.0,
        "longest_consecutive_static_seconds": round(longest_consecutive_static, 6),
        "longest_consecutive_same_image_seconds": round(longest_same_image, 6),
        "unique_source_count": len({item.identity for item in windows}),
    }
    non_anchored = [item for item in windows if not item.explicitly_anchored]
    reasons: list[str] = []
    if len(non_anchored) >= policy.minimum_window_count:
        non_anchored_counts = Counter(item.identity for item in non_anchored)
        non_anchored_reuse = (len(non_anchored) - len(non_anchored_counts)) / len(non_anchored)
        non_anchored_dominant = max(non_anchored_counts.values()) / len(non_anchored)
        if len(non_anchored_counts) == 1:
            reasons.append("visual_plan_degenerate_reuse")
        elif (
            non_anchored_reuse > policy.maximum_reuse_ratio
            and non_anchored_dominant > policy.maximum_dominant_window_ratio
        ):
            reasons.append("visual_plan_insufficient_diversity")
    if images:
        dominant_static = float(metrics["dominant_static_asset_ratio"])
        if dominant_static > policy.maximum_dominant_static_ratio:
            reasons.append("visual_plan_dominant_static_asset")
        if max(image_counts.values(), default=0) > policy.maximum_static_reuse_count:
            reasons.append("visual_plan_excessive_static_reuse")
        if longest_consecutive_static > policy.maximum_consecutive_static_seconds:
            reasons.append("visual_plan_excessive_consecutive_static")
    return VisualDiversityEvaluation(
        status="failed" if reasons else "passed",
        reasons=tuple(reasons),
        metrics=metrics,
        policy=policy,
    )


def timeline_visual_diversity(
    placements: Iterable[Any],
    policy: VisualDiversityPolicy = VisualDiversityPolicy(),
) -> VisualDiversityEvaluation:
    windows = [
        VisualSourceWindow(
            session_id=str(item.source_session_id or ""),
            media_id=str(item.source_media_id),
            start_seconds=float(item.candidate_start_seconds or 0.0),
            end_seconds=float(item.candidate_end_seconds or 0.0),
            timeline_duration_seconds=(
                float(item.timeline_end_seconds) - float(item.timeline_start_seconds)
            ),
            explicitly_anchored=getattr(item, "strategy", None) == "preferred_provenance",
            source_type=str(getattr(item, "source_type", "gameplay")),
        )
        for item in placements
    ]
    return evaluate_visual_diversity(windows, policy)
