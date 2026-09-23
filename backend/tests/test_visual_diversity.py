from app.services.visual_diversity import (
    VisualDiversityPolicy,
    VisualSourceWindow,
    evaluate_visual_diversity,
)


def _window(index: int = 0, *, anchored: bool = False) -> VisualSourceWindow:
    return VisualSourceWindow(
        session_id="session",
        media_id="media",
        start_seconds=float(index * 20),
        end_seconds=float(index * 20 + 20),
        timeline_duration_seconds=30.0,
        explicitly_anchored=anchored,
    )


def test_historical_style_duplicate_plan_fails() -> None:
    result = evaluate_visual_diversity([_window()] * 22)
    assert result.status == "failed"
    assert "visual_plan_degenerate_reuse" in result.reasons
    assert result.metrics["unique_source_window_count"] == 1
    assert result.metrics["reused_source_count"] == 21
    assert result.metrics["dominant_window_use_ratio"] == 1.0


def test_diverse_plan_passes_and_union_does_not_double_count_overlap() -> None:
    windows = [_window(index) for index in range(11)] * 2
    result = evaluate_visual_diversity(windows)
    assert result.status == "warning"
    assert result.metrics["unique_source_window_count"] == 11
    assert result.metrics["unique_source_duration_seconds"] == 220.0


def test_short_reuse_passes_but_extreme_anchored_reuse_blocks() -> None:
    assert evaluate_visual_diversity([_window()] * 2).status == "passed"
    assert evaluate_visual_diversity([_window(anchored=True)] * 22).status == "failed"


def test_overlapping_windows_are_near_duplicates_but_distant_windows_are_distinct() -> None:
    windows = [
        _window(),
        VisualSourceWindow("session", "media", 2, 19, 17),
        VisualSourceWindow("session", "media", 4, 23, 19),
        _window(10),
    ]
    result = evaluate_visual_diversity(windows)
    assert result.metrics["near_duplicate_count"] == 2
    assert result.metrics["overlap_cluster_count"] == 2
    assert result.metrics["partial_overlap_count"] == 0
    assert result.metrics["consecutive_reuse_count"] == 2


def test_occasional_reuse_passes_and_consecutive_reuse_blocks() -> None:
    occasional = evaluate_visual_diversity(
        [_window(0), _window(1), _window(0), _window(2), _window(3), _window(4)]
    )
    assert occasional.status == "passed"
    consecutive = evaluate_visual_diversity(
        [_window(0)] * 5 + [_window(index) for index in range(1, 5)]
    )
    assert consecutive.status == "failed"
    assert "visual_plan_excessive_consecutive_reuse" in consecutive.reasons


def _still(media_id: str, duration: float = 3.0) -> VisualSourceWindow:
    return VisualSourceWindow(
        session_id="",
        media_id=media_id,
        start_seconds=0,
        end_seconds=0,
        timeline_duration_seconds=duration,
        source_type="static_image",
    )


def test_multimodal_diversity_tracks_distribution_and_blocks_dominant_still() -> None:
    policy = VisualDiversityPolicy(
        version="visual_diversity_policy_v2",
        maximum_dominant_static_ratio=0.65,
        maximum_static_reuse_count=3,
        maximum_consecutive_static_seconds=15,
    )
    healthy = evaluate_visual_diversity(
        [_window(0), _still("one"), _window(1), _still("two")], policy
    )
    assert healthy.status == "passed"
    assert healthy.metrics["unique_static_image_count"] == 2
    assert healthy.metrics["video_duration_ratio"] == 0.909091

    dominant = evaluate_visual_diversity([_still("one", 8)] * 4, policy)
    assert dominant.status == "failed"
    assert "visual_plan_dominant_static_asset" in dominant.reasons
    assert "visual_plan_excessive_static_reuse" in dominant.reasons
    assert "visual_plan_excessive_consecutive_static" in dominant.reasons
