from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients.media_indexer_search import MediaAssetMetadata
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
from app.services.production_qa_checks import check_hybrid_source_contracts
from app.services.production_qa_policy import resolve_qa_policy
from tests.test_production_qa_checks import _inputs


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
        perfil_producao="game_analysis",
        erro_mensagem=None,
    )


def _timeline(workflow_id: uuid.UUID, *, gaps: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        status="completed" if gaps == 0 else "completed_with_gaps",
        gap_count=gaps,
        visual_gap_seconds=2.0 if gaps else 0.0,
        visual_coverage_ratio=0.6 if gaps else 1.0,
        gaps=[
            SimpleNamespace(
                timeline_start_seconds=1.0,
                timeline_end_seconds=3.0,
                severity="preferred",
                reason="source_unresolved",
                related_visual_query_ids=[uuid.uuid4()],
            )
        ]
        if gaps
        else [],
        fingerprint="b" * 64,
        duration_seconds=5.0,
        error_message=None,
        placements=[],
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

    async def reset_for_explicit_retry(self, render: SimpleNamespace, *, commit: bool = True):
        render.status = "pending"
        render.phase = "pending"
        render.progress = 0.0
        render.frames_rendered = 0
        render.error_category = None
        render.error_message = None
        render.started_at = dt.datetime.now(dt.UTC)
        render.completed_at = None


class FakeStage:
    def __init__(self, item: SimpleNamespace) -> None:
        self.item = item
        self.ensure = AsyncMock(return_value=(item, True))

    async def current(self, _: uuid.UUID):
        return self.item


@pytest.mark.asyncio
async def test_pre_render_entity_mismatch_blocks_before_composer() -> None:
    workflow = _workflow(phase="render")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    service.visual.item.decisions = [
        SimpleNamespace(
            query="GTA IV street scene",
            selected_source={"session_label": "gta_v_gameplay_ep001", "semantic_score": 0.8},
            source_resolution_status="resolved",
        )
    ]

    result = await service.process(workflow.id)

    assert result.terminal is True
    assert workflow.production_error_category == "visual_diversity_gate_failed"
    assert "game identity" in workflow.production_error_message
    assert composer.create_calls == []


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


def _static_placement(*, duration: float = 3.0) -> SimpleNamespace:
    return SimpleNamespace(
        order=0,
        source_type="static_image",
        source_project_id=uuid.UUID(int=10),
        source_session_id=None,
        source_media_id=uuid.UUID(int=11),
        source_image_semantic_index_id=uuid.UUID(int=12),
        timeline_start_seconds=0.0,
        timeline_end_seconds=duration,
        candidate_start_seconds=None,
        candidate_end_seconds=None,
        source_start_seconds=None,
        source_end_seconds=None,
        playback_rate=None,
        presentation={
            "policy_version": "static_image_presentation_v1",
            "fit": "contain",
            "background": "blurred_fill",
            "motion": "slow_zoom_in",
            "zoom_amount": 0.04,
        },
        source_provenance={
            "expected_sha256": "a" * 64,
            "expected_byte_size": 123,
            "content_type": "image/png",
        },
        visual_decision_id=None,
    )


@pytest.mark.asyncio
async def test_stage17_identity_changes_when_presentation_changes() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    workflow.roteiro_snapshot = {
        "titulo": "Video",
        "blocos": [{"ordem": 0, "conteudo": "texto", "display_title": "Section"}],
    }
    workflow.blocos = [
        SimpleNamespace(
            ordem=0,
            narration_start_seconds=0.0,
            narration_end_seconds=5.0,
        )
    ]
    workflow.transcricao = SimpleNamespace(
        audio_sha256=workflow.audio_final_sha256,
        palavras_timestamps=[{"palavra": "one", "inicio": 0.0, "fim": 0.5}],
    )
    service = _service(workflow, timeline, FakeComposer(_remote(workflow.id, timeline)))
    first_presentation = await service._presentation_fingerprint(workflow.id, timeline)
    first = service.render_request_fingerprint(
        workflow.id, timeline, presentation_fingerprint=first_presentation
    )
    workflow.transcricao.palavras_timestamps[0]["palavra"] = "changed"
    second_presentation = await service._presentation_fingerprint(workflow.id, timeline)
    second = service.render_request_fingerprint(
        workflow.id, timeline, presentation_fingerprint=second_presentation
    )
    assert first_presentation != second_presentation
    assert first != second


@pytest.mark.asyncio
async def test_completed_remote_with_stale_presentation_cannot_promote_workflow() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    workflow.roteiro_snapshot = {
        "titulo": "Video",
        "blocos": [{"ordem": 0, "conteudo": "texto"}],
    }
    workflow.blocos = [
        SimpleNamespace(
            ordem=0,
            narration_start_seconds=0.0,
            narration_end_seconds=5.0,
        )
    ]
    workflow.transcricao = SimpleNamespace(
        audio_sha256=workflow.audio_final_sha256,
        palavras_timestamps=[{"palavra": "current", "inicio": 0.0, "fim": 0.5}],
    )
    remote = _remote(workflow.id, timeline, "completed").model_copy(
        update={"presentation_fingerprint": "0" * 64}
    )
    result = await _service(workflow, timeline, FakeComposer(remote)).process(workflow.id)
    assert result.terminal
    assert workflow.production_status == "blocked"
    assert workflow.production_error_category == "composer_presentation_mismatch"


@pytest.mark.asyncio
async def test_hybrid_pre_render_gate_validates_static_contract_and_asset_identity() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    timeline.schema_version = "production_timeline_schema_v2"
    timeline.placements = [_static_placement()]
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)

    class Provider:
        async def get_asset(self, media_id):
            return MediaAssetMetadata(
                id=media_id,
                project_id=uuid.UUID(int=10),
                media_kind="image",
                content_type="image/png",
                byte_size=123,
                sha256="a" * 64,
                width=1080,
                height=1350,
            )

    service.media_provider = Provider()
    assert service._hybrid_timeline_errors(timeline) == []
    assert await service._validate_static_image_assets(timeline) == []

    timeline.placements[0] = _static_placement(duration=9)
    assert "placement_0_image_hold_invalid" in service._hybrid_timeline_errors(timeline)


