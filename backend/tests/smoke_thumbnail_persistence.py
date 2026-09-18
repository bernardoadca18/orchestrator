"""Opt-in PostgreSQL smoke. All synthetic rows are rolled back; no worker dispatch."""

import asyncio
import copy
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models.workflow import WorkflowExecucao, WorkflowThumbnailPackage
from app.services.thumbnails import ThumbnailService
from tests.test_thumbnails import package, workflow


async def main():
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    fake = workflow()
                    session.add(
                        WorkflowExecucao(
                            id=fake.id,
                            ideia_id=uuid.uuid4(),
                            roteiro_id=fake.roteiro_id,
                            roteiro_snapshot=fake.roteiro_snapshot,
                            status="audio_concluido",
                        )
                    )
                    await session.commit()
                    service = ThumbnailService(session)
                    first = await service.ensure(fake.id)
                    assert (await service.ensure(fake.id)).id == first.id
                    first.result = package(first.request_snapshot)
                    first.status = "completed"
                    await session.commit()
                    await service.select(fake.id, "B", first.id)
                    frozen = copy.deepcopy(first.result)
                    second = await service.ensure(fake.id, True)
                    assert (
                        second.creative_revision == 1
                        and second.request_fingerprint != first.request_fingerprint
                    )
                    assert second.selected_candidate_id is None
                    try:
                        await service.select(fake.id, "A", first.id)
                    except HTTPException as exc:
                        assert exc.status_code == 409
                    else:
                        raise AssertionError("Stale package selection accepted")
                    rows = (
                        await session.scalars(
                            select(WorkflowThumbnailPackage)
                            .where(WorkflowThumbnailPackage.workflow_execucao_id == fake.id)
                            .order_by(WorkflowThumbnailPackage.creative_revision)
                        )
                    ).all()
                    assert (
                        len(rows) == 2
                        and rows[0].result == frozen
                        and rows[0].selected_candidate_id == "B"
                    )
                    print(
                        "PostgreSQL: create/reuse, selection, revision and immutable history "
                        "OK (rolled back)"
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
