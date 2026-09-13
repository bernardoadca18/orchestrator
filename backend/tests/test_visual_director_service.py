from __future__ import annotations

import asyncio
import copy
import datetime as dt
import uuid

import pytest

from app.clients.media_indexer_search import (
    ImageSearchCandidate,
    ImageSearchFilters,
    ImageSearchResultSet,
    MediaIndexerUnavailableError,
    MediaSearchCandidate,
    MediaSearchResultSet,
    MediaSessionMetadata,
)
from app.core.config import Settings
from app.models.workflow import (
    AudioBloco,
    NarrationAlignment,
    VisualPlan,
    VisualQueryNarrationBinding,
    WorkflowExecucao,
)
from app.services.visual_director_service import (
    VISUAL_QUERIES_MISSING_CATEGORY,
    VisualDirectorInputError,
    VisualDirectorService,
)

PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000001")
SESSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000002")
OTHER_SESSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000003")
MEDIA_ID = uuid.UUID("20000000-0000-4000-8000-000000000004")
INDEX_ID = uuid.UUID("20000000-0000-4000-8000-000000000005")


class FakeWorkflowRepository:
    def __init__(self, workflow: WorkflowExecucao) -> None:
        self.workflow = workflow

    async def obter_por_id(self, workflow_id: uuid.UUID, **kwargs):
        return self.workflow if workflow_id == self.workflow.id else None


class FakeAlignmentService:
    def __init__(self, alignment: NarrationAlignment | None) -> None:
        self.alignment = alignment

    async def current(self, workflow_id: uuid.UUID):
        return self.alignment


class FakeVisualPlanRepository:
    def __init__(self) -> None:
        self.plans: dict[str, VisualPlan] = {}
        self.output_writes = 0

    async def get_by_request_fingerprint(self, fingerprint: str):
        return self.plans.get(fingerprint)

    async def create_or_get(self, **values):
        existing = self.plans.get(values["request_fingerprint"])
        if existing:
            return existing, False
        plan = VisualPlan(
            id=uuid.uuid4(),
            criado_em=dt.datetime.now(dt.UTC),
            total_queries=0,
            resolved_queries=0,
            unresolved_queries=0,
            failed_queries=0,
            required_gaps=0,
            optional_gaps=0,
            direct_provenance_count=0,
            media_search_count=0,
            reused_source_count=0,
            **values,
        )
        plan.decisions = []
        self.plans[plan.request_fingerprint] = plan
        return plan, True

    async def clear_outputs(self, plan):
        plan.decisions = []

    async def mark_processing(self, plan):
        plan.status = "processing"
        plan.started_at = dt.datetime.now(dt.UTC)

    async def mark_blocked(self, plan, reason, *, diagnostics=None):
        plan.status = "blocked"
        plan.error_message = reason
        plan.diagnostics = diagnostics

    async def mark_failed(self, plan, reason):
        plan.status = "failed"
        plan.error_message = reason

    async def replace_outputs(
        self,
        plan,
        *,
        decisions,
        status,
        fingerprint,
        media_index_provenance,
        metrics,
        diagnostics,
    ):
        self.output_writes += 1
        for item in decisions:
            if item.id is None:
                item.id = uuid.uuid4()
        plan.decisions = list(decisions)
        plan.status = status
        plan.fingerprint = fingerprint
        plan.media_index_provenance = media_index_provenance
        plan.total_queries = len(decisions)
        plan.resolved_queries = sum(
            item.source_resolution_status == "resolved" for item in decisions
        )
        plan.unresolved_queries = sum(
            item.source_resolution_status == "unresolved" for item in decisions
        )
        plan.failed_queries = sum(item.source_resolution_status == "failed" for item in decisions)
        plan.required_gaps = sum(
            item.importance == "required" and item.source_resolution_status != "resolved"
            for item in decisions
        )
        plan.optional_gaps = sum(
            item.importance != "required" and item.source_resolution_status != "resolved"
            for item in decisions
        )
        plan.direct_provenance_count = sum(
            item.selection_strategy == "preferred_provenance" for item in decisions
        )
        plan.media_search_count = metrics["media_search_calls"]
        plan.reused_source_count = metrics["reused_source_count"]
        plan.metrics = metrics
        plan.diagnostics = diagnostics
        plan.completed_at = dt.datetime.now(dt.UTC)

    async def reload(self, plan):
        return plan


