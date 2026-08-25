from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.video_composer_client import VideoComposerTransientError
from app.core.config import Settings
from app.services.production_qa_service import (
    ProductionQAPreconditionError,
    ProductionQAService,
)
from tests.test_production_qa_checks import _inputs


class FakeWorkflowRepository:
    def __init__(self, workflow: SimpleNamespace) -> None:
        self.workflow = workflow

    async def obter_por_id(self, workflow_id: uuid.UUID, **_: object):
        return self.workflow if workflow_id == self.workflow.id else None


class FakeStage:
    def __init__(self, item: SimpleNamespace | None) -> None:
        self.item = item
        self.ensure = AsyncMock(side_effect=AssertionError("QA must not rebuild upstream"))

    async def current(self, _: uuid.UUID):
        return self.item


class FakeOrchestration(FakeStage):
    async def current_render(self, _: uuid.UUID):
        return self.item


class InMemoryQARepository:
    def __init__(self) -> None:
        self.by_fingerprint: dict[str, SimpleNamespace] = {}
        self.run_count = 0

    async def get_by_fingerprint(self, fingerprint: str):
        return self.by_fingerprint.get(fingerprint)

    async def create_or_get(self, **values: object):
        fingerprint = str(values["request_fingerprint"])
        if fingerprint in self.by_fingerprint:
            return self.by_fingerprint[fingerprint], False
        now = dt.datetime.now(dt.UTC)
        report = SimpleNamespace(
            id=uuid.uuid4(),
            check_count=0,
            passed_count=0,
            warning_count=0,
            failed_count=0,
            blocked_count=0,
            metrics=None,
            error_category=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=now,
            **values,
        )
        self.by_fingerprint[fingerprint] = report
        return report, True

    async def mark_running(self, report: SimpleNamespace) -> None:
        self.run_count += 1
        report.status = "running"
        report.publication_ready = False

    async def complete(self, report: SimpleNamespace, **values: object) -> None:
        aggregate = values["aggregate"]
        checks = values["checks"]
        report.status = aggregate.status
        report.publication_ready = aggregate.publication_ready
        report.check_count = aggregate.check_count
        report.passed_count = aggregate.passed_count
        report.warning_count = aggregate.warning_count
        report.failed_count = aggregate.failed_count
        report.blocked_count = aggregate.blocked_count
        report.checks = [item.to_dict() for item in checks]
        report.metrics = values["metrics"]
        report.error_category = values.get("error_category")
        report.error_message = values.get("error_message")
        report.completed_at = dt.datetime.now(dt.UTC)

    async def fail(self, report: SimpleNamespace, category: str, message: str) -> None:
        report.status = "failed"
        report.publication_ready = False
        report.error_category = category
        report.error_message = message

    async def reload(self, report: SimpleNamespace):
        return report


class FakeComposer:
    def __init__(self, profile, payload: bytes = b"video") -> None:
        self.profile = profile
        self.payload = payload
        self.profile_calls = 0
        self.range_calls = 0
        self.full_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_render_profiles(self):
        self.profile_calls += 1
        return [self.profile]

    async def open_video(self, _: str, *, range_header: str | None = None):
        if range_header:
            self.range_calls += 1
            return httpx.Response(
                206,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-0/{len(self.payload)}",
                    "Accept-Ranges": "bytes",
                },
                content=self.payload[:1],
            )
        self.full_calls += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "video/mp4", "Accept-Ranges": "bytes"},
            content=self.payload,
        )


class UnavailableComposer(FakeComposer):
    async def get_render_profiles(self):
        raise VideoComposerTransientError("offline")

    async def open_video(self, _: str, *, range_header: str | None = None):
        raise VideoComposerTransientError("offline")


def _service(*, composer: FakeComposer | None = None, qa_repo=None):
    inputs = _inputs()
    inputs.workflow.perfil_producao = "game_analysis"
    inputs.workflow.transcricao = inputs.transcription
    inputs.workflow.blocos = []
    inputs.render.composer_render_id = str(uuid.uuid4())
    repository = qa_repo or InMemoryQARepository()
    composer = composer or FakeComposer(inputs.render_profile)
    service = ProductionQAService(
        AsyncMock(),
        Settings(video_composer_http_max_retries=0),
        workflow_repository=FakeWorkflowRepository(inputs.workflow),
        qa_repository=repository,
        alignment_service=FakeStage(inputs.alignment),
        visual_service=FakeStage(inputs.visual_plan),
        timeline_service=FakeStage(inputs.timeline),
        orchestration_service=FakeOrchestration(inputs.render),
        composer_client_factory=lambda: composer,
    )
    return service, inputs, repository, composer


