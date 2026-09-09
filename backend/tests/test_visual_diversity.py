from app.services.visual_diversity import (
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
    assert result.reasons == ("visual_plan_degenerate_reuse",)
    assert result.metrics["unique_source_window_count"] == 1
    assert result.metrics["reused_source_count"] == 21
    assert result.metrics["dominant_window_use_ratio"] == 1.0


def test_diverse_plan_passes_and_union_does_not_double_count_overlap() -> None:
    windows = [_window(index) for index in range(11)] * 2
    result = evaluate_visual_diversity(windows)
    assert result.status == "passed"
    assert result.metrics["unique_source_window_count"] == 11
    assert result.metrics["unique_source_duration_seconds"] == 220.0


def test_short_or_explicitly_anchored_reuse_is_not_blocked() -> None:
    assert evaluate_visual_diversity([_window()] * 2).status == "passed"
    assert evaluate_visual_diversity([_window(anchored=True)] * 22).status == "passed"
