from __future__ import annotations

import copy
import datetime as dt
import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.workflow import (
    NarrationAlignment,
    ProductionTimeline,
    VisualDecision,
    VisualPlan,
    WorkflowExecucao,
)
from app.services.production_timeline_service import ProductionTimelineService


class FakeWorkflowRepository:
    def __init__(self, workflow: WorkflowExecucao) -> None:
        self.workflow = workflow

    async def obter_por_id(self, workflow_id: uuid.UUID, **kwargs):
        return self.workflow if workflow_id == self.workflow.id else None


class ReadOnlyCurrentService:
    def __init__(self, value) -> None:
        self.value = value
        self.current_calls = 0
        self.ensure_calls = 0

    async def current(self, workflow_id: uuid.UUID):
        self.current_calls += 1
        return self.value

    async def ensure(self, *args, **kwargs):
        self.ensure_calls += 1
        raise AssertionError("Stage 15 must not rebuild an upstream artifact")


class FakeTimelineRepository:
    def __init__(self) -> None:
        self.by_fingerprint: dict[str, ProductionTimeline] = {}
        self.output_writes = 0
        self.clear_calls = 0

    async def get_by_request_fingerprint(self, fingerprint: str):
        return self.by_fingerprint.get(fingerprint)

    async def create_or_get(self, **values):
        fingerprint = values["request_fingerprint"]
        existing = self.by_fingerprint.get(fingerprint)
        if existing:
            return existing, False
        timeline = ProductionTimeline(
            id=uuid.uuid4(),
            criado_em=dt.datetime.now(dt.UTC),
            **values,
        )
        timeline.placements = []
        timeline.gaps = []
        self.by_fingerprint[fingerprint] = timeline
        return timeline, True

    async def clear_outputs(self, timeline: ProductionTimeline):
        self.clear_calls += 1
        timeline.placements = []
        timeline.gaps = []

    async def mark_processing(self, timeline: ProductionTimeline):
        timeline.status = "processing"

    async def mark_blocked(self, timeline: ProductionTimeline, reason: str):
        timeline.status = "blocked"
        timeline.error_message = reason

    async def mark_failed(self, timeline: ProductionTimeline, reason: str):
        timeline.status = "failed"
        timeline.error_message = reason

    async def replace_outputs(self, timeline: ProductionTimeline, **values):
        self.output_writes += 1
        timeline.placements = list(values.pop("placements"))
        timeline.gaps = list(values.pop("gaps"))
        timeline.status = values.pop("status")
        timeline.fingerprint = values.pop("fingerprint")
        timeline.visual_coverage_seconds = values.pop("coverage_seconds")
        timeline.visual_gap_seconds = values.pop("gap_seconds")
        timeline.visual_coverage_ratio = values.pop("coverage_ratio")
        timeline.required_gap_seconds = values.pop("required_gap_seconds")
        timeline.preferred_gap_seconds = values.pop("preferred_gap_seconds")
        timeline.optional_gap_seconds = values.pop("optional_gap_seconds")
        timeline.background_gap_seconds = values.pop("background_gap_seconds")
        timeline.cut_count = values.pop("cut_count")
        timeline.unplaced_query_count = values.pop("unplaced_query_count")
        timeline.average_shot_duration_seconds = values.pop("average_shot_duration_seconds")
        timeline.metrics = values.pop("metrics")
        timeline.diagnostics = values.pop("diagnostics")
        timeline.placement_count = len(timeline.placements)
        timeline.gap_count = len(timeline.gaps)
        timeline.completed_at = dt.datetime.now(dt.UTC)
        assert values == {}

    async def reload(self, timeline: ProductionTimeline):
        return timeline


def _source(start: float = 100, end: float = 120) -> dict:
    return {
        "format_version": "visual_source_v1",
        "source_type": "gameplay",
        "project_id": str(uuid.UUID("40000000-0000-4000-8000-000000000001")),
        "session_id": str(uuid.UUID("40000000-0000-4000-8000-000000000002")),
        "media_id": str(uuid.UUID("40000000-0000-4000-8000-000000000003")),
        "source_start_seconds": start,
        "source_end_seconds": end,
        "source_duration_seconds": end - start,
        "semantic_score": 0.8,
        "selection_score": 0.82,
        "rank": 1,
        "session_label": "001",
        "media_filename": "001.mp4",
        "matched_note_ids": [],
        "provenance": {},
    }


