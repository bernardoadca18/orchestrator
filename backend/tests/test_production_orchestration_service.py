from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients.video_composer_client import (
    ComposerRender,
    VideoComposerNotFoundError,
    VideoComposerQueueFullError,
    VideoComposerTransientError,
)
from app.core.config import Settings
from app.services.production_orchestration_service import (
    GAP_MESSAGE,
    ProductionOrchestrationService,
)


def _workflow(*, phase: str = "audio", status: str = "queued") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="audio_concluido",
        production_status=status,
        production_phase=phase,
        production_error_category=None,
        production_error_message=None,
        production_started_at=None,
        production_completed_at=None,
        audio_final_path="/managed/narration.wav",
        audio_final_sha256="a" * 64,
        audio_final_byte_size=100,
        duracao_audio_segundos=5.0,
        erro_mensagem=None,
    )


def _timeline(workflow_id: uuid.UUID, *, gaps: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        status="completed" if gaps == 0 else "completed_with_gaps",
        gap_count=gaps,
        fingerprint="b" * 64,
        error_message=None,
    )


def _remote(
    workflow_id: uuid.UUID, timeline: SimpleNamespace, status: str = "queued"
) -> ComposerRender:
    payload = {
        "id": str(uuid.uuid4()),
        "workflow_id": str(workflow_id),
        "production_timeline_id": str(timeline.id),
        "production_timeline_fingerprint": timeline.fingerprint,
        "render_profile": "youtube_1080p30_v1",
        "render_profile_identity": "c" * 64,
        "mode": "final",
        "status": status,
        "phase": status,
        "progress": 1.0 if status == "completed" else 0.0,
        "frames_rendered": 150 if status == "completed" else 0,
        "total_frames": 150,
        "render_fingerprint": "d" * 64,
        "manifest_fingerprint": "e" * 64,
        "output": None,
        "metrics": {},
        "error": None,
        "created_at": "2026-08-25T00:00:00Z",
        "started_at": "2026-08-25T00:00:01Z",
        "completed_at": None,
    }
    if status == "completed":
        payload["completed_at"] = "2026-08-25T00:01:00Z"
        payload["output"] = {
            "sha256": "f" * 64,
            "byteSize": 200,
            "durationSeconds": 5.0,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "frameCount": 150,
            "videoCodec": "h264",
            "audioCodec": "aac",
            "audioSampleRate": 48000,
            "mimeType": "video/mp4",
            "filename": f"{payload['id']}.mp4",
        }
    if status == "failed":
        payload["completed_at"] = "2026-08-25T00:01:00Z"
        payload["error"] = {"category": "render_failure", "message": "remote failed"}
    payload["status_url"] = f"/v1/renders/{payload['id']}"
    payload["video_url"] = f"/v1/renders/{payload['id']}/video" if status == "completed" else None
    return ComposerRender.model_validate(payload)


class FakeWorkflowRepo:
    def __init__(self, workflow: SimpleNamespace) -> None:
        self.workflow = workflow

    async def obter_por_id(self, workflow_id: uuid.UUID, **_: object):
        return self.workflow if workflow_id == self.workflow.id else None

    async def atualizar_producao(self, workflow_id: uuid.UUID, **values: object):
        assert workflow_id == self.workflow.id
        self.workflow.production_status = values["status"]
        self.workflow.production_phase = values["phase"]
        self.workflow.production_error_category = values.get("error_category")
        self.workflow.production_error_message = values.get("error_message")
        if values.get("start") and self.workflow.production_started_at is None:
            self.workflow.production_started_at = dt.datetime.now(dt.UTC)
        if values.get("complete"):
            self.workflow.production_completed_at = dt.datetime.now(dt.UTC)
        return self.workflow


class FakeRenderRepo:
    def __init__(self) -> None:
        self.render = None

    async def get_by_request_fingerprint(self, _: str):
        return self.render

    async def get_by_id(self, render_id: uuid.UUID):
        return self.render if self.render and self.render.id == render_id else None

    async def create_or_get(self, **values: object):
        if self.render is not None:
            return self.render, False
        self.render = SimpleNamespace(
            id=uuid.uuid4(),
            composer_render_id=None,
            render_profile_identity=None,
            render_fingerprint=None,
            manifest_fingerprint=None,
            progress=0.0,
            frames_rendered=0,
            total_frames=None,
            metrics=None,
            error_category=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=dt.datetime.now(dt.UTC),
            updated_at=dt.datetime.now(dt.UTC),
            **values,
        )
        return self.render, True

    async def bind_remote(self, render: SimpleNamespace, remote: ComposerRender):
        render.composer_render_id = remote.id

    async def sync_remote(self, render: SimpleNamespace, remote: ComposerRender):
        render.status = remote.status
        render.phase = remote.phase
        render.progress = remote.progress
        render.frames_rendered = remote.frames_rendered
        render.total_frames = remote.total_frames
        render.error_category = remote.error.category if remote.error else None
        render.error_message = remote.error.message if remote.error else None

    async def mark_failed(self, render: SimpleNamespace, category: str, message: str):
        render.status = "failed"
        render.error_category = category
        render.error_message = message

    async def reset_for_explicit_retry(self, render: SimpleNamespace):
        render.status = "pending"
        render.started_at = dt.datetime.now(dt.UTC)


