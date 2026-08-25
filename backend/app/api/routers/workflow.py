"""
Endpoints de workflow: listar roteiros aprovados disponíveis no ideas-hub,
enfileirar geração de áudio, acompanhar progresso e baixar o resultado.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ideas_hub_client import ideas_hub_client
from app.core.database import get_db
from app.models.workflow import NarrationAlignment, WorkflowExecucao
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.transcricao import TranscricaoOut
from app.schemas.workflow import (
    NarrationAlignmentEnsureOut,
    NarrationBlockAlignmentOut,
    NarrationTimelineAudioOut,
    NarrationTimelineOut,
    PacoteProducaoOut,
    RoteiroDisponivelOut,
    VisualQueryNarrationBindingOut,
    WorkflowCreateIn,
    WorkflowDetailOut,
    WorkflowOut,
)
from app.services.narration_alignment_service import (
    NarrationAlignmentNotFoundError,
    NarrationAlignmentService,
)
from app.tasks.audio_tasks import gerar_audio_workflow

router = APIRouter()


def _timeline_response(
    workflow: WorkflowExecucao, alignment: NarrationAlignment
) -> NarrationTimelineOut:
    return NarrationTimelineOut(
        workflow_id=workflow.id,
        narration_alignment_id=alignment.id,
        transcription_id=alignment.transcription_id,
        strategy_version=alignment.strategy_version,
        fingerprint=alignment.fingerprint,
        status=alignment.status,
        audio=NarrationTimelineAudioOut(
            sha256=workflow.audio_final_sha256,
            duration_seconds=workflow.duracao_audio_segundos,
            sample_rate=workflow.audio_final_sample_rate,
            channels=workflow.audio_final_channels,
        ),
        block_count=alignment.block_count,
        resolved_visual_queries=alignment.resolved_visual_queries,
        unresolved_visual_queries=alignment.unresolved_visual_queries,
        ambiguous_visual_queries=alignment.ambiguous_visual_queries,
        not_applicable_visual_queries=alignment.not_applicable_visual_queries,
        metrics=alignment.metrics,
        error_message=alignment.error_message,
        started_at=alignment.started_at,
        completed_at=alignment.completed_at,
        created_at=alignment.criado_em,
        blocks=[
            NarrationBlockAlignmentOut(
                id=block.id,
                audio_block_id=block.audio_bloco_id,
                block_order=block.block_order,
                status=block.status,
                narration_start_seconds=block.narration_start_seconds,
                narration_end_seconds=block.narration_end_seconds,
                canonical_token_count=block.canonical_token_count,
                synthesis_token_count=block.synthesis_token_count,
                asr_word_count=block.asr_word_count,
                canonical_synthesis_coverage=block.canonical_synthesis_coverage,
                synthesis_asr_coverage=block.synthesis_asr_coverage,
                overall_coverage=block.overall_coverage,
                diagnostics=block.diagnostics,
            )
            for block in alignment.block_alignments
        ],
        visual_query_bindings=[
            VisualQueryNarrationBindingOut(
                id=binding.id,
                visual_query_id=binding.visual_query_id,
                audio_block_id=binding.audio_bloco_id,
                block_order=binding.block_order,
                query_order=binding.query_order,
                status=binding.status,
                narration_start_seconds=binding.narration_start_seconds,
                narration_end_seconds=binding.narration_end_seconds,
                excerpt_match_method=binding.excerpt_match_method,
                excerpt_match_score=binding.excerpt_match_score,
                speech_alignment_coverage=binding.speech_alignment_coverage,
                binding_confidence=binding.binding_confidence,
                canonical_start_char=binding.canonical_start_char,
                canonical_end_char=binding.canonical_end_char,
                reason=binding.reason,
            )
            for binding in alignment.visual_query_bindings
        ],
    )


@router.get("/roteiros-disponiveis", response_model=list[RoteiroDisponivelOut])
async def listar_roteiros_disponiveis():
    """Proxy read-only para os roteiros aprovados no ideas-hub."""
    roteiros = await ideas_hub_client.listar_roteiros_aprovados()
    return [
        RoteiroDisponivelOut(
            id=r["id"],
            ideia_id=r["ideia_id"],
            versao=r["versao"],
            titulo=r["estrutura"]["titulo"],
            duracao_total_segundos=r["estrutura"]["duracao_total_segundos"],
            num_blocos=len(r["estrutura"]["blocos"]),
        )
        for r in roteiros
    ]


@router.post("", response_model=WorkflowOut, status_code=201)
async def criar_workflow(
    payload: WorkflowCreateIn, session: Annotated[AsyncSession, Depends(get_db)]
):
    from psycopg2.errors import UniqueViolation
    from sqlalchemy.exc import IntegrityError

    repo = WorkflowRepository(session)

    roteiro = await ideas_hub_client.obter_roteiro(payload.roteiro_id)
    if roteiro["status"] != "aprovado":
        raise HTTPException(
            400, "Roteiro precisa estar aprovado no ideas-hub para entrar na fila de produção"
        )

    # Buscar perfil_producao da ideia associada
    ideia = await ideas_hub_client.obter_ideia(uuid.UUID(roteiro["ideia_id"]))
    perfil_producao = ideia.get("perfil_producao")

    try:
        wf = await repo.criar(
            ideia_id=uuid.UUID(roteiro["ideia_id"]),
            roteiro_id=payload.roteiro_id,
            roteiro_snapshot=roteiro["estrutura"],
            perfil_producao=perfil_producao,
        )
        gerar_audio_workflow.delay(str(wf.id))
        return wf
    except IntegrityError as exc:
        if isinstance(exc.orig, UniqueViolation) and "uq_workflow_roteiro_ativo" in str(exc.orig):
            # Race condition: dois requests simultâneos passaram pela checagem de aplicação
            existente = await repo.obter_ativo_por_roteiro(payload.roteiro_id)
            if existente:
                raise HTTPException(
                    409,
                    f"Já existe um workflow ativo para este roteiro (id={existente.id})",
                ) from exc
        raise


@router.get("", response_model=list[WorkflowOut])
async def listar_workflows(
    session: Annotated[AsyncSession, Depends(get_db)], status: str | None = None
):
    repo = WorkflowRepository(session)
    return await repo.listar(status=status)


@router.get("/{workflow_id}", response_model=WorkflowDetailOut)
async def obter_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    return wf


@router.patch("/{workflow_id}/retry", response_model=WorkflowOut)
async def repetir_workflow(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.status != "erro":
        raise HTTPException(400, "Só é possível repetir workflows com status 'erro'")
    if wf.tentativas >= wf.max_tentativas:
        raise HTTPException(400, f"Limite de tentativas atingido ({wf.max_tentativas})")

    await repo.incrementar_tentativa(wf.id)
    gerar_audio_workflow.delay(str(wf.id))
    return await repo.obter_por_id(wf.id)


@router.patch("/{workflow_id}/cancelar", response_model=WorkflowOut)
async def cancelar_workflow(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    from app.core.celery_app import celery_app

    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")

    # Revogar task Celery se ainda estiver pendente
    if wf.status == "agendada":
        # Revoga todas as tasks pendentes para este workflow
        celery_app.control.revoke(
            f"app.tasks.audio_tasks.gerar_audio_workflow-{workflow_id}", terminate=True
        )

    await repo.atualizar_status(wf.id, "cancelada")
    return await repo.obter_por_id(wf.id)


@router.get("/{workflow_id}/audio")
async def baixar_audio_final(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None or not wf.audio_final_path:
        raise HTTPException(404, "Áudio final ainda não disponível para este workflow")
    return FileResponse(wf.audio_final_path, media_type="audio/wav", filename=f"{workflow_id}.wav")


@router.get("/{workflow_id}/transcricao", response_model=TranscricaoOut)
async def obter_transcricao(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Retorna a transcrição do áudio final do workflow com timestamps por palavra."""
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")

    transcricao = await repo.obter_transcricao(workflow_id)
    if transcricao is None:
        raise HTTPException(404, "Transcrição ainda não disponível para este workflow")

    return transcricao