@pytest.mark.parametrize(
    ("duration", "valid"),
    [(2.0, True), (8.0, True), (1.999, False), (8.001, False)],
)
def test_stage17_and_stage18_share_static_image_hard_limits(duration: float, valid: bool) -> None:
    workflow = _workflow()
    timeline = _timeline(workflow.id)
    timeline.schema_version = "production_timeline_schema_v2"
    timeline.placements = [_static_placement(duration=duration)]
    service = _service(workflow, timeline, FakeComposer(_remote(workflow.id, timeline)))
    stage17_valid = not service._hybrid_timeline_errors(timeline)

    inputs = replace(
        _inputs(),
        policy=resolve_qa_policy(
            "faceless_general",
            "production_timeline_schema_v2",
            static_image_minimum_hold_seconds=(service.config.static_image_minimum_hold_seconds),
            static_image_maximum_hold_seconds=(service.config.static_image_maximum_hold_seconds),
            static_image_maximum_consecutive_seconds=(
                service.config.static_image_maximum_consecutive_seconds
            ),
        ),
    )
    inputs.timeline.schema_version = "production_timeline_schema_v2"
    inputs.timeline.placements = timeline.placements
    stage18_valid = check_hybrid_source_contracts(inputs).result == "passed"
    assert stage17_valid is valid
    assert stage18_valid is valid