class FakeStage:
    def __init__(self, item: SimpleNamespace) -> None:
        self.item = item
        self.ensure = AsyncMock(return_value=(item, True))

    async def current(self, _: uuid.UUID):
        return self.item


class FakeComposer:
    def __init__(
        self,
        remote: ComposerRender,
        *,
        missing_get: bool = False,
        failure: Exception | None = None,
    ) -> None:
        self.remote = remote
        self.missing_get = missing_get
        self.failure = failure
        self.create_calls: list[str | None] = []
        self.get_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object):
        return None

    async def get_render(self, _: str):
        self.get_calls += 1
        if self.failure:
            raise self.failure
        if self.missing_get:
            raise VideoComposerNotFoundError("missing")
        return self.remote

    async def create_render(self, *, idempotency_key: str | None, **_: object):
        self.create_calls.append(idempotency_key)
        if self.failure:
            raise self.failure
        return self.remote


class SequenceComposer(FakeComposer):
    def __init__(self, responses: list[ComposerRender]) -> None:
        super().__init__(responses[0])
        self.responses = responses

    def _next(self) -> ComposerRender:
        if not self.responses:
            raise AssertionError("unexpected Composer call")
        return self.responses.pop(0)

    async def get_render(self, _: str):
        self.get_calls += 1
        return self._next()

    async def create_render(self, *, idempotency_key: str | None, **_: object):
        self.create_calls.append(idempotency_key)
        return self._next()


class LostResponseComposer(FakeComposer):
    async def create_render(self, *, idempotency_key: str | None, **_: object):
        self.create_calls.append(idempotency_key)
        if len(self.create_calls) == 1:
            raise VideoComposerTransientError("response lost after durable submission")
        return self.remote


def _service(
    workflow: SimpleNamespace,
    timeline: SimpleNamespace,
    composer: FakeComposer,
    renders: FakeRenderRepo | None = None,
) -> ProductionOrchestrationService:
    alignment = SimpleNamespace(status="completed", error_message=None)
    visual = SimpleNamespace(status="completed", error_message=None)
    return ProductionOrchestrationService(
        AsyncMock(),
        Settings(video_composer_http_max_retries=0),
        workflow_repository=FakeWorkflowRepo(workflow),
        render_repository=renders or FakeRenderRepo(),
        alignment_service=FakeStage(alignment),
        visual_service=FakeStage(visual),
        timeline_service=FakeStage(timeline),
        audio_processor=AsyncMock(),
        composer_client_factory=lambda: composer,
    )


@pytest.mark.asyncio
async def test_happy_path_reuses_audio_and_submits_one_render() -> None:
    workflow = _workflow()
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert result.poll_render_id is not None
    assert composer.create_calls[0].startswith(f"neovids-render:{workflow.id}:")
    assert workflow.status == "audio_concluido"
    assert workflow.production_phase == "render"


@pytest.mark.asyncio
async def test_full_state_machine_reuses_upstream_and_polls_to_completion() -> None:
    workflow = _workflow()
    timeline = _timeline(workflow.id)
    composer = SequenceComposer(
        [
            _remote(workflow.id, timeline, "queued"),
            _remote(workflow.id, timeline, "rendering"),
            _remote(workflow.id, timeline, "completed"),
        ]
    )
    service = _service(workflow, timeline, composer)
    first = await service.process(workflow.id)
    assert first.poll_render_id is not None
    second = await service.poll(workflow.id, first.poll_render_id)
    assert second.poll_render_id == first.poll_render_id
    third = await service.poll(workflow.id, first.poll_render_id)
    assert third.terminal
    assert workflow.production_status == "completed"
    assert workflow.production_phase == "completed"
    service.audio_processor.assert_not_awaited()
    service.alignments.ensure.assert_awaited_once_with(workflow.id, force=False)
    service.visual.ensure.assert_awaited_once_with(workflow.id, force=False)
    service.timelines.ensure.assert_awaited_once_with(workflow.id, force=False)