def _entities(*, duration: float = 20, source: dict | None = None):
    workflow = WorkflowExecucao(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={"blocos": []},
        status="audio_concluido",
        audio_final_sha256="a" * 64,
        audio_final_sample_rate=48_000,
        audio_final_channels=1,
        audio_final_byte_size=1000,
        duracao_audio_segundos=duration,
    )
    workflow.blocos = []
    alignment = NarrationAlignment(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow.id,
        final_audio_sha256=workflow.audio_final_sha256,
        script_fingerprint="b" * 64,
        transcription_fingerprint="c" * 64,
        strategy_version="narration_alignment_v1",
        fingerprint="d" * 64,
        status="completed",
    )
    decision = VisualDecision(
        id=uuid.uuid4(),
        visual_plan_id=uuid.uuid4(),
        visual_query_id=uuid.uuid4(),
        block_order=0,
        query_order=0,
        query="Geralt learns Aard",
        intent="evidence",
        importance="required",
        narration_binding_status="resolved",
        narration_start_seconds=5,
        narration_end_seconds=10,
        source_resolution_status="resolved" if source is not None else "unresolved",
        selection_strategy="media_search" if source is not None else None,
        selected_source=source,
        alternatives=[],
    )
    plan = VisualPlan(
        id=decision.visual_plan_id,
        workflow_execucao_id=workflow.id,
        narration_alignment_id=alignment.id,
        script_fingerprint="e" * 64,
        media_scope_fingerprint="f" * 64,
        strategy_version="visual_director_v1",
        request_fingerprint="1" * 64,
        fingerprint="2" * 64,
        status="completed" if source is not None else "completed_with_gaps",
    )
    plan.decisions = [decision]
    return workflow, alignment, plan


def _service(workflow, alignment, plan, repository=None, config=None):
    alignment_reader = ReadOnlyCurrentService(alignment)
    visual_reader = ReadOnlyCurrentService(plan)
    repository = repository or FakeTimelineRepository()
    service = ProductionTimelineService(
        None,
        config or Settings(),
        workflow_repository=FakeWorkflowRepository(workflow),
        alignment_service=alignment_reader,
        visual_director_service=visual_reader,
        timeline_repository=repository,
    )
    return service, repository, alignment_reader, visual_reader


def test_timeline_configuration_rejects_unsafe_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(production_timeline_min_visual_seconds=0)
    with pytest.raises(ValidationError):
        Settings(production_timeline_max_sources_per_query=0)


@pytest.mark.asyncio
async def test_service_builds_timeline_and_never_rebuilds_upstream() -> None:
    workflow, alignment, plan = _entities(source=_source())
    service, _, alignment_reader, visual_reader = _service(workflow, alignment, plan)
    timeline, created = await service.ensure(workflow.id)
    assert created is True
    assert timeline.status == "completed_with_gaps"
    assert timeline.final_audio_sha256 == workflow.audio_final_sha256
    assert timeline.narration_alignment_id == alignment.id
    assert timeline.visual_plan_id == plan.id
    assert timeline.placement_count == 1
    assert timeline.placements[0].timeline_start_seconds == 0
    assert timeline.placements[0].source_start_seconds == 102.5
    assert timeline.placements[0].playback_rate == 1
    assert alignment_reader.ensure_calls == 0
    assert visual_reader.ensure_calls == 0


@pytest.mark.asyncio
async def test_idempotency_force_and_changed_visual_plan_identity() -> None:
    workflow, alignment, plan = _entities(source=_source())
    repository = FakeTimelineRepository()
    service, _, _, _ = _service(workflow, alignment, plan, repository)
    first, first_created = await service.ensure(workflow.id)
    repeated, repeated_created = await service.ensure(workflow.id)
    assert first_created is True
    assert repeated_created is False
    assert repeated.id == first.id
    fingerprint = first.fingerprint
    placement_ids = [item.id for item in first.placements]
    gap_ids = [item.id for item in first.gaps]

    rebuilt, rebuilt_created = await service.ensure(workflow.id, force=True)
    assert rebuilt_created is False
    assert rebuilt.id == first.id
    assert rebuilt.fingerprint == fingerprint
    assert [item.id for item in rebuilt.placements] == placement_ids
    assert [item.id for item in rebuilt.gaps] == gap_ids
    assert repository.clear_calls == 1
    assert repository.output_writes == 2

    plan.fingerprint = "9" * 64
    changed, changed_created = await service.ensure(workflow.id)
    assert changed_created is True
    assert changed.id != first.id


@pytest.mark.asyncio
async def test_missing_or_incompatible_prerequisite_is_blocked_without_mutation() -> None:
    workflow, alignment, plan = _entities(source=_source())
    frozen_plan = copy.deepcopy(plan.decisions[0].selected_source)
    plan.narration_alignment_id = uuid.uuid4()
    service, _, _, _ = _service(workflow, alignment, plan)
    timeline, _ = await service.ensure(workflow.id)
    assert timeline.status == "blocked"
    assert "another NarrationAlignment" in timeline.error_message
    assert plan.decisions[0].selected_source == frozen_plan


@pytest.mark.asyncio
async def test_unresolved_source_creates_valid_gapped_timeline() -> None:
    workflow, alignment, plan = _entities(source=None)
    service, _, _, _ = _service(workflow, alignment, plan)
    timeline, _ = await service.ensure(workflow.id)
    assert timeline.status == "completed_with_gaps"
    assert timeline.placement_count == 0
    assert timeline.gap_count == 3
    assert timeline.required_gap_seconds == 5
    assert timeline.background_gap_seconds == 15
    outcomes = timeline.diagnostics["query_placement_outcomes"]
    assert outcomes[0]["status"] == "unplaced"
    assert outcomes[0]["reason"] == "source_unresolved"