@pytest.mark.parametrize(("total_duration", "valid"), [(15.0, True), (15.001, False)])
def test_stage17_and_stage18_share_consecutive_still_limit(
    total_duration: float, valid: bool
) -> None:
    workflow = _workflow()
    timeline = _timeline(workflow.id)
    timeline.schema_version = "production_timeline_schema_v2"
    first = _static_placement(duration=7.5)
    second = _static_placement(duration=total_duration - 7.5)
    second.order = 1
    second.timeline_start_seconds = 7.5
    second.timeline_end_seconds = total_duration
    second.source_media_id = uuid.UUID(int=13)
    timeline.placements = [first, second]
    service = _service(workflow, timeline, FakeComposer(_remote(workflow.id, timeline)))
    stage17_valid = not service._hybrid_timeline_errors(timeline)

    inputs = replace(
        _inputs(),
        policy=resolve_qa_policy("faceless_general", "production_timeline_schema_v2"),
    )
    inputs.timeline.schema_version = "production_timeline_schema_v2"
    inputs.timeline.placements = timeline.placements
    stage18_valid = check_hybrid_source_contracts(inputs).result == "passed"
    assert stage17_valid is valid
    assert stage18_valid is valid


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
    service.timelines.ensure.assert_not_awaited()


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
async def test_pre_render_visual_diversity_gate_blocks_degenerate_timeline() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    timeline.placements = [
        SimpleNamespace(
            source_session_id=uuid.UUID(int=1),
            source_media_id=uuid.UUID(int=2),
            candidate_start_seconds=285.688,
            candidate_end_seconds=346.788,
            timeline_start_seconds=float(index * 30),
            timeline_end_seconds=float(index * 30 + 30),
            strategy="media_search",
        )
        for index in range(22)
    ]
    composer = FakeComposer(_remote(workflow.id, timeline))
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert result.terminal
    assert workflow.production_status == "blocked"
    assert workflow.production_error_category == "visual_diversity_gate_failed"
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_pre_render_visual_diversity_gate_passes_diverse_timeline() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    timeline.placements = [
        SimpleNamespace(
            source_session_id=uuid.UUID(int=1),
            source_media_id=uuid.UUID(int=2),
            candidate_start_seconds=float(index * 20),
            candidate_end_seconds=float(index * 20 + 20),
            timeline_start_seconds=float(index * 30),
            timeline_end_seconds=float(index * 30 + 30),
            strategy="media_search",
        )
        for index in range(22)
    ]
    composer = FakeComposer(_remote(workflow.id, timeline))
    result = await _service(workflow, timeline, composer).process(workflow.id)
    assert not result.terminal
    assert composer.create_calls


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
async def test_asset_materialization_failure_is_preserved_for_operator_retry() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    payload = _remote(workflow.id, timeline, "failed").model_dump(mode="json")
    payload["error"] = {
        "category": "asset_materialization_failed",
        "message": "Falha ao materializar mídia necessária para o render.",
    }
    remote = ComposerRender.model_validate(payload)
    composer = FakeComposer(remote)
    renders = FakeRenderRepo()

    await _service(workflow, timeline, composer, renders).process(workflow.id)

    assert renders.render.error_category == "asset_materialization_failed"
    assert renders.render.error_message == ("Falha ao materializar mídia necessária para o render.")
    assert workflow.production_error_category == "composer:asset_materialization_failed"
    assert workflow.production_error_message == (
        "Falha ao materializar mídia necessária para o render."
    )


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
async def test_render_timeout_reconciles_active_remote_before_terminal_policy() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.composer_render_id = composer.remote.id
    render.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=3601)
    result = await service.poll(workflow.id, render.id)
    assert result.terminal
    assert composer.get_calls == 1
    assert render.status == "failed"
    assert render.error_category == "composer_timeout"
    assert workflow.production_status == "failed"
    assert workflow.production_error_category == "composer_timeout"


@pytest.mark.asyncio
async def test_render_timeout_reconciles_remote_completion() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline, "completed"))
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.composer_render_id = composer.remote.id
    render.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=3601)

    result = await service.poll(workflow.id, render.id)

    assert result.terminal
    assert composer.get_calls == 1
    assert workflow.production_status == "completed"


@pytest.mark.asyncio
async def test_render_timeout_mirrors_remote_failure() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline, "failed"))
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.composer_render_id = composer.remote.id
    render.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=3601)

    result = await service.poll(workflow.id, render.id)

    assert result.terminal
    assert composer.get_calls == 1
    assert workflow.production_error_category == "composer:render_failure"


