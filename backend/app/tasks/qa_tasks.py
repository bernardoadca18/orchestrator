"""One-shot Stage 18 QA task and deterministic restart recovery."""

from __future__ import annotations

import asyncio
import logging
import uuid

from celery.signals import worker_ready

from app.core.celery_app import celery_app, is_control_worker
from app.core.config import settings
from app.core.database import task_session
from app.core.logging_config import set_workflow_id
from app.repositories.workflow_repository import WorkflowRepository
from app.services.production_qa_service import (
    ProductionQAPreconditionError,
    ProductionQAService,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.qa_tasks.executar_qa_producao",
    bind=True,
    max_retries=settings.production_qa_task_max_retries,
)
def executar_qa_producao(self, workflow_id: str) -> None:
    set_workflow_id(workflow_id)
    status, category = asyncio.run(_execute(workflow_id, force=bool(self.request.retries)))
    if (
        status == "blocked"
        and category == "video_composer_unavailable"
        and self.request.retries < self.max_retries
    ):
        raise self.retry(
            countdown=settings.production_qa_task_retry_seconds,
            exc=RuntimeError("Video Composer unavailable during standard QA"),
        )


async def _execute(workflow_id: str, *, force: bool) -> tuple[str, str | None]:
    async with task_session() as session:
        result = await ProductionQAService(session).ensure(
            uuid.UUID(workflow_id), mode="standard", force=force
        )
        return result.report.status, result.report.error_category


async def recover_standard_qa() -> None:
    async with task_session() as session:
        workflows = await WorkflowRepository(session).completed_productions()
        service = ProductionQAService(session)
        for workflow in workflows:
            try:
                report = await service.current(workflow.id, "standard")
            except ProductionQAPreconditionError:
                continue
            if report is None or report.status in {"pending", "running"}:
                executar_qa_producao.delay(str(workflow.id))


@worker_ready.connect
def setup_qa_recovery(sender, **kwargs) -> None:
    if not is_control_worker(sender):
        return
    logger.info("worker configured; recovering missing Stage 18 standard QA")
    asyncio.run(recover_standard_qa())
