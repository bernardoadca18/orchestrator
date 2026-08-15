"""
Serviço central: gera o áudio completo de um workflow, bloco a bloco,
a partir do roteiro estruturado (BlocoNarrativo-compatível), usando o
voice-workshop, e concatena tudo num único arquivo final.

Chamado pela task Celery (app/tasks/audio_tasks.py) — mantido separado
para poder ser testado sem depender do broker.
"""
import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.voice_client import VoiceClientError, voice_client
from app.core.config import settings
from app.repositories.workflow_repository import WorkflowRepository
from app.services.ffmpeg_utils import concatenar_com_silencio, obter_duracao_segundos

logger = logging.getLogger(__name__)


async def processar_workflow(session: AsyncSession, workflow_id: uuid.UUID) -> None:
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        logger.warning("Workflow %s não encontrado — abortando task", workflow_id)
        return

    await repo.atualizar_status(wf.id, "gerando_audio")

    blocos_estrutura = wf.roteiro_snapshot.get("blocos", [])
    if not blocos_estrutura:
        await repo.atualizar_status(wf.id, "erro", erro_mensagem="Roteiro sem blocos narrativos")
        return

    pasta_workflow = Path(settings.audio_storage_path) / str(wf.id)
    pasta_workflow.mkdir(parents=True, exist_ok=True)

    caminhos_blocos: list[Path] = []

    for ordem, bloco_estrutura in enumerate(blocos_estrutura):
        texto = bloco_estrutura["conteudo"]
        tipo = bloco_estrutura.get("tipo", f"bloco_{ordem}")
        bloco = await repo.upsert_bloco(wf.id, ordem=ordem, tipo=tipo, texto=texto)
        await repo.atualizar_bloco(bloco.id, status="gerando")

        try:
            job_id = await voice_client.iniciar_narracao(texto=texto)
            await repo.atualizar_bloco(bloco.id, job_id_voz=job_id)

            resultado = await voice_client.aguardar_conclusao(job_id)
            audio_bytes = await voice_client.baixar_audio(job_id)

            caminho_bloco = pasta_workflow / f"bloco_{ordem:02d}.wav"
            caminho_bloco.write_bytes(audio_bytes)
            duracao = await obter_duracao_segundos(caminho_bloco)

            await repo.atualizar_bloco(
                bloco.id, status="concluido", audio_path=str(caminho_bloco), duracao_segundos=duracao
            )
            caminhos_blocos.append(caminho_bloco)

        except VoiceClientError as exc:
            logger.exception("Falha ao gerar bloco %s do workflow %s", ordem, wf.id)
            await repo.atualizar_bloco(bloco.id, status="erro", erro_mensagem=str(exc))
            await repo.atualizar_status(
                wf.id, "erro", erro_mensagem=f"Falha no bloco {ordem} ({tipo}): {exc}"
            )
            return

    caminho_final = pasta_workflow / "audio_final.wav"
    await concatenar_com_silencio(caminhos_blocos, settings.silencio_entre_blocos_ms, caminho_final)
    duracao_total = await obter_duracao_segundos(caminho_final)

    await repo.atualizar_status(
        wf.id,
        "audio_concluido",
        audio_final_path=str(caminho_final),
        duracao_audio_segundos=duracao_total,
    )
    logger.info("Workflow %s concluído — áudio final: %s (%.1fs)", wf.id, caminho_final, duracao_total)
