from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.database import task_session
from app.models.workflow import ProductionTimeline, WorkflowExecucao, WorkflowRender
from app.repositories.workflow_render_repository import WorkflowRenderRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 for PostgreSQL Stage 17 validation",
)


def _workflow(**values: object) -> WorkflowExecucao:
    return WorkflowExecucao(
        id=values.pop("id", uuid.uuid4()),
        ideia_id=uuid.uuid4(),
        roteiro_id=values.pop("roteiro_id", uuid.uuid4()),
        roteiro_snapshot={"blocos": []},
        **values,
    )


@pytest.mark.asyncio
async def test_stage17_render_uniqueness_constraints_and_cascade() -> None:
    workflow_id = uuid.uuid4()
    timeline_id = uuid.uuid4()
    fingerprint = "a" * 64
    async with task_session() as session:
        workflow = _workflow(
            id=workflow_id,
            status="audio_concluido",
            production_status="running",
            production_phase="render",
        )
        timeline = ProductionTimeline(
            id=timeline_id,
            workflow_execucao_id=workflow_id,
            final_audio_sha256="b" * 64,
            script_fingerprint="c" * 64,
            strategy_version="production_timeline_v1",
            schema_version="production_timeline_schema_v1",
            validator_version="production_timeline_validator_v1",
            policy={},
            request_fingerprint="d" * 64,
            fingerprint="e" * 64,
            status="completed",
            duration_seconds=5,
        )
        session.add_all([workflow, timeline])
        await session.commit()
        values = dict(
            workflow_execucao_id=workflow_id,
            production_timeline_id=timeline_id,
            production_timeline_fingerprint="e" * 64,
            render_profile="youtube_1080p30_v1",
            mode="final",
            request_fingerprint=fingerprint,
            idempotency_key="stable-stage17-key",
        )

        async def create():
            async with task_session() as concurrent:
                return await WorkflowRenderRepository(concurrent).create_or_get(**values)

        results = await asyncio.gather(create(), create())
        assert sum(created for _, created in results) == 1
        assert len({render.id for render, _ in results}) == 1

        invalid = WorkflowRender(
            workflow_execucao_id=workflow_id,
            production_timeline_id=timeline_id,
            production_timeline_fingerprint="e" * 64,
            render_profile="youtube_1080p30_v1",
            mode="final",
            request_fingerprint="f" * 64,
            progress=1.1,
        )
        session.add(invalid)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        await session.execute(delete(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id))
        await session.commit()
        assert (
            await session.execute(
                select(WorkflowRender).where(WorkflowRender.production_timeline_id == timeline_id)
            )
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_active_workflow_index_separates_stage17_from_legacy() -> None:
    roteiro_id = uuid.uuid4()
    async with task_session() as session:
        first = _workflow(
            roteiro_id=roteiro_id,
            status="audio_concluido",
            production_status="completed",
            production_phase="completed",
        )
        session.add(first)
        await session.commit()
        active = _workflow(
            roteiro_id=roteiro_id,
            status="agendada",
            production_status="queued",
            production_phase="audio",
        )
        session.add(active)
        await session.commit()
        duplicate = _workflow(
            roteiro_id=roteiro_id,
            status="agendada",
            production_status="queued",
            production_phase="audio",
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        await session.execute(
            delete(WorkflowExecucao).where(WorkflowExecucao.roteiro_id == roteiro_id)
        )
        await session.commit()
