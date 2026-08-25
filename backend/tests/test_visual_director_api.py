from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.workflow import VisualDecision, VisualPlan


def _plan() -> VisualPlan:
    workflow_id = uuid.uuid4()
    plan = VisualPlan(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        narration_alignment_id=uuid.uuid4(),
        script_fingerprint="a" * 64,
        media_scope={
            "media_project_id": str(uuid.uuid4()),
            "media_session_ids": [str(uuid.uuid4())],
            "origin": "script_media_context",
        },
        media_scope_fingerprint="b" * 64,
        media_index_provenance={"temporal_index_ids": [str(uuid.uuid4())]},
        strategy_version="visual_director_v1",
        request_fingerprint="c" * 64,
        fingerprint="d" * 64,
        status="completed",
        total_queries=1,
        resolved_queries=1,
        unresolved_queries=0,
        failed_queries=0,
        required_gaps=0,
        optional_gaps=0,
        direct_provenance_count=0,
        media_search_count=1,
        reused_source_count=0,
        metrics={"processing_seconds": 0.01},
        criado_em=dt.datetime.now(dt.UTC),
    )
    project_id = uuid.UUID(plan.media_scope["media_project_id"])
    session_id = uuid.UUID(plan.media_scope["media_session_ids"][0])
    plan.decisions = [
        VisualDecision(
            id=uuid.uuid4(),
            visual_plan_id=plan.id,
            visual_query_id=uuid.uuid4(),
            audio_bloco_id=uuid.uuid4(),
            block_order=0,
            query_order=0,
            query="Geralt learns Aard",
            intent="evidence",
            importance="required",
            narration_binding_status="resolved",
            narration_start_seconds=10,
            narration_end_seconds=18,
            target_narration_duration_seconds=8,
            source_resolution_status="resolved",
            selection_strategy="media_search",
            selected_source={
                "format_version": "visual_source_v1",
                "source_type": "gameplay",
                "project_id": str(project_id),
                "session_id": str(session_id),
                "media_id": str(uuid.uuid4()),
                "source_start_seconds": 1200,
                "source_end_seconds": 1240,
                "source_duration_seconds": 40,
                "semantic_score": 0.82,
                "selection_score": 0.84,
                "rank": 1,
                "session_label": "001",
                "media_filename": "001.mp4",
                "matched_note_ids": [],
                "provenance": {},
            },
            alternatives=[],
            selection_score=0.84,
            score_components={"semantic": 0.82},
        )
    ]
    return plan


def test_visual_plan_get_and_post_are_typed_and_do_not_hide_work() -> None:
    plan = _plan()
    service = MagicMock()
    service.current = AsyncMock(return_value=plan)
    service.ensure = AsyncMock(return_value=(plan, False))

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.VisualDirectorService", return_value=service),
            TestClient(app) as client,
        ):
            fetched = client.get(f"/workflow/{plan.workflow_execucao_id}/visual-plan")
            rebuilt = client.post(f"/workflow/{plan.workflow_execucao_id}/visual-plan")
    finally:
        app.dependency_overrides.clear()

    assert fetched.status_code == 200
    payload = fetched.json()
    decision = payload["decisions"][0]
    assert decision["narration_start_seconds"] == 10
    assert decision["selected_source"]["source_start_seconds"] == 1200
    assert "timeline_start" not in str(payload)
    assert rebuilt.status_code == 200
    assert rebuilt.json()["created"] is False
    service.current.assert_awaited_once_with(plan.workflow_execucao_id)
    service.ensure.assert_awaited_once_with(plan.workflow_execucao_id, force=False)


def test_visual_plan_get_returns_404_without_compatible_plan() -> None:
    service = MagicMock()
    service.current = AsyncMock(return_value=None)

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.VisualDirectorService", return_value=service),
            TestClient(app) as client,
        ):
            response = client.get(f"/workflow/{uuid.uuid4()}/visual-plan")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