class FakeProvider:
    def __init__(self) -> None:
        self.session_calls: list[uuid.UUID] = []
        self.session_search_calls: list[tuple[uuid.UUID, str]] = []
        self.project_search_calls: list[tuple[uuid.UUID, str]] = []
        self.metadata = {
            SESSION_ID: MediaSessionMetadata(
                id=SESSION_ID,
                project_id=PROJECT_ID,
                media_id=MEDIA_ID,
                label="001",
                status="ready",
                media_filename="001.mp4",
                duration_seconds=3600,
            )
        }
        self.results: list[MediaSearchCandidate] = []
        self.image_results: list[ImageSearchCandidate] = []
        self.image_search_calls: list[tuple[uuid.UUID, str, ImageSearchFilters]] = []
        self.search_error: Exception | None = None
        self.image_error: Exception | None = None

    async def get_session(self, session_id: uuid.UUID):
        self.session_calls.append(session_id)
        if session_id not in self.metadata:
            raise MediaIndexerUnavailableError("missing")
        return self.metadata[session_id]

    def _result(self, scope: str, session_id: uuid.UUID | None = None):
        if self.search_error:
            raise self.search_error
        return MediaSearchResultSet(
            query="query",
            scope=scope,
            project_id=PROJECT_ID,
            session_id=session_id,
            results=tuple(self.results),
            searched_sessions=1,
            skipped_sessions=0,
            session_summaries=(
                {
                    "id": str(SESSION_ID),
                    "label": "001",
                    "searched": True,
                    "result_count": len(self.results),
                    "skip_reason": None,
                },
            ),
            strategy="media_search_v4_hybrid_evidence",
            embedding_provider="ollama",
            embedding_model="mxbai-embed-large:latest",
            embedding_dimensions=1024,
        )

    async def search_session(self, session_id: uuid.UUID, *, query: str, **kwargs):
        self.session_search_calls.append((session_id, query))
        return self._result("session", session_id)

    async def search_project(self, project_id: uuid.UUID, *, query: str, **kwargs):
        self.project_search_calls.append((project_id, query))
        return self._result("project")

    async def search_images(
        self,
        project_id: uuid.UUID,
        *,
        query: str,
        filters: ImageSearchFilters,
        **kwargs,
    ):
        self.image_search_calls.append((project_id, query, filters))
        if self.image_error:
            raise self.image_error
        return ImageSearchResultSet(
            query=query,
            project_id=project_id,
            results=tuple(self.image_results),
            searched_indexes=1,
            compatibility_groups=1,
            filters=filters,
        )


def _query(
    *,
    query_id: uuid.UUID | None = None,
    order: int = 0,
    text: str = "Geralt learns Aard",
    importance: str = "required",
    preferred_session_id: uuid.UUID | None = SESSION_ID,
    start: float | None = 1200.0,
    end: float | None = 1240.0,
) -> dict:
    return {
        "id": str(query_id or uuid.uuid4()),
        "order": order,
        "intent": "evidence",
        "query": text,
        "narration_excerpt": "aprende Aard",
        "importance": importance,
        "source_note_id": str(uuid.uuid4()),
        "preferred_session_id": str(preferred_session_id) if preferred_session_id else None,
        "preferred_start_seconds": start,
        "preferred_end_seconds": end,
        "desired_duration_seconds": 8.0,
    }


