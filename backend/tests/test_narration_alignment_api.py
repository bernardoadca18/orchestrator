from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.workflow import (
    NarrationAlignment,
    NarrationBlockAlignment,
    VisualQueryNarrationBinding,
)


def _entities():
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    query_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        audio_final_sha256="a" * 64,
        duracao_audio_segundos=8.0,
        audio_final_sample_rate=48_000,
        audio_final_channels=1,
    )
    alignment = NarrationAlignment(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        transcription_id=uuid.uuid4(),
        final_audio_sha256="a" * 64,
        script_fingerprint="b" * 64,
        transcription_fingerprint="c" * 64,
        strategy_version="narration_alignment_v1",
        fingerprint="d" * 64,
        status="completed",
        block_count=1,
        resolved_visual_queries=1,
        unresolved_visual_queries=0,
        ambiguous_visual_queries=0,
        not_applicable_visual_queries=0,
        metrics={"average_block_coverage": 0.9},
        started_at=dt.datetime.now(dt.UTC),
        completed_at=dt.datetime.now(dt.UTC),
        criado_em=dt.datetime.now(dt.UTC),
    )
    alignment.block_alignments = [
        NarrationBlockAlignment(
            id=uuid.uuid4(),
            narration_alignment_id=alignment.id,
            audio_bloco_id=block_id,
            block_order=0,
            status="aligned",
            narration_start_seconds=0.0,
            narration_end_seconds=8.0,
            canonical_token_count=8,
            synthesis_token_count=8,
            asr_word_count=8,
            canonical_synthesis_coverage=1.0,
            synthesis_asr_coverage=0.9,
            overall_coverage=0.9,
        )
    ]
    alignment.visual_query_bindings = [
        VisualQueryNarrationBinding(
            id=uuid.uuid4(),
            narration_alignment_id=alignment.id,
            audio_bloco_id=block_id,
            visual_query_id=query_id,
            block_order=0,
            query_order=0,
            status="resolved",
            narration_start_seconds=3.0,
            narration_end_seconds=5.0,
            excerpt_match_method="literal",
            excerpt_match_score=1.0,
            speech_alignment_coverage=1.0,
            binding_confidence=1.0,
            canonical_start_char=12,
            canonical_end_char=30,
        )
    ]
    return workflow, alignment


def test_timeline_get_and_manual_rebuild_endpoints_are_typed_and_idempotent() -> None:
    workflow, alignment = _entities()
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    service = MagicMock()
    service.current = AsyncMock(return_value=alignment)
    service.ensure = AsyncMock(return_value=(alignment, False))

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
            patch("app.api.routers.workflow.NarrationAlignmentService", return_value=service),
            TestClient(app) as client,
        ):
            timeline = client.get(f"/workflow/{workflow.id}/narration-timeline")
            rebuild = client.post(f"/workflow/{workflow.id}/narration-alignment")
    finally:
        app.dependency_overrides.clear()

    assert timeline.status_code == 200
    payload = timeline.json()
    assert payload["audio"]["sha256"] == "a" * 64
    assert payload["visual_query_bindings"][0]["narration_start_seconds"] == 3.0
    assert "palavras_timestamps" not in payload
    assert rebuild.status_code == 200
    assert rebuild.json()["created"] is False
    service.ensure.assert_awaited_once_with(workflow.id, force=False)


def test_timeline_rejects_unknown_workflow() -> None:
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=None)

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
            TestClient(app) as client,
        ):
            response = client.get(f"/workflow/{uuid.uuid4()}/narration-timeline")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


def test_timeline_returns_404_when_current_identity_has_no_alignment() -> None:
    workflow, _ = _entities()
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    service = MagicMock()
    service.current = AsyncMock(return_value=None)

    async def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
            patch("app.api.routers.workflow.NarrationAlignmentService", return_value=service),
            TestClient(app) as client,
        ):
            response = client.get(f"/workflow/{workflow.id}/narration-timeline")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
