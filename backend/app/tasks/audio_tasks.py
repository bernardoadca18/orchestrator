"""
Task Celery que dispara a geração de áudio de um workflow.

Roda na fila "audio_producao" com --concurrency=1 (ver core/celery_app.py)
para respeitar o lock de GPU do voice-workshop. `asyncio.run` cria um
event loop novo por execução de task — seguro aqui porque cada task abre
sua própria sessão de banco via AsyncSessionLocal.
"""
import asyncio
import uuid

from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal
from app.services.audio_service import processar_workflow


@celery_app.task(name="app.tasks.audio_tasks.gerar_audio_workflow", bind=True, max_retries=0)
def gerar_audio_workflow(self, workflow_id: str) -> None:
    asyncio.run(_processar(workflow_id))


async def _processar(workflow_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await processar_workflow(session, uuid.UUID(workflow_id))