def _entities(
    queries: list[dict],
    *,
    media_project_id: uuid.UUID | None = PROJECT_ID,
    media_session_ids: list[uuid.UUID] | None = None,
) -> tuple[WorkflowExecucao, NarrationAlignment]:
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    workflow = WorkflowExecucao(
        id=workflow_id,
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        perfil_producao="game_analysis",
        roteiro_snapshot={
            "titulo": "Aard",
            "duracao_total_segundos": 20,
            "media_context": {
                "media_project_id": str(media_project_id) if media_project_id else None,
                "media_session_ids": [str(value) for value in media_session_ids]
                if media_session_ids is not None
                else None,
            }
            if media_project_id or media_session_ids is not None
            else None,
            "blocos": [
                {
                    "ordem": 0,
                    "tipo": "intro",
                    "conteudo": "Geralt aprende Aard.",
                    "visual_queries": queries,
                }
            ],
        },
        status="audio_concluido",
        audio_final_sha256="a" * 64,
        duracao_audio_segundos=10,
    )
    workflow.blocos = [
        AudioBloco(
            id=block_id,
            workflow_execucao_id=workflow_id,
            ordem=0,
            tipo="intro",
            texto="Geralt aprende Aard.",
            status="concluido",
        )
    ]
    alignment = NarrationAlignment(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        final_audio_sha256="a" * 64,
        script_fingerprint="b" * 64,
        transcription_fingerprint="c" * 64,
        strategy_version="narration_alignment_v1",
        fingerprint="d" * 64,
        status="completed",
        criado_em=dt.datetime.now(dt.UTC),
    )
    alignment.block_alignments = []
    alignment.visual_query_bindings = [
        VisualQueryNarrationBinding(
            id=uuid.uuid4(),
            narration_alignment_id=alignment.id,
            audio_bloco_id=block_id,
            visual_query_id=uuid.UUID(item["id"]),
            block_order=0,
            query_order=item["order"],
            status="resolved",
            narration_start_seconds=float(index * 4),
            narration_end_seconds=float(index * 4 + 3),
            excerpt_match_score=1,
            speech_alignment_coverage=1,
            binding_confidence=1,
        )
        for index, item in enumerate(queries)
    ]
    return workflow, alignment


@pytest.mark.asyncio
async def test_nonempty_final_faceless_narration_with_zero_queries_is_blocked() -> None:
    workflow, alignment = _entities([])
    workflow.perfil_producao = "faceless_pop_culture_lists"
    provider = FakeProvider()
    service, repository = _service(workflow, alignment, provider)

    plan, created = await service.ensure(workflow.id)

    assert created is True
    assert plan.status == "blocked"
    assert plan.total_queries == 0
    assert plan.diagnostics == {
        "error_category": VISUAL_QUERIES_MISSING_CATEGORY,
        "production_profile": "faceless_pop_culture_lists",
        "narration_duration_seconds": 10,
        "visual_query_count": 0,
    }
    assert repository.output_writes == 0
    assert provider.session_calls == []
    assert provider.session_search_calls == []
    assert provider.project_search_calls == []

    plan.status = "completed"
    repeated, repeated_created = await service.ensure(workflow.id)
    assert repeated_created is False
    assert repeated.status == "blocked"
    assert repeated.diagnostics["error_category"] == VISUAL_QUERIES_MISSING_CATEGORY


def _candidate(
    start: float = 1200,
    end: float = 1240,
    score: float = 0.8,
    *,
    session_id: uuid.UUID = SESSION_ID,
) -> MediaSearchCandidate:
    return MediaSearchCandidate(
        session_id=session_id,
        media_id=MEDIA_ID,
        start_seconds=start,
        end_seconds=end,
        duration_seconds=end - start,
        score=score,
        rank=1,
        session_label="001",
        media_filename="001.mp4",
        temporal_index_id=INDEX_ID,
        retrieval_candidate_id=None,
        transcript_preview="Geralt aprende Aard",
        matched_note_ids=(),
        provenance={"compatibility_signature": "sig"},
    )


def _image_candidate(score: float = 0.8) -> ImageSearchCandidate:
    return ImageSearchCandidate(
        project_id=PROJECT_ID,
        media_id=uuid.uuid4(),
        score=score,
        rank=1,
        original_filename="official.png",
        content_type="image/png",
        byte_size=123,
        sha256="e" * 64,
        width=1920,
        height=1080,
        format_name="PNG",
        provenance={"asset_class": "official_screenshot"},
        semantic_preview="Vice City",
        image_semantic_index_id=uuid.uuid4(),
        semantic_index={"strategy": "image_semantic_v1"},
    )


def _service(workflow, alignment, provider, repository=None, config=None):
    repository = repository or FakeVisualPlanRepository()
    config = config or Settings(
        visual_director_search_concurrency=2,
        visual_director_min_semantic_score=0.25,
    )
    return (
        VisualDirectorService(
            None,
            config,
            provider=provider,
            workflow_repository=FakeWorkflowRepository(workflow),
            alignment_service=FakeAlignmentService(alignment),
            visual_plan_repository=repository,
        ),
        repository,
    )


