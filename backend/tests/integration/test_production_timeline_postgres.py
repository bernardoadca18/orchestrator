from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.database import task_session
from app.models.workflow import (
    NarrationAlignment,
    ProductionTimeline,
    TimelineGap,
    TimelinePlacement,
    VisualDecision,
    VisualPlan,
    WorkflowExecucao,
)
from app.repositories.production_timeline_repository import ProductionTimelineRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 for PostgreSQL constraint/cascade validation",
)


@pytest.mark.asyncio
async def test_timeline_uniqueness_persistence_force_and_workflow_cascade() -> None:
    workflow_id = uuid.uuid4()
    alignment_id = uuid.uuid4()
    visual_plan_id = uuid.uuid4()
    visual_decision_id = uuid.uuid4()
    query_id = uuid.uuid4()
    request_fingerprint = uuid.uuid4().hex * 2
    async with task_session() as session:
        workflow = WorkflowExecucao(
            id=workflow_id,
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={"blocos": []},
            status="audio_concluido",
            audio_final_sha256="a" * 64,
            duracao_audio_segundos=10,
        )
        alignment = NarrationAlignment(
            id=alignment_id,
            workflow_execucao_id=workflow_id,
            final_audio_sha256="a" * 64,
            script_fingerprint="b" * 64,
            transcription_fingerprint="c" * 64,
            strategy_version="narration_alignment_v1",
            fingerprint=uuid.uuid4().hex * 2,
            status="completed",
        )
        visual_plan = VisualPlan(
            id=visual_plan_id,
            workflow_execucao_id=workflow_id,
            narration_alignment_id=alignment_id,
            script_fingerprint="d" * 64,
            media_scope_fingerprint="e" * 64,
            strategy_version="visual_director_v1",
            request_fingerprint=uuid.uuid4().hex * 2,
            fingerprint=uuid.uuid4().hex * 2,
            status="completed",
        )
        visual_decision = VisualDecision(
            id=visual_decision_id,
            visual_plan_id=visual_plan_id,
            visual_query_id=query_id,
            block_order=0,
            query_order=0,
            query="event",
            intent="evidence",
            importance="required",
            narration_binding_status="resolved",
            narration_start_seconds=1,
            narration_end_seconds=6,
            source_resolution_status="resolved",
            selected_source={},
            alternatives=[],
        )
        session.add_all([workflow, alignment, visual_plan, visual_decision])
        await session.commit()

        values = dict(
            workflow_execucao_id=workflow_id,
            narration_alignment_id=alignment_id,
            visual_plan_id=visual_plan_id,
            final_audio_sha256="a" * 64,
            script_fingerprint="d" * 64,
            strategy_version="production_timeline_v1",
            schema_version="production_timeline_schema_v1",
            validator_version="production_timeline_validator_v1",
            policy={"default_transition": "cut"},
            request_fingerprint=request_fingerprint,
            fingerprint=request_fingerprint,
            status="pending",
            duration_seconds=10,
        )

        async def create_concurrently():
            async with task_session() as concurrent_session:
                return await ProductionTimelineRepository(concurrent_session).create_or_get(
                    **values
                )

        concurrent_results = await asyncio.gather(create_concurrently(), create_concurrently())
        assert sum(created for _, created in concurrent_results) == 1
        assert len({item.id for item, _ in concurrent_results}) == 1
        repository = ProductionTimelineRepository(session)
        timeline = await repository.get_by_id(concurrent_results[0][0].id)
        assert timeline is not None

        placement = TimelinePlacement(
            production_timeline_id=timeline.id,
            order=0,
            visual_decision_id=visual_decision_id,
            visual_decision_ids=[str(visual_decision_id)],
            visual_query_ids=[str(query_id)],
            source_project_id=uuid.uuid4(),
            source_session_id=uuid.uuid4(),
            source_media_id=uuid.uuid4(),
            timeline_start_seconds=1,
            timeline_end_seconds=6,
            candidate_start_seconds=100,
            candidate_end_seconds=120,
            source_start_seconds=107.5,
            source_end_seconds=112.5,
            playback_rate=1,
            transition="cut",
            strategy="centered_trim",
            importance="required",
            source_provenance={},
        )
        gap = TimelineGap(
            production_timeline_id=timeline.id,
            order=0,
            timeline_start_seconds=6,
            timeline_end_seconds=10,
            reason="no_visual_query",
            severity="background",
            related_visual_query_ids=[],
        )
        await repository.replace_outputs(
            timeline,
            placements=[placement],
            gaps=[gap],
            status="completed_with_gaps",
            fingerprint="f" * 64,
            coverage_seconds=5,
            gap_seconds=5,
            coverage_ratio=0.5,
            required_gap_seconds=0,
            preferred_gap_seconds=0,
            optional_gap_seconds=0,
            background_gap_seconds=5,
            cut_count=0,
            unplaced_query_count=0,
            average_shot_duration_seconds=5,
            metrics={},
            diagnostics={"query_placement_outcomes": []},
        )
        loaded = await repository.reload(timeline)
        assert len(loaded.placements) == 1
        assert len(loaded.gaps) == 1

        await repository.clear_outputs(loaded)
        rebuilt = await repository.reload(loaded)
        assert rebuilt.placements == []
        assert rebuilt.gaps == []

        await session.execute(delete(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id))
        await session.commit()
        assert (
            await session.execute(
                select(ProductionTimeline).where(ProductionTimeline.id == timeline.id)
            )
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_hybrid_timeline_source_union_database_constraints() -> None:
    workflow_id = uuid.uuid4()
    async with task_session() as session:
        workflow = WorkflowExecucao(
            id=workflow_id,
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={"blocos": []},
            status="audio_concluido",
            audio_final_sha256="a" * 64,
            duracao_audio_segundos=10,
        )
        timeline = ProductionTimeline(
            workflow_execucao_id=workflow_id,
            final_audio_sha256="a" * 64,
            script_fingerprint="b" * 64,
            strategy_version="production_timeline_v2",
            schema_version="production_timeline_schema_v2",
            validator_version="production_timeline_validator_v2",
            policy={},
            request_fingerprint=uuid.uuid4().hex * 2,
            fingerprint=uuid.uuid4().hex * 2,
            status="completed",
            duration_seconds=10,
        )
        session.add_all([workflow, timeline])
        await session.flush()

        def static_placement(order: int, **changes) -> TimelinePlacement:
            values = dict(
                production_timeline_id=timeline.id,
                order=order,
                visual_decision_ids=[],
                visual_query_ids=[],
                source_type="static_image",
                source_project_id=uuid.uuid4(),
                source_session_id=None,
                source_media_id=uuid.uuid4(),
                source_image_semantic_index_id=uuid.uuid4(),
                timeline_start_seconds=0,
                timeline_end_seconds=3,
                candidate_start_seconds=None,
                candidate_end_seconds=None,
                source_start_seconds=None,
                source_end_seconds=None,
                playback_rate=None,
                presentation={
                    "fit": "contain",
                    "background": "blurred_fill",
                    "motion": "slow_zoom_in",
                },
                transition="cut",
                strategy="static_image_hold",
                importance="required",
                source_provenance={},
            )
            values.update(changes)
            return TimelinePlacement(**values)

        session.add(static_placement(0))
        session.add(
            TimelinePlacement(
                production_timeline_id=timeline.id,
                order=1,
                visual_decision_ids=[],
                visual_query_ids=[],
                source_type="video_segment",
                source_project_id=uuid.uuid4(),
                source_session_id=uuid.uuid4(),
                source_media_id=uuid.uuid4(),
                timeline_start_seconds=3,
                timeline_end_seconds=6,
                candidate_start_seconds=10,
                candidate_end_seconds=20,
                source_start_seconds=10,
                source_end_seconds=13,
                playback_rate=1,
                presentation=None,
                transition="cut",
                strategy="direct_target_fit",
                importance="required",
                source_provenance={},
            )
        )
        await session.flush()

        invalid = [
            static_placement(2, source_session_id=uuid.uuid4()),
            static_placement(
                3,
                candidate_start_seconds=0,
                candidate_end_seconds=3,
                source_start_seconds=0,
                source_end_seconds=3,
            ),
            static_placement(
                4,
                source_type="video_segment",
                source_image_semantic_index_id=None,
                presentation=None,
                playback_rate=1,
            ),
            static_placement(5, source_type="future_source"),
        ]
        for item in invalid:
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(item)
                    await session.flush()
        await session.rollback()
