from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.workflow import ProductionTimelineOut, TimelineGameplaySourceOut

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "production_timeline_v2_contract.json"


def fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_canonical_v2_contract_fixture_serializes_video_then_image() -> None:
    timeline = ProductionTimelineOut.model_validate(fixture_payload())
    dumped = timeline.model_dump(mode="json")
    video, image = dumped["tracks"]["primary_visual"]["placements"]
    assert video["source"]["source_type"] == "video_segment"
    assert image["source"]["source_type"] == "static_image"
    assert image["playback_rate"] is None
    assert "source_start_seconds" not in image["source"]


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
@pytest.mark.parametrize(
    ("fit", "background"),
    [("cover", "black"), ("contain", "blurred_fill")],
)
@pytest.mark.parametrize("motion", ["none", "slow_zoom_in", "slow_zoom_out"])
def test_v2_contract_accepts_synchronized_image_enums(
    mime: str, fit: str, background: str, motion: str
) -> None:
    payload = fixture_payload()
    placement = payload["tracks"]["primary_visual"]["placements"][1]
    placement["source"]["content_type"] = mime
    placement["presentation"].update({"fit": fit, "background": background, "motion": motion})
    ProductionTimelineOut.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "invalid"),
    [
        (("source", "content_type"), "image/gif"),
        (("presentation", "fit"), "stretch"),
        (("presentation", "background"), "white"),
        (("presentation", "motion"), "pan"),
        (("presentation", "policy_version"), "static_image_presentation_v0"),
    ],
)
def test_v2_contract_rejects_unsynchronized_image_enums(
    path: tuple[str, str], invalid: str
) -> None:
    payload = fixture_payload()
    placement = payload["tracks"]["primary_visual"]["placements"][1]
    placement[path[0]][path[1]] = invalid
    with pytest.raises(ValidationError):
        ProductionTimelineOut.model_validate(payload)


def test_v2_union_rejects_gameplay_unknown_and_cross_type_fields() -> None:
    for source_type in ("gameplay", "unknown_source"):
        payload = fixture_payload()
        payload["tracks"]["primary_visual"]["placements"][0]["source"]["source_type"] = source_type
        with pytest.raises(ValidationError):
            ProductionTimelineOut.model_validate(payload)

    temporal_image = fixture_payload()
    temporal_image["tracks"]["primary_visual"]["placements"][1]["source"][
        "source_start_seconds"
    ] = 0
    with pytest.raises(ValidationError):
        ProductionTimelineOut.model_validate(temporal_image)

    incomplete_video = copy.deepcopy(fixture_payload())
    del incomplete_video["tracks"]["primary_visual"]["placements"][0]["source"][
        "source_end_seconds"
    ]
    with pytest.raises(ValidationError):
        ProductionTimelineOut.model_validate(incomplete_video)


def test_v1_gameplay_source_remains_valid_and_rejects_static_image() -> None:
    source = {
        "source_type": "gameplay",
        "project_id": "30000000-0000-4000-8000-000000000001",
        "session_id": "30000000-0000-4000-8000-000000000002",
        "media_id": "30000000-0000-4000-8000-000000000003",
        "candidate_start_seconds": 10.0,
        "candidate_end_seconds": 20.0,
        "source_start_seconds": 10.0,
        "source_end_seconds": 13.0,
        "provenance": {},
    }
    TimelineGameplaySourceOut.model_validate(source)
    source["source_type"] = "static_image"
    with pytest.raises(ValidationError):
        TimelineGameplaySourceOut.model_validate(source)
