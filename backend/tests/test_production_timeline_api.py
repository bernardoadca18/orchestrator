from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.workflow import ProductionTimeline, TimelineGap, TimelinePlacement
from app.schemas.workflow import PresentationSpecOut

PRESENTATION_FIXTURE = Path(__file__).parent / "fixtures" / "presentation_spec_v1_contract.json"


def _timeline() -> ProductionTimeline:
    workflow_id = uuid.uuid4()
    timeline = ProductionTimeline(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        narration_alignment_id=uuid.uuid4(),
        visual_plan_id=uuid.uuid4(),
        final_audio_sha256="a" * 64,
        narration_sample_rate=48_000,
        narration_channels=1,
        narration_byte_size=1000,
        script_fingerprint="b" * 64,
        strategy_version="production_timeline_v1",
        schema_version="production_timeline_schema_v1",
        validator_version="production_timeline_validator_v1",
        policy={"numeric_precision": "integer_microseconds"},
        request_fingerprint="c" * 64,
        fingerprint="d" * 64,
        status="completed_with_gaps",
        duration_seconds=20,
        visual_coverage_seconds=8,
        visual_gap_seconds=12,
        visual_coverage_ratio=0.4,
        placement_count=1,
        gap_count=1,
        cut_count=0,
        unplaced_query_count=0,
        required_gap_seconds=0,
        preferred_gap_seconds=0,
        optional_gap_seconds=0,
        background_gap_seconds=12,
        average_shot_duration_seconds=8,
        metrics={"planning_time_ms": 0.4},
        diagnostics={
            "query_placement_outcomes": [
                {
                    "visual_decision_id": str(uuid.uuid4()),
                    "visual_query_id": str(uuid.uuid4()),
                    "importance": "required",
                    "requested_start_seconds": 2,
                    "requested_end_seconds": 10,
                    "requested_duration_seconds": 8,
                    "placed_duration_seconds": 8,
                    "coverage_ratio": 1,
                    "status": "fully_placed",
                    "reason": None,
                }
            ]
        },
        criado_em=dt.datetime.now(dt.UTC),
    )
    decision_id = uuid.uuid4()
    query_id = uuid.uuid4()
    timeline.placements = [
        TimelinePlacement(
            id=uuid.uuid4(),
            production_timeline_id=timeline.id,
            order=0,
            visual_decision_id=decision_id,
            visual_decision_ids=[str(decision_id)],
            visual_query_ids=[str(query_id)],
            source_project_id=uuid.uuid4(),
            source_session_id=uuid.uuid4(),
            source_media_id=uuid.uuid4(),
            timeline_start_seconds=2,
            timeline_end_seconds=10,
            candidate_start_seconds=1200,
            candidate_end_seconds=1240,
            source_start_seconds=1216,
            source_end_seconds=1224,
            playback_rate=1,
            transition="cut",
            strategy="centered_trim",
            importance="required",
            source_provenance={"temporal_index_id": str(uuid.uuid4())},
        )
    ]
    timeline.gaps = [
        TimelineGap(
            id=uuid.uuid4(),
            production_timeline_id=timeline.id,
            order=0,
            timeline_start_seconds=10,
            timeline_end_seconds=20,
            reason="no_visual_query",
            severity="background",
            related_visual_query_ids=[],
        )
    ]
    return timeline


def test_production_timeline_get_post_and_force_are_typed_and_side_effect_explicit() -> None:
    timeline = _timeline()
    service = MagicMock()
    service.current = AsyncMock(return_value=timeline)
    service.ensure = AsyncMock(return_value=(timeline, False))

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.ProductionTimelineService", return_value=service),
            TestClient(app) as client,
        ):
            fetched = client.get(f"/workflow/{timeline.workflow_execucao_id}/production-timeline")
            rebuilt = client.post(
                f"/workflow/{timeline.workflow_execucao_id}/production-timeline?force=true"
            )
    finally:
        app.dependency_overrides.clear()

    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["tracks"]["narration"]["timeline_start_seconds"] == 0
    placement = payload["tracks"]["primary_visual"]["placements"][0]
    assert placement["timeline_start_seconds"] == 2
    assert placement["source"]["source_start_seconds"] == 1216
    assert placement["source"]["candidate_start_seconds"] == 1200
    assert "audio_final_path" not in str(payload)
    assert rebuilt.status_code == 200
    service.current.assert_awaited_once_with(timeline.workflow_execucao_id)
    service.ensure.assert_awaited_once_with(timeline.workflow_execucao_id, force=True)


def test_production_timeline_get_returns_404_without_current_artifact() -> None:
    service = MagicMock()
    service.current = AsyncMock(return_value=None)

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.ProductionTimelineService", return_value=service),
            TestClient(app) as client,
        ):
            response = client.get(f"/workflow/{uuid.uuid4()}/production-timeline")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_composer_render_input_returns_one_coherent_versioned_package() -> None:
    timeline = _timeline()
    timeline_service = MagicMock()
    timeline_service.current = AsyncMock(return_value=timeline)
    workflow_repository = MagicMock()
    workflow_repository.obter_por_id = AsyncMock(return_value=MagicMock())
    presentation = PresentationSpecOut.model_validate_json(
        PRESENTATION_FIXTURE.read_text(encoding="utf-8")
    )

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch(
                "app.api.routers.workflow.ProductionTimelineService",
                return_value=timeline_service,
            ),
            patch(
                "app.api.routers.workflow.WorkflowRepository",
                return_value=workflow_repository,
            ),
            patch(
                "app.api.routers.workflow.build_presentation_spec",
                return_value=presentation,
            ),
            TestClient(app) as client,
        ):
            response = client.get(
                f"/workflow/{timeline.workflow_execucao_id}/composer-render-input"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "composer_render_input_v1"
    assert payload["production_timeline"]["fingerprint"] == timeline.fingerprint
    assert payload["presentation"]["fingerprint"] == presentation.fingerprint
    workflow_repository.obter_por_id.assert_awaited_once_with(
        timeline.workflow_execucao_id, com_blocos=True, com_transcricao=True
    )