@router.get("/{workflow_id}/narration-timeline", response_model=NarrationTimelineOut)
async def obter_narration_timeline(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Return the alignment compatible with the current script/audio/transcription identity."""
    repo = WorkflowRepository(session)
    workflow = await repo.obter_por_id(workflow_id)
    if workflow is None:
        raise HTTPException(404, "Workflow não encontrado")
    alignment = await NarrationAlignmentService(session).current(workflow_id)
    if alignment is None:
        raise HTTPException(404, "Narration Alignment compatível ainda não disponível")
    return _timeline_response(workflow, alignment)


@router.post(
    "/{workflow_id}/narration-alignment",
    response_model=NarrationAlignmentEnsureOut,
)
async def reconstruir_narration_alignment(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
):
    """Build only derived alignment records; never invokes voice, mastering or ASR."""
    service = NarrationAlignmentService(session)
    try:
        alignment, created = await service.ensure(workflow_id, force=force)
    except NarrationAlignmentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    workflow = await WorkflowRepository(session).obter_por_id(workflow_id)
    if workflow is None:  # defensive after alignment transaction
        raise HTTPException(404, "Workflow não encontrado")
    return NarrationAlignmentEnsureOut(
        created=created,
        timeline=_timeline_response(workflow, alignment),
    )


@router.get("/{workflow_id}/blocos/{bloco_id}/audio")
async def baixar_audio_bloco(
    workflow_id: uuid.UUID, bloco_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Retorna o áudio de um bloco específico."""
    repo = WorkflowRepository(session)
    bloco = await repo.obter_bloco_por_id(bloco_id)

    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    if not bloco.audio_path:
        raise HTTPException(404, "Áudio do bloco ainda não disponível")

    return FileResponse(
        bloco.audio_path, media_type="audio/wav", filename=f"bloco_{bloco.ordem:02d}.wav"
    )


@router.get("/{workflow_id}/pacote-producao", response_model=PacoteProducaoOut)
async def obter_pacote_producao(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """
    Retorna o pacote consolidado de produção visual.

    Agrega todos os dados necessários para o serviço visual iniciar a produção:
    - Roteiro completo com blocos
    - Áudio final e por bloco
    - Transcrição com timestamps por palavra
    - Perfil de produção
    - Metadata de controle

    O serviço visual deve consumir este endpoint em vez de bater em múltiplos.
    """
    from app.schemas.workflow import (
        AudioPacoteOut,
        BlocoAudioPacoteOut,
        BlocoRoteiroPacote,
        MetadataPacoteOut,
        PacoteProducaoOut,
        PalavraTimestampPacote,
        RoteiroPacoteOut,
        TranscricaoPacoteOut,
        VisualQueryPacote,
    )

    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)

    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")

    if wf.status != "audio_concluido":
        raise HTTPException(400, f"Workflow ainda não concluído (status: {wf.status})")

    # Montar seção do roteiro
    blocos_roteiro = [
        BlocoRoteiroPacote(
            ordem=b.get("ordem", i),
            tipo=b.get("tipo", f"bloco_{i}"),
            conteudo=b.get("conteudo", ""),
            visual_queries=[
                VisualQueryPacote.model_validate(item) for item in b.get("visual_queries", [])
            ],
        )
        for i, b in enumerate(wf.roteiro_snapshot.get("blocos", []))
    ]

    roteiro = RoteiroPacoteOut(
        titulo=wf.roteiro_snapshot.get("titulo", ""),
        duracao_total_segundos=wf.roteiro_snapshot.get("duracao_total_segundos", 0),
        blocos=blocos_roteiro,
    )

    # Montar seção de áudio
    blocos_audio = [
        BlocoAudioPacoteOut(
            id=bloco.id,
            ordem=bloco.ordem,
            tipo=bloco.tipo,
            audio_url=f"/workflow/{workflow_id}/blocos/{bloco.id}/audio"
            if bloco.audio_path
            else None,
            duracao_segundos=bloco.duracao_segundos,
            sha256=bloco.audio_sha256,
            sample_rate=bloco.audio_sample_rate,
            channels=bloco.audio_channels,
            byte_size=bloco.audio_byte_size,
            narration_start_seconds=bloco.narration_start_seconds,
            narration_end_seconds=bloco.narration_end_seconds,
            voice_profile=bloco.voice_profile,
            generation_profile=bloco.generation_profile,
            resolved_seed=bloco.resolved_seed,
            generation_attempt=bloco.generation_attempt,
        )
        for bloco in (wf.blocos or [])
    ]

    audio = AudioPacoteOut(
        url=f"/workflow/{workflow_id}/audio",
        duracao_segundos=wf.duracao_audio_segundos,
        sha256=wf.audio_final_sha256,
        sample_rate=wf.audio_final_sample_rate,
        channels=wf.audio_final_channels,
        byte_size=wf.audio_final_byte_size,
        mastering_config=wf.mastering_config,
        mastering_metrics=wf.mastering_metrics,
        blocos=blocos_audio,
    )

    # Montar seção de transcrição
    transcricao_data = await repo.obter_transcricao(workflow_id)
    transcricao = None
    if transcricao_data:
        transcricao = TranscricaoPacoteOut(
            texto_completo=transcricao_data.texto_completo,
            palavras_timestamps=[
                PalavraTimestampPacote(
                    palavra=p.get("palavra", ""),
                    inicio=p.get("inicio", 0.0),
                    fim=p.get("fim", 0.0),
                )
                for p in (transcricao_data.palavras_timestamps or [])
            ],
            audio_sha256=transcricao_data.audio_sha256,
            provider=transcricao_data.provider,
            model=transcricao_data.model,
            language=transcricao_data.language,
        )

    alignment_data = await NarrationAlignmentService(session).current(workflow_id)
    narration_alignment = (
        _timeline_response(wf, alignment_data) if alignment_data is not None else None
    )

    # Montar metadata
    metadata = MetadataPacoteOut(
        criado_em=wf.criado_em,
        atualizado_em=wf.atualizado_em,
        tentativas=wf.tentativas,
    )

    return PacoteProducaoOut(
        workflow_id=wf.id,
        ideia_id=wf.ideia_id,
        roteiro_id=wf.roteiro_id,
        status=wf.status,
        duracao_audio_segundos=wf.duracao_audio_segundos,
        perfil_producao=wf.perfil_producao,
        roteiro=roteiro,
        audio=audio,
        transcricao=transcricao,
        narration_alignment=narration_alignment,
        metadata=metadata,
    )