@pytest.mark.asyncio
async def test_render_timeout_transient_outage_uses_bounded_grace() -> None:
    workflow = _workflow(phase="render", status="running")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(
        _remote(workflow.id, timeline),
        failure=VideoComposerTransientError("temporary outage"),
    )
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.composer_render_id = composer.remote.id
    render.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=3601)

    grace = await service.poll(workflow.id, render.id)
    assert not grace.terminal
    assert workflow.production_status == "running"

    render.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=3721)
    exhausted = await service.poll(workflow.id, render.id)
    assert exhausted.terminal
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
async def test_explicit_cancelled_resume_reuses_current_upstream_at_render() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    workflow.status = "cancelada"
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline, "queued"))
    service = _service(workflow, timeline, composer)

    await service.resume(workflow)
    result = await service.process(workflow.id)

    assert workflow.status == "audio_concluido"
    assert workflow.production_status == "running"
    assert workflow.production_phase == "render"
    assert result.poll_render_id is not None
    service.audio_processor.assert_not_awaited()
    service.alignments.ensure.assert_not_awaited()
    service.visual.ensure.assert_not_awaited()
    service.timelines.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_presentation_retry_reuses_qwen_alignment_visual_plan_and_timeline() -> None:
    workflow = _workflow(phase="render", status="failed")
    workflow.roteiro_snapshot = {"titulo": "Video", "blocos": []}
    workflow.blocos = []
    workflow.transcricao = SimpleNamespace(
        audio_sha256=workflow.audio_final_sha256,
        palavras_timestamps=[
            {"palavra": "explicar,", "inicio": 264.18, "fim": 264.18},
            {"palavra": "porque", "inicio": 264.46, "fim": 264.92},
            {"palavra": "se", "inicio": 711.64, "fim": 711.64},
            {"palavra": "a", "inicio": 711.64, "fim": 711.76},
        ],
    )
    workflow.duracao_audio_segundos = 876.32
    timeline = _timeline(workflow.id)
    timeline.duration_seconds = 876.32
    composer = FakeComposer(_remote(workflow.id, timeline, "queued"))
    service = _service(workflow, timeline, composer)
    voice_generation = AsyncMock()
    service.audio_processor.side_effect = lambda *_: voice_generation()

    await service.explicit_retry(workflow)
    result = await service.process(workflow.id)

    assert workflow.production_status == "running"
    assert workflow.production_phase == "render"
    assert result.poll_render_id is not None
    assert composer.create_calls and len(composer.create_calls) == 1
    service.audio_processor.assert_not_awaited()
    voice_generation.assert_not_awaited()
    service.alignments.ensure.assert_not_awaited()
    service.visual.ensure.assert_not_awaited()
    service.timelines.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_presentation_failure_persists_actionable_state_and_structured_log(caplog) -> None:
    workflow = _workflow(phase="render", status="running")
    workflow.roteiro_snapshot = {"titulo": "Video", "blocos": []}
    workflow.blocos = []
    workflow.transcricao = SimpleNamespace(
        audio_sha256=workflow.audio_final_sha256,
        palavras_timestamps=[
            {"palavra": "one", "inicio": 0.0, "fim": 0.5},
            {"palavra": "broken", "inicio": 0.2, "fim": 0.8},
        ],
    )
    timeline = _timeline(workflow.id)
    timeline.narration_alignment_id = uuid.uuid4()
    composer = FakeComposer(_remote(workflow.id, timeline, "queued"))
    service = _service(workflow, timeline, composer)

    result = await service.process(workflow.id)

    assert result.terminal
    assert workflow.production_status == "failed"
    assert workflow.production_phase == "render"
    assert workflow.production_error_category == "presentation_input_invalid"
    assert "reason=overlaps_previous_word" in workflow.production_error_message
    record = next(
        item for item in caplog.records if item.message == "presentation spec input rejected"
    )
    assert record.workflow_id == str(workflow.id)
    assert record.alignment_id == str(timeline.narration_alignment_id)
    assert record.timeline_id == str(timeline.id)
    assert record.reason == "overlaps_previous_word"
    assert record.word_index == 1
    assert record.token == "broken"
    assert record.start == 0.2
    assert record.end == 0.8
    assert record.previous_end == 0.5
    assert record.word_duration_seconds == pytest.approx(0.6)
    assert record.overlap_seconds == pytest.approx(0.3)
    assert record.timeline_duration == 5.0


@pytest.mark.asyncio
async def test_explicit_cancelled_resume_reconciles_compatible_remote_completion() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    timeline = _timeline(workflow.id)
    remote = _remote(workflow.id, timeline, "completed")
    composer = FakeComposer(remote)
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.status = "rendering"
    render.composer_render_id = remote.id

    await service.resume(workflow)
    result = await service.process(workflow.id)

    assert result.terminal
    assert result.qa_workflow_id == workflow.id
    assert composer.get_calls == 1
    assert composer.create_calls == []
    assert workflow.production_status == "completed"


@pytest.mark.asyncio
async def test_explicit_cancelled_resume_retries_confirmed_remote_failure() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    timeline = _timeline(workflow.id)
    failed = _remote(workflow.id, timeline, "failed")
    replacement = _remote(workflow.id, timeline, "queued")
    composer = SequenceComposer([failed, replacement])
    renders = FakeRenderRepo()
    service = _service(workflow, timeline, composer, renders)
    render = await service._ensure_local_render(workflow, timeline)
    render.status = "rendering"
    render.composer_render_id = failed.id

    await service.resume(workflow)
    result = await service.process(workflow.id)

    assert result.poll_render_id == render.id
    assert composer.get_calls == 1
    assert composer.create_calls == [None]
    assert render.composer_render_id == replacement.id
    assert workflow.production_status == "running"