@pytest.mark.asyncio
async def test_valid_preferred_provenance_resolves_without_media_search() -> None:
    workflow, alignment = _entities([_query()], media_session_ids=[SESSION_ID])
    snapshot = copy.deepcopy(workflow.roteiro_snapshot)
    provider = FakeProvider()
    service, _ = _service(workflow, alignment, provider)
    plan, created = await service.ensure(workflow.id)
    decision = plan.decisions[0]
    assert created is True
    assert plan.status == "completed"
    assert decision.selection_strategy == "preferred_provenance"
    assert decision.selected_source["source_start_seconds"] == 1200
    assert decision.narration_start_seconds == 0
    assert provider.session_calls == [SESSION_ID]
    assert provider.session_search_calls == []
    assert provider.project_search_calls == []
    assert workflow.roteiro_snapshot == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(("start", "end"), [(10, 10), (-1, 10)])
async def test_invalid_preferred_range_falls_back_to_session_search(start, end) -> None:
    workflow, alignment = _entities([_query(start=start, end=end)], media_session_ids=[SESSION_ID])
    provider = FakeProvider()
    provider.results = [_candidate()]
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.decisions[0].selection_strategy == "media_search"
    assert provider.session_calls == []
    assert len(provider.session_search_calls) == 1


@pytest.mark.asyncio
async def test_session_outside_explicit_scope_uses_project_fallback() -> None:
    workflow, alignment = _entities(
        [_query(preferred_session_id=OTHER_SESSION_ID)], media_session_ids=[SESSION_ID]
    )
    provider = FakeProvider()
    provider.results = [_candidate()]
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.decisions[0].selected_source["session_id"] == str(SESSION_ID)
    assert provider.session_calls == []
    assert len(provider.project_search_calls) == 1


@pytest.mark.asyncio
async def test_duplicate_queries_reuse_search_but_keep_separate_decisions() -> None:
    queries = [
        _query(order=0, preferred_session_id=None, start=None, end=None),
        _query(order=1, preferred_session_id=None, start=None, end=None),
    ]
    workflow, alignment = _entities(queries)
    provider = FakeProvider()
    provider.results = [_candidate()]
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert len(plan.decisions) == 2
    assert len(provider.project_search_calls) == 1
    assert plan.media_search_count == 1
    assert plan.reused_source_count == 1


@pytest.mark.asyncio
async def test_required_and_optional_no_results_are_explicit_gaps() -> None:
    queries = [
        _query(order=0, importance="required", preferred_session_id=None, start=None, end=None),
        _query(order=1, importance="optional", preferred_session_id=None, start=None, end=None),
    ]
    workflow, alignment = _entities(queries)
    provider = FakeProvider()
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "completed_with_gaps"
    assert plan.required_gaps == 1
    assert plan.optional_gaps == 1
    assert {item.source_resolution_status for item in plan.decisions} == {"unresolved"}


@pytest.mark.asyncio
async def test_hybrid_image_only_result_is_selected_without_temporal_fields() -> None:
    workflow, alignment = _entities([_query(preferred_session_id=None, start=None, end=None)])
    workflow.roteiro_snapshot["media_context"]["static_images"] = {
        "enabled": True,
        "asset_classes": ["official_screenshot"],
        "collections": [],
        "source_owners": [],
        "source_classes": [],
    }
    provider = FakeProvider()
    provider.image_results = [_image_candidate()]
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    source = plan.decisions[0].selected_source
    assert plan.strategy_version == "visual_director_v2_hybrid_sources"
    assert source["source_type"] == "static_image"
    assert "source_start_seconds" not in source
    assert len(provider.project_search_calls) == 1
    assert provider.image_search_calls[0][2].asset_class == "official_screenshot"


@pytest.mark.asyncio
async def test_empty_image_filters_issue_one_request_and_combinations_are_bounded() -> None:
    workflow, alignment = _entities([_query(preferred_session_id=None, start=None, end=None)])
    workflow.roteiro_snapshot["media_context"]["static_images"] = {"enabled": True}
    provider = FakeProvider()
    provider.image_results = [_image_candidate()]
    service, _ = _service(workflow, alignment, provider)
    await service.ensure(workflow.id)
    assert len(provider.image_search_calls) == 1
    assert provider.image_search_calls[0][2].to_dict() == {}

    workflow.roteiro_snapshot["media_context"]["static_images"] = {
        "enabled": True,
        "asset_classes": ["a", "b", "c"],
        "collections": ["one", "two", "three"],
        "source_classes": ["official", "licensed", "press"],
    }
    with pytest.raises(
        VisualDirectorInputError,
        match="static image filter combinations exceed configured limit",
    ):
        await service.ensure(workflow.id, force=True)


