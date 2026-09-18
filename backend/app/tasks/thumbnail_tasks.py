import asyncio
import datetime as dt
import uuid

from celery.signals import worker_ready
from sqlalchemy import select

from app.clients.thumbnail_director import ThumbnailClientError, ThumbnailDirectorClient
from app.core.celery_app import celery_app
from app.core.database import task_session
from app.models.workflow import WorkflowThumbnailPackage


async def execute(package_id, *, retry_failed=False):
    async with task_session() as session:
        query = (
            select(WorkflowThumbnailPackage)
            .where(WorkflowThumbnailPackage.id == uuid.UUID(package_id))
            .with_for_update()
        )
        package = await session.scalar(query)
        if (
            package is None
            or package.status == "completed"
            or (package.status == "failed" and not retry_failed)
        ):
            return False
        current_id = await session.scalar(
            select(WorkflowThumbnailPackage.id)
            .where(WorkflowThumbnailPackage.workflow_execucao_id == package.workflow_execucao_id)
            .order_by(WorkflowThumbnailPackage.creative_revision.desc())
            .limit(1)
        )
        if current_id != package.id:
            return False
        package.status = "running"
        await session.commit()
        # Hold a row lock through the bounded service call to serialize deliveries.
        # Running is already durable; a worker crash releases the lock for recovery.
        package = await session.scalar(query.execution_options(populate_existing=True))
        if package.status == "completed" or (package.status == "failed" and not retry_failed):
            return False
        retryable = False
        try:
            package.result = await ThumbnailDirectorClient().generate(package.request_snapshot)
            package.status = "completed"
            package.error_category = package.error_message = None
        except ThumbnailClientError as exc:
            package.status = "failed"
            package.error_category = exc.category
            package.error_message = "Falha na geração de thumbnails; tente novamente."
            retryable = exc.retryable
        except Exception:
            package.status = "failed"
            package.error_category = "contract_error"
            package.error_message = "Falha ao validar o package de thumbnails."
        package.completed_at = dt.datetime.now(dt.UTC)
        await session.commit()
        return retryable


@celery_app.task(
    name="app.tasks.thumbnail_tasks.generate_thumbnails",
    bind=True,
    max_retries=1,
    reject_on_worker_lost=True,
)
def generate_thumbnails(self, package_id):
    retryable = asyncio.run(execute(package_id, retry_failed=bool(self.request.retries)))
    if retryable and self.request.retries < self.max_retries:
        raise self.retry(countdown=15, exc=RuntimeError("Thumbnail Director unavailable"))


async def recover():
    async with task_session() as session:
        ids = (
            await session.scalars(
                select(WorkflowThumbnailPackage.id).where(
                    WorkflowThumbnailPackage.status.in_(["pending", "running"])
                )
            )
        ).all()
        for package_id in ids:
            generate_thumbnails.delay(str(package_id))


@worker_ready.connect
def recover_thumbnails(sender, **kwargs):
    asyncio.run(recover())