@pytest.mark.asyncio
async def test_standard_qa_passes_and_is_idempotently_reused() -> None:
    service, inputs, repository, composer = _service()
    first = await service.ensure(inputs.workflow.id)
    second = await service.ensure(inputs.workflow.id)
    assert first.created is True
    assert second.created is False
    assert second.report.id == first.report.id
    assert first.report.status == "passed"
    assert first.report.publication_ready is True
    assert repository.run_count == 1
    assert composer.range_calls == 1
    assert composer.full_calls == 0


@pytest.mark.asyncio
async def test_force_reruns_qa_only_without_rebuilding_upstream() -> None:
    service, inputs, repository, composer = _service()
    await service.ensure(inputs.workflow.id)
    result = await service.ensure(inputs.workflow.id, force=True)
    assert result.created is False
    assert repository.run_count == 2
    assert composer.range_calls == 2
    assert service.alignments.ensure.await_count == 0
    assert service.visual.ensure.await_count == 0
    assert service.timelines.ensure.await_count == 0


@pytest.mark.asyncio
async def test_standard_qa_composer_outage_blocks_without_demoting_production() -> None:
    service, inputs, _, _ = _service(composer=UnavailableComposer(_inputs().render_profile))
    result = await service.ensure(inputs.workflow.id)
    assert result.report.status == "blocked"
    assert result.report.publication_ready is False
    assert result.report.error_category == "video_composer_unavailable"
    assert inputs.workflow.production_status == "completed"


@pytest.mark.asyncio
async def test_deep_qa_streams_hashes_and_probes_without_persisting_temp_path() -> None:
    payload = b"small-controlled-mp4"
    base = _inputs()
    composer = FakeComposer(base.render_profile, payload)
    service, inputs, _, _ = _service(composer=composer)
    inputs.render.output_sha256 = hashlib.sha256(payload).hexdigest()
    inputs.render.output_byte_size = len(payload)
    probe = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
        ],
        "format": {"duration": "10.0"},
    }
    service._ffprobe = AsyncMock(return_value=(probe, None))
    result = await service.ensure(inputs.workflow.id, mode="deep")
    by_code = {item["code"]: item for item in result.report.checks}
    assert result.report.status == "passed"
    assert by_code["full_content_sha"]["result"] == "passed"
    assert by_code["deep_ffprobe_validation"]["result"] == "passed"
    assert composer.range_calls == 1
    assert composer.full_calls == 1
    assert "temp" not in str(result.report.metrics).lower()


@pytest.mark.asyncio
async def test_deep_qa_sha_mismatch_fails() -> None:
    payload = b"different"
    base = _inputs()
    composer = FakeComposer(base.render_profile, payload)
    service, inputs, _, _ = _service(composer=composer)
    inputs.render.output_byte_size = len(payload)
    service._ffprobe = AsyncMock(return_value=({}, "invalid"))
    result = await service.ensure(inputs.workflow.id, mode="deep")
    by_code = {item["code"]: item for item in result.report.checks}
    assert by_code["full_content_sha"]["result"] == "failed"
    assert result.report.publication_ready is False


@pytest.mark.asyncio
async def test_deep_size_limit_blocks_before_full_download() -> None:
    base = _inputs()
    composer = FakeComposer(base.render_profile)
    service, inputs, _, _ = _service(composer=composer)
    service.config = Settings(production_qa_deep_max_video_bytes=10)
    inputs.render.output_byte_size = 11
    result = await service.ensure(inputs.workflow.id, mode="deep")
    by_code = {item["code"]: item for item in result.report.checks}
    assert by_code["deep_content_size"]["result"] == "blocked"
    assert composer.full_calls == 0


@pytest.mark.asyncio
async def test_missing_timeline_or_render_is_explicit_precondition_error() -> None:
    service, inputs, _, _ = _service()
    service.timelines.item = None
    with pytest.raises(ProductionQAPreconditionError):
        await service.ensure(inputs.workflow.id)


@pytest.mark.asyncio
async def test_mode_and_changed_upstream_identity_create_distinct_reports() -> None:
    repository = InMemoryQARepository()
    service, inputs, _, _ = _service(qa_repo=repository)
    standard = await service.ensure(inputs.workflow.id)
    deep = await service.ensure(inputs.workflow.id, mode="deep")
    assert standard.report.id != deep.report.id
    inputs.render.output_sha256 = "1" * 64
    changed = await service.ensure(inputs.workflow.id)
    assert changed.report.id not in {standard.report.id, deep.report.id}