@pytest.mark.asyncio
async def test_hybrid_image_failure_degrades_to_video_branch() -> None:
    workflow, alignment = _entities([_query(preferred_session_id=None, start=None, end=None)])
    workflow.roteiro_snapshot["media_context"]["static_images"] = {"enabled": True}
    provider = FakeProvider()
    provider.results = [_candidate()]
    provider.image_error = MediaIndexerUnavailableError("offline")
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "completed"
    assert plan.decisions[0].selected_source["source_type"] == "video_segment"
    assert plan.decisions[0].diagnostics["image_branch_error"] == "unavailable"


@pytest.mark.asyncio
async def test_media_indexer_outage_differs_from_no_results_and_keeps_direct_decision() -> None:
    queries = [
        _query(order=0),
        _query(order=1, preferred_session_id=None, start=None, end=None),
    ]
    workflow, alignment = _entities(queries)
    provider = FakeProvider()
    provider.search_error = MediaIndexerUnavailableError("offline")
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "completed_with_gaps"
    assert [item.source_resolution_status for item in plan.decisions] == ["resolved", "failed"]
    assert plan.decisions[1].reason == "Media Search failed: unavailable"


@pytest.mark.asyncio
async def test_idempotency_force_and_changed_query() -> None:
    query = _query(preferred_session_id=None, start=None, end=None)
    workflow, alignment = _entities([query])
    provider = FakeProvider()
    provider.results = [_candidate()]
    repository = FakeVisualPlanRepository()
    service, _ = _service(workflow, alignment, provider, repository)
    first, first_created = await service.ensure(workflow.id)
    repeated, repeated_created = await service.ensure(workflow.id)
    forced, forced_created = await service.ensure(workflow.id, force=True)
    assert first_created is True
    assert repeated_created is False
    assert forced_created is False
    assert first.id == repeated.id == forced.id
    assert len(provider.project_search_calls) == 2
    assert repository.output_writes == 2

    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]["query"] = "Kaer Morhen"
    changed, changed_created = await service.ensure(workflow.id)
    assert changed_created is True
    assert changed.id != first.id


@pytest.mark.asyncio
async def test_changed_media_scope_creates_distinct_visual_plan_identity() -> None:
    query = _query(preferred_session_id=None, start=None, end=None)
    workflow, alignment = _entities([query])
    provider = FakeProvider()
    provider.results = [_candidate()]
    repository = FakeVisualPlanRepository()
    service, _ = _service(workflow, alignment, provider, repository)
    first, _ = await service.ensure(workflow.id)

    workflow.roteiro_snapshot["media_context"]["media_project_id"] = str(uuid.uuid4())
    changed, created = await service.ensure(workflow.id)

    assert created is True
    assert changed.id != first.id
    assert changed.media_scope_fingerprint != first.media_scope_fingerprint


