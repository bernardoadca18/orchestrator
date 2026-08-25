from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.database import task_session
from app.models.workflow import (
    ProductionQAReport,
    ProductionTimeline,
    WorkflowExecucao,
    WorkflowRender,
)
from app.repositories.production_qa_repository import ProductionQARepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 for PostgreSQL Stage 18 validation",
)


async def _artifact_chain():
    workflow_id = uuid.uuid4()
    timeline_id = uuid.uuid4()
    render_id = uuid.uuid4()
    async with task_session() as session:
        session.add_all(
            [
                WorkflowExecucao(
                    id=workflow_id,
                    ideia_id=uuid.uuid4(),
                    roteiro_id=uuid.uuid4(),
                    roteiro_snapshot={"blocos": []},
                    status="audio_concluido",
                    production_status="completed",
                    production_phase="completed",
                ),
                ProductionTimeline(
                    id=timeline_id,
                    workflow_execucao_id=workflow_id,
                    final_audio_sha256="a" * 64,
                    script_fingerprint="b" * 64,
                    strategy_version="production_timeline_v1",
                    schema_version="production_timeline_schema_v1",
                    validator_version="production_timeline_validator_v1",
                    policy={},
                    request_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
                    fingerprint="c" * 64,
                    status="completed",
                    duration_seconds=5,
                ),
            ]
        )
        await session.commit()
        session.add(
            WorkflowRender(
                id=render_id,
                workflow_execucao_id=workflow_id,
                production_timeline_id=timeline_id,
                production_timeline_fingerprint="c" * 64,
                render_profile="youtube_1080p30_v1",
                request_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
                status="completed",
                output_sha256="d" * 64,
            )
        )
        await session.commit()
    return workflow_id, timeline_id, render_id


def _values(workflow_id: uuid.UUID, timeline_id: uuid.UUID, render_id: uuid.UUID, mode: str):
    return dict(
        workflow_execucao_id=workflow_id,
        workflow_render_id=render_id,
        production_timeline_id=timeline_id,
        final_audio_sha256="a" * 64,
        render_output_sha256="d" * 64,
        qa_mode=mode,
        policy_name="generic_v1",
        policy_version="1",
        policy_identity="e" * 64,
        strategy_version="production_qa_v1",
        request_fingerprint=("f" if mode == "standard" else "0") * 64,
        checks=[],
    )


@pytest.mark.asyncio
async def test_stage18_persistence_uniqueness_modes_constraints_and_cascade() -> None:
    workflow_id, timeline_id, render_id = await _artifact_chain()
    standard_values = _values(workflow_id, timeline_id, render_id, "standard")

    async def create_standard():
        async with task_session() as session:
            return await ProductionQARepository(session).create_or_get(**standard_values)

    concurrent = await asyncio.gather(create_standard(), create_standard())
    assert sum(created for _, created in concurrent) == 1
    assert len({report.id for report, _ in concurrent}) == 1

    async with task_session() as session:
        deep, created = await ProductionQARepository(session).create_or_get(
            **_values(workflow_id, timeline_id, render_id, "deep")
        )
        assert created is True
        assert deep.qa_mode == "deep"

        invalid_status = ProductionQAReport(
            **{
                **_values(workflow_id, timeline_id, render_id, "standard"),
                "request_fingerprint": "1" * 64,
                "status": "impossible",
            }
        )
        session.add(invalid_status)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        invalid_counter = ProductionQAReport(
            **{
                **_values(workflow_id, timeline_id, render_id, "standard"),
                "request_fingerprint": "2" * 64,
                "check_count": -1,
            }
        )
        session.add(invalid_counter)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        await session.execute(delete(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id))
        await session.commit()
        reports = (
            (
                await session.execute(
                    select(ProductionQAReport).where(
                        ProductionQAReport.workflow_execucao_id == workflow_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert reports == []
