from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request

from app.api.routers.workflow import (
    baixar_video_final,
    obter_render,
    obter_transcricao,
    repetir_workflow,
    retomar_producao,
)
from app.schemas.workflow import WorkflowOut


def _workflow(status: str | None = "blocked") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="audio_concluido",
        production_status=status,
        production_phase="production_timeline",
    )


def _render(workflow_id: uuid.UUID, status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        production_timeline_id=uuid.uuid4(),
        production_timeline_fingerprint="a" * 64,
        render_profile="youtube_1080p30_v1",
        mode="final",
        status=status,
        phase=status,
        progress=1.0,
        frames_rendered=150,
        total_frames=150,
        render_profile_identity="b" * 64,
        render_fingerprint="c" * 64,
        manifest_fingerprint="d" * 64,
        output_sha256="e" * 64,
        output_byte_size=100,
        output_duration_seconds=5.0,
        output_width=1920,
        output_height=1080,
        output_fps=30,
        output_frame_count=150,
        output_video_codec="h264",
        output_audio_codec="aac",
        output_audio_sample_rate=48000,
        metrics={},
        error_category=None,
        error_message=None,
        started_at=dt.datetime.now(dt.UTC),
        completed_at=dt.datetime.now(dt.UTC),
        created_at=dt.datetime.now(dt.UTC),
        updated_at=dt.datetime.now(dt.UTC),
        composer_render_id=str(uuid.uuid4()),
    )


@pytest.mark.asyncio
async def test_render_endpoint_returns_current_semantic_render() -> None:
    workflow = _workflow("completed")
    render = _render(workflow.id)
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    service = MagicMock()
    service.current_render = AsyncMock(return_value=render)
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ProductionOrchestrationService", return_value=service),
    ):
        result = await obter_render(workflow.id, MagicMock())
    assert result.status == "completed"
    assert result.video_url == f"/workflow/{workflow.id}/video"
    assert result.output is not None and result.output.sha256 == "e" * 64


@pytest.mark.asyncio
async def test_resume_only_accepts_blocked_or_legacy() -> None:
    workflow = _workflow("running")
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        pytest.raises(HTTPException) as error,
    ):
        await retomar_producao(workflow.id, MagicMock())
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_video_endpoint_forwards_range_and_streams() -> None:
    workflow = _workflow("completed")
    render = _render(workflow.id)
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    service = MagicMock()
    service.current_render = AsyncMock(return_value=render)
    remote = httpx.Response(
        206,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": "bytes 0-3/10",
            "Content-Length": "4",
        },
        content=b"1234",
    )
    composer = MagicMock()
    composer.__aenter__ = AsyncMock(return_value=composer)
    composer.aclose = AsyncMock()
    composer.open_video = AsyncMock(return_value=remote)
    request = Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [(b"range", b"bytes=0-3")]}
    )
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ProductionOrchestrationService", return_value=service),
        patch("app.api.routers.workflow.VideoComposerClient", return_value=composer),
    ):
        response = await baixar_video_final(workflow.id, request, MagicMock())
        body = b"".join([chunk async for chunk in response.body_iterator])
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-3/10"
    assert body == b"1234"
    composer.open_video.assert_awaited_once_with(
        str(render.composer_render_id), range_header="bytes=0-3"
    )


def test_audio_availability_is_artifact_based_after_downstream_failure() -> None:
    workflow = WorkflowOut.model_construct(
        status="erro",
        audio_final_path="/managed/narration.wav",
        audio_final_sha256="a" * 64,
        audio_final_byte_size=100,
    )
    assert workflow.audio_available is True


@pytest.mark.asyncio
async def test_stale_transcription_is_not_exposed_as_current() -> None:
    workflow = _workflow("running")
    workflow.audio_final_sha256 = "b" * 64
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    repository.obter_transcricao = AsyncMock(
        return_value=SimpleNamespace(audio_sha256="a" * 64)
    )
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        pytest.raises(HTTPException) as error,
    ):
        await obter_transcricao(workflow.id, MagicMock())
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_reuses_valid_final_audio_without_legacy_demotion() -> None:
    workflow = _workflow("failed")
    workflow.production_phase = "audio"
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    repository.incrementar_tentativa = AsyncMock()
    service = MagicMock()
    service.explicit_retry = AsyncMock()
    service_class = MagicMock(return_value=service)
    service_class._audio_is_reusable.return_value = True

    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ProductionOrchestrationService", service_class),
        patch("app.api.routers.workflow.processar_producao_workflow.delay") as enqueue,
    ):
        result = await repetir_workflow(workflow.id, MagicMock())

    assert result is workflow
    repository.incrementar_tentativa.assert_not_awaited()
    service.explicit_retry.assert_awaited_once_with(workflow)
    enqueue.assert_called_once_with(str(workflow.id))


@pytest.mark.asyncio
async def test_retry_preserves_narration_alignment_phase() -> None:
    workflow = _workflow("failed")
    workflow.production_phase = "narration_alignment"
    workflow.audio_final_sha256 = "a" * 64
    original_sha = workflow.audio_final_sha256
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    repository.incrementar_tentativa = AsyncMock()
    service = MagicMock()
    service.explicit_retry = AsyncMock()

    with (
        patch(
            "app.api.routers.workflow.ProductionOrchestrationService",
            return_value=service,
        ),
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.processar_producao_workflow.delay") as enqueue,
    ):
        result = await repetir_workflow(workflow.id, MagicMock())

    assert result is workflow
    assert workflow.production_phase == "narration_alignment"
    assert workflow.audio_final_sha256 == original_sha
    repository.incrementar_tentativa.assert_not_awaited()
    service.explicit_retry.assert_awaited_once_with(workflow)
    enqueue.assert_called_once_with(str(workflow.id))