@pytest.mark.asyncio
async def test_missing_alignment_creates_blocked_artifact_without_provider_calls() -> None:
    workflow, _ = _entities([_query()])
    provider = FakeProvider()
    service, _ = _service(workflow, None, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "blocked"
    assert "NarrationAlignment" in plan.error_message
    assert provider.session_calls == []
    assert provider.project_search_calls == []


@pytest.mark.asyncio
async def test_missing_scope_persists_decisions_and_specific_aggregate_diagnostic() -> None:
    query = _query(preferred_session_id=None, start=None, end=None)
    workflow, alignment = _entities([query])
    workflow.roteiro_snapshot["media_context"] = None
    provider = FakeProvider()
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "blocked"
    assert plan.error_message == "media scope is unavailable"
    assert plan.diagnostics["error_category"] == "visual_plan_media_scope_missing"
    assert plan.decisions[0].reason == "media scope is unavailable"
    assert plan.media_search_count == 0
    assert provider.project_search_calls == []


@pytest.mark.asyncio
async def test_below_threshold_candidate_is_not_selected() -> None:
    workflow, alignment = _entities([_query(preferred_session_id=None, start=None, end=None)])
    provider = FakeProvider()
    provider.results = [_candidate(score=0.1)]
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.decisions[0].source_resolution_status == "unresolved"
    assert plan.decisions[0].selected_source is None


@pytest.mark.asyncio
async def test_exact_duplicate_collapse_is_blocked_by_quality_gate() -> None:
    queries = [
        _query(
            order=index,
            text=f"distinct visual query {index}",
            preferred_session_id=None,
            start=None,
            end=None,
        )
        for index in range(22)
    ]
    workflow, alignment = _entities(queries)
    provider = FakeProvider()
    provider.results = [_candidate(start=285.688, end=346.788, score=0.45)]
    service, _ = _service(workflow, alignment, provider)
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "blocked"
    assert plan.resolved_queries == 22
    assert plan.metrics["unique_source_window_count"] == 1
    assert plan.metrics["reused_source_count"] == 21
    assert plan.diagnostics["error_category"] == "visual_plan_degenerate_reuse"


@pytest.mark.asyncio
async def test_provenance_alignment_and_strategy_changes_create_new_identity() -> None:
    query = _query()
    workflow, alignment = _entities([query])
    provider = FakeProvider()
    repository = FakeVisualPlanRepository()
    first_service, _ = _service(workflow, alignment, provider, repository)
    first, _ = await first_service.ensure(workflow.id)

    query["preferred_start_seconds"] = 1201.0
    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]["preferred_start_seconds"] = 1201.0
    provenance_changed, _ = await first_service.ensure(workflow.id)

    alignment_changed = copy.copy(alignment)
    alignment_changed.id = uuid.uuid4()
    alignment_changed.fingerprint = "9" * 64
    alignment_changed.visual_query_bindings = alignment.visual_query_bindings
    alignment_service, _ = _service(workflow, alignment_changed, provider, repository)
    new_alignment, _ = await alignment_service.ensure(workflow.id)

    strategy_service, _ = _service(
        workflow,
        alignment_changed,
        provider,
        repository,
        Settings(visual_director_strategy_version="visual_director_v3_test"),
    )
    new_strategy, _ = await strategy_service.ensure(workflow.id)
    assert len({first.id, provenance_changed.id, new_alignment.id, new_strategy.id}) == 4


@pytest.mark.asyncio
async def test_query_guard_fails_before_any_network_work() -> None:
    queries = [
        _query(query_id=uuid.uuid4(), order=index, text=f"query {index}") for index in range(3)
    ]
    workflow, alignment = _entities(queries)
    provider = FakeProvider()
    service, _ = _service(
        workflow,
        alignment,
        provider,
        config=Settings(visual_director_max_queries=2),
    )
    plan, _ = await service.ensure(workflow.id)
    assert plan.status == "failed"
    assert "configured limit" in plan.error_message
    assert provider.session_calls == []
    assert provider.project_search_calls == []


@pytest.mark.asyncio
async def test_search_concurrency_is_bounded_and_output_order_stable() -> None:
    class ConcurrentProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum_active = 0

        async def search_project(self, project_id: uuid.UUID, *, query: str, **kwargs):
            self.project_search_calls.append((project_id, query))
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return self._result("project")

    queries = [
        _query(
            order=index,
            text=f"distinct query {index}",
            preferred_session_id=None,
            start=None,
            end=None,
        )
        for index in range(5)
    ]
    workflow, alignment = _entities(queries)
    provider = ConcurrentProvider()
    provider.results = [_candidate()]
    service, _ = _service(
        workflow,
        alignment,
        provider,
        config=Settings(visual_director_search_concurrency=2),
    )
    plan, _ = await service.ensure(workflow.id)
    assert provider.maximum_active == 2
    assert [item.query_order for item in plan.decisions] == list(range(5))


@pytest.mark.asyncio
async def test_malformed_visual_query_is_rejected_as_contract_input() -> None:
    workflow, alignment = _entities([_query(text="   ")])
    service, _ = _service(workflow, alignment, FakeProvider())

    with pytest.raises(VisualDirectorInputError, match="must not be empty"):
        await service.ensure(workflow.id)

    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]["query"] = "valid"
    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]["order"] = "invalid"
    with pytest.raises(VisualDirectorInputError, match="must be an integer"):
        await service.ensure(workflow.id)
