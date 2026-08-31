"""One-shot Celery tasks for the durable Stage 17 state machine."""

from __future__ import annotations

import asyncio
import logging
import uuid

from celery.signals import worker_ready  # type: ignore[import-untyped]

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import task_session
from app.core.logging_config import set_workflow_id
from app.repositories.workflow_repository import WorkflowRepository
from app.services.production_orchestration_service import ProductionOrchestrationService

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.production_tasks.processar_producao_workflow",
    bind=True,
    max_retries=0,
)
def processar_producao_workflow(self, workflow_id: str) -> None:
    set_workflow_id(workflow_id)
    asyncio.run(_process(workflow_id))


@celery_app.task(
    name="app.tasks.production_tasks.poll_video_render",
    bind=True,
    max_retries=0,
)
def poll_video_render(self, workflow_id: str, render_id: str) -> None:
    set_workflow_id(workflow_id)
    asyncio.run(_poll(workflow_id, render_id))


async def _process(workflow_id: str) -> None:
    async with task_session() as session:
        result = await ProductionOrchestrationService(session).process(uuid.UUID(workflow_id))
    if result.poll_render_id is not None:
        poll_video_render.apply_async(
            args=[workflow_id, str(result.poll_render_id)],
            countdown=settings.video_composer_poll_interval_seconds,
        )
    if result.qa_workflow_id is not None:
        from app.tasks.qa_tasks import executar_qa_producao

        executar_qa_producao.delay(str(result.qa_workflow_id))


async def _poll(workflow_id: str, render_id: str) -> None:
    async with task_session() as session:
        result = await ProductionOrchestrationService(session).poll(
            uuid.UUID(workflow_id), uuid.UUID(render_id)
        )
    if result.poll_render_id is not None:
        poll_video_render.apply_async(
            args=[workflow_id, str(result.poll_render_id)],
            countdown=settings.video_composer_poll_interval_seconds,
        )
    if result.qa_workflow_id is not None:
        from app.tasks.qa_tasks import executar_qa_producao

        executar_qa_producao.delay(str(result.qa_workflow_id))


async def recover_productions() -> None:
    async with task_session() as session:
        workflows = await WorkflowRepository(session).recoverable_productions()
        for workflow in workflows:
            if workflow.production_phase == "render":
                render = await ProductionOrchestrationService(session).current_render(workflow.id)
                if render is not None and render.composer_render_id is not None:
                    poll_video_render.apply_async(
                        args=[str(workflow.id), str(render.id)],
                        countdown=settings.video_composer_poll_interval_seconds,
                    )
                    continue
            processar_producao_workflow.delay(str(workflow.id))


@worker_ready.connect
def setup_production_recovery(sender, **kwargs) -> None:
    logger.info("worker configured; recovering Stage 17 productions")
    asyncio.run(recover_productions())
