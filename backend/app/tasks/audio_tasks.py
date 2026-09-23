"""Celery entrypoint and durable remote-job restart reconciliation."""

from __future__ import annotations

import asyncio
import logging
import uuid

from celery.signals import worker_ready

from app.clients.voice_client import (
    VoiceClient,
    VoiceClientError,
    VoiceClientTransientError,
    VoiceJobNotFoundError,
)
from app.core.celery_app import celery_app
from app.core.database import task_session
from app.core.logging_config import set_workflow_id
from app.repositories.workflow_repository import WorkflowRepository
from app.services.audio_service import processar_workflow

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.audio_tasks.gerar_audio_workflow", bind=True, max_retries=0)
def gerar_audio_workflow(self, workflow_id: str) -> None:
    set_workflow_id(workflow_id)
    asyncio.run(_processar(workflow_id))


async def _processar(workflow_id: str) -> None:
    async with task_session() as session:
        await processar_workflow(session, uuid.UUID(workflow_id))


async def reconciliar_blocos_orfaos() -> None:
    """Resume workflows without replacing the persisted job/seed identity."""
    async with task_session() as session:
        repo = WorkflowRepository(session)
        orphaned = await repo.obter_blocos_orfaos()
        workflow_ids: set[uuid.UUID] = set()
        async with VoiceClient() as voice:
            for block in orphaned:
                workflow_ids.add(block.workflow_execucao_id)
                try:
                    job = await voice.get_narration(block.job_id_voz or "")
                except VoiceJobNotFoundError:
                    # The service will recreate with the persisted seed and idempotency key.
                    await repo.atualizar_bloco(
                        block.id,
                        status="pendente",
                        job_id_voz=None,
                        erro_mensagem="Remote narration no longer exists; safe recreation pending",
                    )
                except VoiceClientTransientError:
                    logger.warning(
                        "voice service unavailable during orphan reconciliation",
                        extra={"block_id": str(block.id)},
                    )
                    continue
                except VoiceClientError as exc:
                    await repo.atualizar_bloco(
                        block.id,
                        status="erro",
                        erro_mensagem=str(exc),
                    )
                    continue
                else:
                    if job.status == "failed":
                        await repo.atualizar_bloco(
                            block.id,
                            status="erro",
                            erro_mensagem=job.error_message or "Remote narration failed",
                        )
                    else:
                        # queued/running/completed all resume through the shared service.
                        await repo.atualizar_bloco(
                            block.id,
                            status="gerando",
                            resolved_seed=job.resolved_seed,
                            model_id=job.model_id,
                            model_revision=job.model_revision,
                        )
        for workflow_id in workflow_ids:
            workflow = await repo.obter_por_id(workflow_id)
            if workflow is not None and workflow.status != "cancelada":
                if workflow.production_status is None:
                    gerar_audio_workflow.delay(str(workflow_id))
                else:
                    # Stage 17's dedicated worker-ready recovery owns this row and
                    # queues the production task once after orphan reconciliation.
                    logger.info(
                        "Stage 17 audio orphan left for production recovery",
                        extra={"workflow_id": str(workflow_id)},
                    )


@worker_ready.connect
def setup_reconciliacao(sender, **kwargs) -> None:
    logger.info("worker configured; reconciling durable voice jobs")
    asyncio.run(reconciliar_blocos_orfaos())
