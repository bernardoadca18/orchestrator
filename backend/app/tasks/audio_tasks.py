"""
Task Celery que dispara a geração de áudio de um workflow.

Roda na fila "audio_producao" com --concurrency=1 (ver core/celery_app.py)
para respeitar o lock de GPU do voice-workshop. `asyncio.run` cria um
event loop novo por execução de task — seguro aqui porque cada task abre
sua própria sessão de banco via AsyncSessionLocal.
"""
import asyncio
import logging
import uuid
from pathlib import Path

from app.clients.voice_client import VoiceClientError, VoiceClientTransientError, voice_client
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging_config import set_workflow_id
from app.repositories.workflow_repository import WorkflowRepository
from app.services.audio_service import processar_workflow
from app.services.ffmpeg_utils import obter_duracao_segundos

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.audio_tasks.gerar_audio_workflow", bind=True, max_retries=0)
def gerar_audio_workflow(self, workflow_id: str) -> None:
    set_workflow_id(workflow_id)
    asyncio.run(_processar(workflow_id))


async def _processar(workflow_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await processar_workflow(session, uuid.UUID(workflow_id))


async def reconciliar_blocos_orfaos() -> None:
    """
    Reconciliação de blocos órfãos executada no startup do worker.

    Para cada bloco com status 'gerando' e job_id_voz, consulta o status
    no voice-workshop e retoma o processamento apropriadamente.
    """
    logger.info("Iniciando reconciliação de blocos órfãos...")
    async with AsyncSessionLocal() as session:
        repo = WorkflowRepository(session)
        blocos_orfaos = await repo.obter_blocos_orfaos()

        if not blocos_orfaos:
            logger.info("Nenhum bloco órfão encontrado")
            return

        logger.info("Encontrados %d blocos órfãos para reconciliar", len(blocos_orfaos))

        for bloco in blocos_orfaos:
            try:
                await _reconciliar_bloco(repo, bloco)
            except Exception as exc:
                logger.exception("Erro ao reconciliar bloco %s: %s", bloco.id, exc)


async def _reconciliar_bloco(repo: WorkflowRepository, bloco) -> None:
    """Reconcilia um bloco órfão consultando o job no voice-workshop."""
    logger.info("Reconciliando bloco %s (job_id_voz=%s)", bloco.id, bloco.job_id_voz)

    try:
        job_status = await voice_client.consultar_job(bloco.job_id_voz)
    except (VoiceClientError, VoiceClientTransientError) as exc:
        logger.error("Falha ao consultar job %s no voice-workshop: %s", bloco.job_id_voz, exc)
        # Resetar bloco para pendente e reenfileirar workflow
        await repo.atualizar_bloco(bloco.id, status="pendente", erro_mensagem=f"Falha ao reconciliar: {exc}")
        gerar_audio_workflow.delay(str(bloco.workflow_execucao_id))
        return

    if job_status.get("status") == "nao_encontrado":
        logger.warning("Job %s não encontrado no voice-workshop - resetando bloco", bloco.job_id_voz)
        await repo.atualizar_bloco(bloco.id, status="pendente", job_id_voz=None, erro_mensagem="Job expirado ou não encontrado")
        gerar_audio_workflow.delay(str(bloco.workflow_execucao_id))
        return

    status = job_status.get("status")

    if status == "concluido":
        # Baixar áudio e continuar workflow
        logger.info("Job %s concluído - baixando áudio", bloco.job_id_voz)
        try:
            audio_bytes = await voice_client.baixar_audio(bloco.job_id_voz)
            pasta_workflow = Path(settings.audio_storage_path) / str(bloco.workflow_execucao_id)
            pasta_workflow.mkdir(parents=True, exist_ok=True)
            caminho_bloco = pasta_workflow / f"bloco_{bloco.ordem:02d}.wav"
            caminho_bloco.write_bytes(audio_bytes)
            duracao = await obter_duracao_segundos(caminho_bloco)
            await repo.atualizar_bloco(
                bloco.id, status="concluido", audio_path=str(caminho_bloco), duracao_segundos=duracao
            )
            # Reenfileirar workflow para continuar do próximo bloco
            gerar_audio_workflow.delay(str(bloco.workflow_execucao_id))
        except Exception as exc:
            logger.exception("Falha ao baixar áudio do job %s", bloco.job_id_voz)
            await repo.atualizar_bloco(bloco.id, status="erro", erro_mensagem=str(exc))
        return

    if status == "erro":
        erro_msg = job_status.get("erro", "Job falhou sem detalhe")
        logger.error("Job %s falhou no voice-workshop: %s", bloco.job_id_voz, erro_msg)
        await repo.atualizar_bloco(bloco.id, status="erro", erro_mensagem=erro_msg)
        return

    # Job ainda está rodando ou em estado inesperado - resetar para pendente
    logger.warning("Job %s em estado inesperado '%s' - resetando bloco", bloco.job_id_voz, status)
    await repo.atualizar_bloco(bloco.id, status="pendente", job_id_voz=None)
    gerar_audio_workflow.delay(str(bloco.workflow_execucao_id))


# Hook de startup do worker para executar reconciliação
@celery_app.on_after_configure.connect
def setup_reconciliacao(sender, **kwargs):
    """Executa reconciliação quando o worker termina de configurar."""
    logger.info("Worker configurado - executando reconciliação de blocos órfãos")
    asyncio.run(reconciliar_blocos_orfaos())
