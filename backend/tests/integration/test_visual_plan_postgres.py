from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from app.core.database import task_session
from app.models.workflow import (
    AudioBloco,
    NarrationAlignment,
    VisualDecision,
    VisualPlan,
    WorkflowExecucao,
)
from app.repositories.visual_plan_repository import VisualPlanRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 for PostgreSQL constraint/cascade validation",
)


@pytest.mark.asyncio
async def test_visual_plan_uniqueness_force_persistence_and_workflow_cascade() -> None:
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    alignment_id = uuid.uuid4()
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
        block = AudioBloco(
            id=block_id,
            workflow_execucao_id=workflow_id,
            ordem=0,
            tipo="intro",
            texto="texto",
            status="concluido",
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
        session.add_all([workflow, block, alignment])
        await session.commit()

        repository = VisualPlanRepository(session)
        plan, created = await repository.create_or_get(
            workflow_execucao_id=workflow_id,
            narration_alignment_id=alignment_id,
            script_fingerprint="d" * 64,
            media_scope={"media_project_id": str(uuid.uuid4())},
            media_scope_fingerprint="e" * 64,
            strategy_version="visual_director_v1",
            request_fingerprint=request_fingerprint,
            fingerprint=request_fingerprint,
            status="pending",
        )
        repeated, repeated_created = await repository.create_or_get(
            workflow_execucao_id=workflow_id,
            narration_alignment_id=alignment_id,
            script_fingerprint="d" * 64,
            media_scope={"media_project_id": str(uuid.uuid4())},
            media_scope_fingerprint="e" * 64,
            strategy_version="visual_director_v1",
            request_fingerprint=request_fingerprint,
            fingerprint=request_fingerprint,
            status="pending",
        )
        assert created is True
        assert repeated_created is False
        assert repeated.id == plan.id

        decision = VisualDecision(
            visual_plan_id=plan.id,
            visual_query_id=query_id,
            audio_bloco_id=block_id,
            block_order=0,
            query_order=0,
            query="Aard",
            intent="evidence",
            importance="required",
            narration_binding_status="resolved",
            narration_start_seconds=1,
            narration_end_seconds=3,
            target_narration_duration_seconds=2,
            source_resolution_status="resolved",
            selection_strategy="media_search",
            selected_source={
                "format_version": "visual_source_v1",
                "source_type": "gameplay",
                "project_id": str(uuid.uuid4()),
                "session_id": str(uuid.uuid4()),
                "media_id": str(uuid.uuid4()),
                "source_start_seconds": 1200,
                "source_end_seconds": 1240,
            },
            alternatives=[],
            selection_score=0.8,
        )
        await repository.replace_outputs(
            plan,
            decisions=[decision],
            status="completed",
            fingerprint="f" * 64,
            media_index_provenance={"temporal_index_ids": [str(uuid.uuid4())]},
            metrics={"media_search_calls": 1, "reused_source_count": 0},
            diagnostics={"candidate_format_version": "visual_source_v1"},
        )
        loaded = await repository.get_by_request_fingerprint(request_fingerprint)
        assert loaded is not None
        assert len(loaded.decisions) == 1
        assert loaded.decisions[0].visual_query_id == query_id

        await repository.clear_outputs(loaded)
        await repository.replace_outputs(
            loaded,
            decisions=[
                VisualDecision(
                    visual_plan_id=loaded.id,
                    visual_query_id=query_id,
                    audio_bloco_id=block_id,
                    block_order=0,
                    query_order=0,
                    query="Aard",
                    intent="evidence",
                    importance="required",
                    narration_binding_status="resolved",
                    narration_start_seconds=1,
                    narration_end_seconds=3,
                    source_resolution_status="unresolved",
                    alternatives=[],
                    reason="no results",
                )
            ],
            status="completed_with_gaps",
            fingerprint="1" * 64,
            media_index_provenance={"temporal_index_ids": []},
            metrics={"media_search_calls": 1, "reused_source_count": 0},
            diagnostics={},
        )
        rebuilt = await repository.get_by_id(plan.id)
        assert rebuilt is not None
        assert len(rebuilt.decisions) == 1
        assert rebuilt.decisions[0].source_resolution_status == "unresolved"

        plan_id = plan.id
        await session.delete(workflow)
        await session.commit()
        session.expire_all()
        assert await session.get(VisualPlan, plan_id) is None
        remaining = await session.execute(
            select(VisualDecision).where(VisualDecision.visual_plan_id == plan_id)
        )
        assert remaining.scalars().all() == []