@pytest.mark.asyncio
async def test_explicit_cancelled_resume_still_blocks_stale_gap_timeline() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    timeline = _timeline(workflow.id, gaps=1)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)

    await service.resume(workflow)
    result = await service.process(workflow.id)

    assert result.terminal
    assert workflow.production_status == "blocked"
    assert workflow.production_error_category == "production_timeline_has_gaps"
    assert composer.get_calls == 0
    assert composer.create_calls == []


@pytest.mark.asyncio
async def test_blocked_resume_preserves_the_blocked_phase() -> None:
    workflow = _workflow(phase="visual_plan", status="blocked")
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)

    await service.resume(workflow)

    assert workflow.production_status == "queued"
    assert workflow.production_phase == "visual_plan"
    service.alignments.ensure.assert_not_awaited()
    service.visual.ensure.assert_not_awaited()
    service.timelines.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_without_reusable_audio_returns_to_audio_queue() -> None:
    workflow = _workflow(phase="render", status="cancelled")
    workflow.status = "cancelada"
    workflow.audio_final_sha256 = None
    timeline = _timeline(workflow.id)
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)

    await service.resume(workflow)

    assert workflow.status == "agendada"
    assert workflow.production_status == "queued"
    assert workflow.production_phase == "audio"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "completed_with_gaps"])
@pytest.mark.parametrize("phase", ["production_timeline", "render"])
async def test_query_warning_and_legacy_status_pass_both_temporal_gates(status, phase):
    workflow = _workflow(phase=phase, status="blocked")
    timeline = _timeline(workflow.id)
    timeline.status = status
    timeline.unplaced_query_count = 1
    composer = FakeComposer(_remote(workflow.id, timeline))
    service = _service(workflow, timeline, composer)
    await service.resume(workflow)
    result = await service.process(workflow.id)
    assert result.poll_render_id is not None
    assert workflow.production_phase == "render"
    assert workflow.production_error_category is None
    service.timelines.ensure.assert_not_awaited()
    service.visual.ensure.assert_not_awaited()
    service.alignments.ensure.assert_not_awaited()
    service.audio_processor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes,expected_status,visual_calls",
    [
        ([0], "queued", 0),
        ([1, 0], "queued", 1),
        ([1, 1], "blocked", 1),
    ],
)
async def test_explicit_repair_is_bounded_and_preserves_upstream(
    outcomes, expected_status, visual_calls
):
    from unittest.mock import call

    workflow = _workflow(phase="production_timeline", status="blocked")
    workflow.production_error_category = "production_timeline_has_gaps"
    original = _timeline(workflow.id, gaps=1)
    composer = FakeComposer(_remote(workflow.id, original))
    service = _service(workflow, original, composer)
    service.timelines.ensure.side_effect = [
        (_timeline(workflow.id, gaps=count), True) for count in outcomes
    ]
    await service.repair_timeline(workflow)
    assert workflow.production_status == expected_status
    assert workflow.production_phase == "production_timeline"
    assert service.timelines.ensure.await_args_list == [call(workflow.id, force=True)] * len(
        outcomes
    )
    assert service.visual.ensure.await_args_list == [call(workflow.id, force=True)] * visual_calls
    service.alignments.ensure.assert_not_awaited()
    service.audio_processor.assert_not_awaited()
    assert not composer.create_calls
    if expected_status == "blocked":
        assert workflow.production_error_category == "production_timeline_has_gaps"
        for key in (
            "gap_count",
            "total_gap_seconds",
            "start_seconds",
            "end_seconds",
            "severity",
            "reason",
            "related_visual_query_ids",
        ):
            assert key in workflow.production_error_message


@pytest.mark.asyncio
async def test_query_warning_only_cannot_trigger_repair():
    workflow = _workflow(phase="production_timeline", status="blocked")
    workflow.production_error_category = "production_timeline_has_gaps"
    timeline = _timeline(workflow.id)
    timeline.status = "completed_with_gaps"
    timeline.unplaced_query_count = 1
    service = _service(workflow, timeline, FakeComposer(_remote(workflow.id, timeline)))
    with pytest.raises(ValueError, match="gaps temporais reais"):
        await service.repair_timeline(workflow)
    service.timelines.ensure.assert_not_awaited()
    service.visual.ensure.assert_not_awaited()