@pytest.mark.asyncio
async def test_invalid_stage14_source_fails_only_derived_timeline() -> None:
    workflow, alignment, plan = _entities(source=_source(120, 100))
    service, _, _, _ = _service(workflow, alignment, plan)
    timeline, _ = await service.ensure(workflow.id)
    assert timeline.status == "failed"
    assert workflow.status == "audio_concluido"
    assert alignment.status == "completed"
    assert plan.status == "completed"


@pytest.mark.asyncio
async def test_alignment_audio_and_strategy_changes_create_new_timeline_identity() -> None:
    workflow, alignment, plan = _entities(source=_source())
    repository = FakeTimelineRepository()
    first_service, _, _, _ = _service(workflow, alignment, plan, repository)
    first, _ = await first_service.ensure(workflow.id)

    alignment.id = uuid.uuid4()
    alignment.fingerprint = "7" * 64
    plan.narration_alignment_id = alignment.id
    alignment_changed, _ = await first_service.ensure(workflow.id)

    workflow.audio_final_sha256 = "8" * 64
    alignment.final_audio_sha256 = workflow.audio_final_sha256
    audio_changed, _ = await first_service.ensure(workflow.id)

    strategy_service, _, _, _ = _service(
        workflow,
        alignment,
        plan,
        repository,
        Settings(production_timeline_strategy_version="production_timeline_v2"),
    )
    strategy_changed, _ = await strategy_service.ensure(workflow.id)
    assert len({first.id, alignment_changed.id, audio_changed.id, strategy_changed.id}) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("hybrid", [False, True])
async def test_current_reads_previous_validator_only_for_matching_upstream(hybrid):
    workflow, alignment, plan = _entities(source=_source())
    previous = Settings(
        production_timeline_validator_version="production_timeline_validator_v1",
        production_timeline_hybrid_validator_version="production_timeline_validator_v2",
    )
    if hybrid:
        plan.strategy_version = previous.visual_director_hybrid_strategy_version
        plan.decisions[0].selected_source = {
            **plan.decisions[0].selected_source,
            "source_type": "video_segment",
        }
    old, repository, _, _ = _service(workflow, alignment, plan, config=previous)
    legacy, _ = await old.ensure(workflow.id)
    current, _, alignment_reader, visual_reader = _service(workflow, alignment, plan, repository)
    assert await current.current(workflow.id) is legacy
    assert repository.output_writes == 1
    new, created = await current.ensure(workflow.id)
    assert created
    assert new.request_fingerprint != legacy.request_fingerprint
    assert new.strategy_version == legacy.strategy_version
    assert new.validator_version == f"production_timeline_validator_v{3 if hybrid else 2}"
    assert await current.current(workflow.id) is new
    plan.fingerprint = "9" * 64
    assert await current.current(workflow.id) is None
    assert alignment_reader.ensure_calls == visual_reader.ensure_calls == 0


@pytest.mark.asyncio
async def test_service_retains_unplaced_query_diagnostic_on_complete_timeline():
    workflow, alignment, plan = _entities(duration=30, source=_source())
    first = plan.decisions[0]
    first.narration_start_seconds = 0
    first.narration_end_seconds = 20
    first.selected_source = {
        **first.selected_source,
        "source_start_seconds": 0,
        "source_end_seconds": 20,
    }
    missing = VisualDecision(
        id=uuid.uuid4(),
        visual_plan_id=plan.id,
        visual_query_id=uuid.uuid4(),
        block_order=0,
        query_order=1,
        query="missing shot",
        intent="evidence",
        importance="preferred",
        narration_binding_status="resolved",
        narration_start_seconds=20,
        narration_end_seconds=25,
        source_resolution_status="unresolved",
        selected_source=None,
        alternatives=[],
    )
    covering = VisualDecision(
        id=uuid.uuid4(),
        visual_plan_id=plan.id,
        visual_query_id=uuid.uuid4(),
        block_order=0,
        query_order=2,
        query="adjacent coverage",
        intent="evidence",
        importance="preferred",
        narration_binding_status="resolved",
        narration_start_seconds=20,
        narration_end_seconds=30,
        source_resolution_status="resolved",
        selection_strategy="media_search",
        selected_source={
            **first.selected_source,
            "source_start_seconds": 20,
            "source_end_seconds": 30,
        },
        alternatives=[],
    )
    plan.decisions.extend([missing, covering])
    service, _, _, _ = _service(workflow, alignment, plan)
    timeline, _ = await service.ensure(workflow.id)
    assert timeline.status == "completed"
    assert timeline.gap_count == 0
    assert timeline.visual_coverage_ratio == 1
    assert timeline.unplaced_query_count == 1
    outcomes = timeline.diagnostics["query_placement_outcomes"]
    warning = next(
        item for item in outcomes if item["visual_query_id"] == str(missing.visual_query_id)
    )
    assert warning["status"] == "unplaced"
    assert warning["reason"] == "source_unresolved"