@pytest.mark.asyncio
async def test_gap_timeline_blocks_without_contacting_composer() -> None:
    workflow = _workflow(phase="production_timeline")
    timeline = _timeline(workflow.id, gaps=1)
    composer = FakeComposer(_remote(workflow.id, timeline))
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert result.terminal
    assert composer.create_calls == []
    assert workflow.production_status == "blocked"
    assert workflow.production_error_category == "production_timeline_has_gaps"
    assert workflow.production_error_message == GAP_MESSAGE


@pytest.mark.asyncio
async def test_invalid_narration_quality_blocks_before_visual_production() -> None:
    workflow = _workflow(phase="narration_alignment", status="running")
    original_audio_path = workflow.audio_final_path
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    service.alignments.item.status = "blocked"
    service.alignments.item.error_message = "speech quality rejected"
    result = await service.process(workflow.id)
    assert result.terminal
    assert workflow.production_status == "blocked"
    assert workflow.production_error_category == "narration_alignment_blocked"
    assert workflow.audio_final_path == original_audio_path
    assert workflow.status == "audio_concluido"
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_alignment_retry_reuses_final_audio_and_resumes_at_alignment() -> None:
    workflow = _workflow(phase="narration_alignment", status="failed")
    original_path = workflow.audio_final_path
    original_sha = workflow.audio_final_sha256
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    service.visual.item.status = "blocked"
    service.visual.item.error_message = "next independent blocker"

    await service.explicit_retry(workflow)
    result = await service.process(workflow.id)

    assert result.terminal
    assert workflow.audio_final_path == original_path
    assert workflow.audio_final_sha256 == original_sha
    assert workflow.status == "audio_concluido"
    assert workflow.production_phase == "visual_plan"
    service.audio_processor.assert_not_awaited()
    service.alignments.ensure.assert_awaited_once_with(workflow.id, force=False)
    service.visual.ensure.assert_awaited_once_with(workflow.id, force=False)
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_missing_visual_queries_propagates_stable_workflow_error_category() -> None:
    workflow = _workflow(phase="visual_plan", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    service.visual.item.status = "blocked"
    service.visual.item.error_message = "Structured VisualQueries are required"
    service.visual.item.diagnostics = {"error_category": "visual_queries_missing"}

    result = await service.process(workflow.id)

    assert result.terminal
    assert workflow.production_status == "blocked"
    assert workflow.production_phase == "visual_plan"
    assert workflow.production_error_category == "visual_queries_missing"
    assert workflow.production_error_message == "Structured VisualQueries are required"
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_missing_media_scope_propagates_specific_visual_plan_reason() -> None:
    workflow = _workflow(phase="visual_plan", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    service.visual.item.status = "blocked"
    service.visual.item.error_message = "media scope is unavailable"
    service.visual.item.diagnostics = {"error_category": "visual_plan_media_scope_missing"}

    result = await service.process(workflow.id)

    assert result.terminal
    assert workflow.production_status == "blocked"
    assert workflow.production_error_category == "visual_plan_media_scope_missing"
    assert workflow.production_error_message == "media scope is unavailable"
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_completed_remote_promotes_workflow() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline, "completed"))
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert result.terminal
    assert workflow.production_status == "completed"
    assert workflow.production_phase == "completed"
    assert workflow.status == "audio_concluido"


@pytest.mark.asyncio
async def test_remote_identity_mismatch_is_permanent_failure() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    remote = _remote(uuid.uuid4(), timeline)
    composer = FakeComposer(remote)
    await _service(workflow, timeline, composer).process(workflow.id)
    assert workflow.production_status == "failed"
    assert workflow.production_error_category == "composer_contract_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["production_timeline_id", "fingerprint"])
async def test_remote_timeline_identity_mismatch_is_permanent_failure(field: str) -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    remote = _remote(workflow.id, timeline)
    if field == "production_timeline_id":
        remote.production_timeline_id = uuid.uuid4()
    else:
        remote.production_timeline_fingerprint = "0" * 64
    composer = FakeComposer(remote)
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert result.terminal
    assert workflow.production_status == "failed"
    assert workflow.production_error_category == "composer_contract_mismatch"


