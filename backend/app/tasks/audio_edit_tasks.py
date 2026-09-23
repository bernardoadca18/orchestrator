"""Durable worker entrypoint for batched physical narration edits."""

from __future__ import annotations

import asyncio
import logging
import uuid

from celery.signals import worker_ready

from app.core.celery_app import celery_app
from app.core.database import task_session
from app.core.logging_config import set_workflow_id
from app.repositories.workflow_repository import WorkflowRepository
from app.services.audio_edit_service import AudioEditMaterializationService
from app.tasks.production_tasks import processar_producao_workflow

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.audio_edit_tasks.aplicar_edicoes_audio",
    bind=True,
    max_retries=0,
)
def aplicar_edicoes_audio(self, update_id: str) -> None:
    set_workflow_id(update_id)
    asyncio.run(_apply(update_id))


async def _apply(update_id: str) -> None:
    try:
        async with task_session() as session:
            result = await AudioEditMaterializationService(session).apply(
                uuid.UUID(update_id)
            )
    except Exception:
        logger.exception("audio edit update failed", extra={"update_id": update_id})
        return
    if result.performed and result.downstream_ready:
        processar_producao_workflow.delay(str(result.workflow_id))


async def recover_audio_edit_updates() -> None:
    async with task_session() as session:
        updates = await WorkflowRepository(session).recoverable_audio_edit_updates()
    for update in updates:
        aplicar_edicoes_audio.delay(str(update.id))


@worker_ready.connect
def setup_audio_edit_recovery(sender, **kwargs) -> None:
    logger.info("worker configured; recovering audio edit updates")
    asyncio.run(recover_audio_edit_updates())