@pytest.mark.asyncio
async def test_lost_post_reuses_stable_key() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    remote = _remote(workflow.id, timeline)
    composer = FakeComposer(remote, missing_get=True)
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.composer_render_id = str(uuid.uuid4())
    result = await service.process(workflow.id)
    assert composer.get_calls == 1
    assert composer.create_calls == [render.idempotency_key]
    assert render.composer_render_id == remote.id
    assert result.poll_render_id == render.id


@pytest.mark.asyncio
async def test_lost_initial_post_response_reuses_key_on_scheduled_reconciliation() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    remote = _remote(workflow.id, timeline)
    composer = LostResponseComposer(remote)
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    first = await service.process(workflow.id)
    assert first.poll_render_id is not None
    second = await service.poll(workflow.id, first.poll_render_id)
    assert composer.create_calls[0] == composer.create_calls[1]
    assert composer.create_calls[0] is not None
    assert renders.render.composer_render_id == remote.id
    assert second.poll_render_id == renders.render.id


@pytest.mark.asyncio
async def test_remote_failure_does_not_demote_legacy_audio_status() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline, "failed"))
    await _service(workflow, timeline, composer).process(workflow.id)
    assert workflow.production_status == "failed"
    assert workflow.production_error_category == "composer:render_failure"
    assert workflow.status == "audio_concluido"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        VideoComposerQueueFullError("queue full"),
        VideoComposerTransientError("temporary outage"),
    ],
)
async def test_transient_submission_is_rescheduled_without_invalidating_upstream(
    failure: Exception,
) -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline), failure=failure)
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert not result.terminal
    assert result.poll_render_id is not None
    assert workflow.production_status == "running"
    assert workflow.production_error_category is None
    assert workflow.status == "audio_concluido"


@pytest.mark.asyncio
async def test_transient_poll_is_rescheduled_without_marking_render_failed() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(
        _remote(workflow.id, timeline),
        failure=VideoComposerTransientError("temporary outage"),
    )
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.composer_render_id = str(uuid.uuid4())
    result = await service.poll(workflow.id, render.id)
    assert not result.terminal
    assert result.poll_render_id == render.id
    assert render.status == "pending"
    assert workflow.production_status == "running"


@pytest.mark.asyncio
async def test_render_timeout_is_terminal_and_does_not_contact_composer() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=3601)
    result = await service.poll(workflow.id, render.id)
    assert result.terminal
    assert composer.get_calls == 0
    assert render.status == "failed"
    assert render.error_category == "composer_timeout"
    assert workflow.production_status == "failed"
    assert workflow.production_error_category == "composer_timeout"


@pytest.mark.asyncio
async def test_cancelled_workflow_never_contacts_composer() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert result.terminal
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_explicit_failed_render_retry_omits_original_key() -> None:
    workflow = _workflow(phase="render", status="failed")
    timeline = _timeline(workflow.id)
    remote = _remote(workflow.id, timeline, "failed")
    composer = FakeComposer(remote)
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.status = "failed"
    render.started_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    render.composer_render_id = remote.id
    await service.explicit_retry(workflow)
    assert not service._timed_out(render)
    await service.process(workflow.id)
    assert composer.get_calls == 1
    assert composer.create_calls == [None]


@pytest.mark.asyncio
async def test_explicit_retry_reconciles_remote_completion_before_post() -> None:
    workflow = _workflow(phase="render", status="failed")
    timeline = _timeline(workflow.id)
    remote = _remote(workflow.id, timeline, "completed")
    composer = FakeComposer(remote)
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.status = "failed"
    render.composer_render_id = remote.id
    await service.explicit_retry(workflow)
    await service.process(workflow.id)
    assert composer.get_calls == 1
    assert composer.create_calls == []
    assert workflow.production_status == "completed"


@pytest.mark.asyncio
async def test_cancelled_poll_cannot_promote_completed_remote() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline, "completed"))
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    result = await service.poll(workflow.id, render.id)
    assert result.terminal
    assert composer.get_calls == 0
    assert workflow.production_status == "cancelled"


@pytest.mark.asyncio
async def test_legacy_resume_detects_earliest_missing_derived_stage() -> None:
    workflow = _workflow(phase="audio", status=None)
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    service.alignments.item.status = "blocked"
    await service.resume(workflow)
    assert workflow.production_status == "queued"
    assert workflow.production_phase == "narration_alignment"


@pytest.mark.asyncio
async def test_legacy_resume_with_all_current_artifacts_starts_at_render() -> None:
    workflow = _workflow(phase="audio", status=None)
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    await service.resume(workflow)
    assert workflow.production_status == "queued"
    assert workflow.production_phase == "render"
