"""
Endpoints de workflow: listar roteiros aprovados disponíveis no ideas-hub,
enfileirar geração de áudio, acompanhar progresso e baixar o resultado.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ideas_hub_client import ideas_hub_client
from app.clients.video_composer_client import (
    VideoComposerClient,
    VideoComposerConflictError,
    VideoComposerError,
    VideoComposerNotFoundError,
)
from app.core.database import get_db
from app.models.workflow import (
    NarrationAlignment,
    ProductionQAReport,
    ProductionTimeline,
    VisualPlan,
    WorkflowExecucao,
    WorkflowRender,
)
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.transcricao import TranscricaoOut
from app.schemas.workflow import (
    NarrationAlignmentEnsureOut,
    NarrationBlockAlignmentOut,
    NarrationTimelineAudioOut,
    NarrationTimelineOut,
    NarrationTrackOut,
    PacoteProducaoOut,
    PrimaryVisualTrackOut,
    ProductionQACheckOut,
    ProductionQAEnsureOut,
    ProductionQAReportOut,
    ProductionTimelineEnsureOut,
    ProductionTimelineOut,
    ProductionTimelineTracksOut,
    RoteiroDisponivelOut,
    TimelineGapOut,
    TimelinePlacementOut,
    TimelineQueryOutcomeOut,
    TimelineSourceOut,
    VisualDecisionOut,
    VisualPlanEnsureOut,
    VisualPlanOut,
    VisualQueryNarrationBindingOut,
    VisualSourceSelectionOut,
    WorkflowCreateIn,
    WorkflowDetailOut,
    WorkflowOut,
    WorkflowRenderErrorOut,
    WorkflowRenderOut,
    WorkflowRenderOutputOut,
)
from app.services.narration_alignment_service import (
    NarrationAlignmentNotFoundError,
    NarrationAlignmentService,
)
from app.services.production_orchestration_service import ProductionOrchestrationService
from app.services.production_qa_service import (
    ProductionQANotFoundError,
    ProductionQAPreconditionError,
    ProductionQAService,
)
from app.services.production_timeline_service import (
    ProductionTimelineNotFoundError,
    ProductionTimelineService,
)
from app.services.visual_director_service import (
    VisualDirectorInputError,
    VisualDirectorNotFoundError,
    VisualDirectorService,
)
from app.tasks.audio_tasks import gerar_audio_workflow
from app.tasks.production_tasks import processar_producao_workflow

router = APIRouter()


def _render_response(render: WorkflowRender) -> WorkflowRenderOut:
    output = None
    if render.status == "completed" and render.output_sha256:
        output = WorkflowRenderOutputOut(
            sha256=render.output_sha256,
            byte_size=render.output_byte_size or 0,
            duration_seconds=render.output_duration_seconds or 0,
            width=render.output_width or 0,
            height=render.output_height or 0,
            fps=render.output_fps or 0,
            frame_count=render.output_frame_count,
            video_codec=render.output_video_codec or "",
            audio_codec=render.output_audio_codec or "",
            audio_sample_rate=render.output_audio_sample_rate or 0,
        )
    error = (
        WorkflowRenderErrorOut(
            category=render.error_category or "render_failed",
            message=render.error_message or "Render failed",
        )
        if render.status == "failed"
        else None
    )
    return WorkflowRenderOut(
        id=render.id,
        workflow_id=render.workflow_execucao_id,
        production_timeline_id=render.production_timeline_id,
        production_timeline_fingerprint=render.production_timeline_fingerprint,
        composer_render_id=render.composer_render_id,
        render_profile=render.render_profile,
        mode=render.mode,
        status=render.status,
        phase=render.phase,
        progress=render.progress,
        frames_rendered=render.frames_rendered,
        total_frames=render.total_frames,
        render_profile_identity=render.render_profile_identity,
        render_fingerprint=render.render_fingerprint,
        manifest_fingerprint=render.manifest_fingerprint,
        output=output,
        metrics=render.metrics,
        error=error,
        started_at=render.started_at,
        completed_at=render.completed_at,
        created_at=render.created_at,
        updated_at=render.updated_at,
        video_url=f"/workflow/{render.workflow_execucao_id}/video" if output is not None else None,
    )


def _qa_response(report: ProductionQAReport) -> ProductionQAReportOut:
    return ProductionQAReportOut(
        id=report.id,
        workflow_id=report.workflow_execucao_id,
        workflow_render_id=report.workflow_render_id,
        production_timeline_id=report.production_timeline_id,
        visual_plan_id=report.visual_plan_id,
        narration_alignment_id=report.narration_alignment_id,
        final_audio_sha256=report.final_audio_sha256,
        render_output_sha256=report.render_output_sha256,
        qa_mode=report.qa_mode,
        policy_name=report.policy_name,
        policy_version=report.policy_version,
        policy_identity=report.policy_identity,
        strategy_version=report.strategy_version,
        request_fingerprint=report.request_fingerprint,
        status=report.status,
        publication_ready=report.publication_ready,
        check_count=report.check_count,
        passed_count=report.passed_count,
        warning_count=report.warning_count,
        failed_count=report.failed_count,
        blocked_count=report.blocked_count,
        checks=[ProductionQACheckOut.model_validate(item) for item in report.checks],
        metrics=report.metrics,
        error_category=report.error_category,
        error_message=report.error_message,
        created_at=report.created_at,
        started_at=report.started_at,
        completed_at=report.completed_at,
        updated_at=report.updated_at,
    )


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


def _visual_plan_response(plan: VisualPlan) -> VisualPlanOut:
    return VisualPlanOut(
        id=plan.id,
        workflow_id=plan.workflow_execucao_id,
        narration_alignment_id=plan.narration_alignment_id,
        strategy_version=plan.strategy_version,
        request_fingerprint=plan.request_fingerprint,
        fingerprint=plan.fingerprint,
        status=plan.status,
        media_scope=plan.media_scope,
        media_index_provenance=plan.media_index_provenance,
        total_queries=plan.total_queries,
        resolved_queries=plan.resolved_queries,
        unresolved_queries=plan.unresolved_queries,
        failed_queries=plan.failed_queries,
        required_gaps=plan.required_gaps,
        optional_gaps=plan.optional_gaps,
        direct_provenance_count=plan.direct_provenance_count,
        media_search_count=plan.media_search_count,
        reused_source_count=plan.reused_source_count,
        metrics=plan.metrics,
        diagnostics=plan.diagnostics,
        error_message=plan.error_message,
        started_at=plan.started_at,
        completed_at=plan.completed_at,
        created_at=plan.criado_em,
        decisions=[
            VisualDecisionOut(
                id=item.id,
                visual_query_id=item.visual_query_id,
                audio_block_id=item.audio_bloco_id,
                block_order=item.block_order,
                query_order=item.query_order,
                query=item.query,
                intent=item.intent,
                importance=item.importance,
                source_note_id=item.source_note_id,
                desired_duration_seconds=item.desired_duration_seconds,
                narration_binding_status=item.narration_binding_status,
                narration_start_seconds=item.narration_start_seconds,
                narration_end_seconds=item.narration_end_seconds,
                target_narration_duration_seconds=item.target_narration_duration_seconds,
                source_resolution_status=item.source_resolution_status,
                selection_strategy=item.selection_strategy,
                selected_source=VisualSourceSelectionOut.model_validate(item.selected_source)
                if item.selected_source
                else None,
                alternatives=[
                    VisualSourceSelectionOut.model_validate(candidate)
                    for candidate in item.alternatives
                ],
                selection_score=item.selection_score,
                score_components=item.score_components,
                reason=item.reason,
                diagnostics=item.diagnostics,
            )
            for item in plan.decisions
        ],
    )


def _production_timeline_response(timeline: ProductionTimeline) -> ProductionTimelineOut:
    narration = None
    if timeline.final_audio_sha256 and timeline.duration_seconds > 0:
        narration = NarrationTrackOut(
            timeline_start_seconds=0.0,
            timeline_end_seconds=timeline.duration_seconds,
            source_start_seconds=0.0,
            source_end_seconds=timeline.duration_seconds,
            audio_url=f"/workflow/{timeline.workflow_execucao_id}/audio",
            audio_sha256=timeline.final_audio_sha256,
            sample_rate=timeline.narration_sample_rate,
            channels=timeline.narration_channels,
            byte_size=timeline.narration_byte_size,
        )
    outcomes = (
        timeline.diagnostics.get("query_placement_outcomes", []) if timeline.diagnostics else []
    )
    return ProductionTimelineOut(
        id=timeline.id,
        workflow_id=timeline.workflow_execucao_id,
        narration_alignment_id=timeline.narration_alignment_id,
        visual_plan_id=timeline.visual_plan_id,
        schema_version=timeline.schema_version,
        strategy_version=timeline.strategy_version,
        validator_version=timeline.validator_version,
        request_fingerprint=timeline.request_fingerprint,
        fingerprint=timeline.fingerprint,
        status=timeline.status,
        duration_seconds=timeline.duration_seconds,
        policy=timeline.policy,
        tracks=ProductionTimelineTracksOut(
            narration=narration,
            primary_visual=PrimaryVisualTrackOut(
                placements=[
                    TimelinePlacementOut(
                        id=item.id,
                        order=item.order,
                        visual_decision_id=item.visual_decision_id,
                        visual_decision_ids=[
                            uuid.UUID(value) for value in item.visual_decision_ids
                        ],
                        visual_query_ids=[uuid.UUID(value) for value in item.visual_query_ids],
                        timeline_start_seconds=item.timeline_start_seconds,
                        timeline_end_seconds=item.timeline_end_seconds,
                        source=TimelineSourceOut(
                            project_id=item.source_project_id,
                            session_id=item.source_session_id,
                            media_id=item.source_media_id,
                            candidate_start_seconds=item.candidate_start_seconds,
                            candidate_end_seconds=item.candidate_end_seconds,
                            source_start_seconds=item.source_start_seconds,
                            source_end_seconds=item.source_end_seconds,
                            provenance=item.source_provenance,
                        ),
                        playback_rate=item.playback_rate,
                        transition=item.transition,
                        strategy=item.strategy,
                        importance=item.importance,
                        diagnostics=item.diagnostics,
                    )
                    for item in timeline.placements
                ]
            ),
        ),
        gaps=[
            TimelineGapOut(
                id=item.id,
                order=item.order,
                timeline_start_seconds=item.timeline_start_seconds,
                timeline_end_seconds=item.timeline_end_seconds,
                reason=item.reason,
                severity=item.severity,
                related_visual_query_ids=[
                    uuid.UUID(value) for value in item.related_visual_query_ids
                ],
                diagnostics=item.diagnostics,
            )
            for item in timeline.gaps
        ],
        query_placement_outcomes=[
            TimelineQueryOutcomeOut.model_validate(item) for item in outcomes
        ],
        visual_coverage_seconds=timeline.visual_coverage_seconds,
        visual_gap_seconds=timeline.visual_gap_seconds,
        visual_coverage_ratio=timeline.visual_coverage_ratio,
        placement_count=timeline.placement_count,
        gap_count=timeline.gap_count,
        cut_count=timeline.cut_count,
        unplaced_query_count=timeline.unplaced_query_count,
        required_gap_seconds=timeline.required_gap_seconds,
        preferred_gap_seconds=timeline.preferred_gap_seconds,
        optional_gap_seconds=timeline.optional_gap_seconds,
        background_gap_seconds=timeline.background_gap_seconds,
        average_shot_duration_seconds=timeline.average_shot_duration_seconds,
        metrics=timeline.metrics,
        diagnostics=timeline.diagnostics,
        error_message=timeline.error_message,
        started_at=timeline.started_at,
        completed_at=timeline.completed_at,
        created_at=timeline.criado_em,
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
        processar_producao_workflow.delay(str(wf.id))
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
    if wf.production_status == "failed":
        if wf.production_phase == "audio":
            if wf.tentativas >= wf.max_tentativas:
                raise HTTPException(400, f"Limite de tentativas atingido ({wf.max_tentativas})")
            await repo.incrementar_tentativa(wf.id)
            wf = await repo.obter_por_id(wf.id)
            assert wf is not None
        await ProductionOrchestrationService(session).explicit_retry(wf)
        processar_producao_workflow.delay(str(wf.id))
    elif wf.production_status is None and wf.status == "erro":
        if wf.tentativas >= wf.max_tentativas:
            raise HTTPException(400, f"Limite de tentativas atingido ({wf.max_tentativas})")
        await repo.incrementar_tentativa(wf.id)
        gerar_audio_workflow.delay(str(wf.id))
    else:
        raise HTTPException(400, "Só é possível repetir workflows com falha")
    return await repo.obter_por_id(wf.id)


@router.post("/{workflow_id}/resume-production", response_model=WorkflowOut)
async def retomar_producao(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.production_status not in {None, "blocked"}:
        raise HTTPException(409, "A produção não está bloqueada nem em modo legado")
    await ProductionOrchestrationService(session).resume(wf)
    processar_producao_workflow.delay(str(wf.id))
    resumed = await repo.obter_por_id(wf.id)
    assert resumed is not None
    return resumed


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
    if wf.production_status is not None:
        await repo.atualizar_producao(
            wf.id,
            status="cancelled",
            phase=wf.production_phase or "audio",
            error_category=None,
            error_message=None,
            complete=True,
        )
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


@router.get("/{workflow_id}/visual-plan", response_model=VisualPlanOut)
async def obter_visual_plan(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Return the compatible persisted plan without triggering Media Search."""
    try:
        plan = await VisualDirectorService(session).current(workflow_id)
    except VisualDirectorNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if plan is None:
        raise HTTPException(404, "Visual Plan compatível ainda não disponível")
    return _visual_plan_response(plan)


@router.post("/{workflow_id}/visual-plan", response_model=VisualPlanEnsureOut)
async def construir_visual_plan(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
):
    """Resolve source windows only; never invokes voice, ASR or media preprocessing."""
    try:
        plan, created = await VisualDirectorService(session).ensure(workflow_id, force=force)
    except VisualDirectorNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except VisualDirectorInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    return VisualPlanEnsureOut(created=created, visual_plan=_visual_plan_response(plan))


@router.get("/{workflow_id}/production-timeline", response_model=ProductionTimelineOut)
async def obter_production_timeline(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Return the current compatible edit plan without rebuilding upstream artifacts."""
    try:
        timeline = await ProductionTimelineService(session).current(workflow_id)
    except ProductionTimelineNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if timeline is None:
        raise HTTPException(404, "Production Timeline compatível ainda não disponível")
    return _production_timeline_response(timeline)


@router.post("/{workflow_id}/production-timeline", response_model=ProductionTimelineEnsureOut)
async def construir_production_timeline(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
):
    """Plan metadata only; never calls voice, ASR, Media Search or a renderer."""
    try:
        timeline, created = await ProductionTimelineService(session).ensure(
            workflow_id, force=force
        )
    except ProductionTimelineNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return ProductionTimelineEnsureOut(
        created=created,
        production_timeline=_production_timeline_response(timeline),
    )


@router.get("/{workflow_id}/render", response_model=WorkflowRenderOut)
async def obter_render(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    workflow = await WorkflowRepository(session).obter_por_id(workflow_id)
    if workflow is None:
        raise HTTPException(404, "Workflow não encontrado")
    render = await ProductionOrchestrationService(session).current_render(workflow_id)
    if render is None:
        raise HTTPException(404, "Render compatível ainda não disponível")
    return _render_response(render)


@router.get("/{workflow_id}/qa-report", response_model=ProductionQAReportOut)
async def obter_qa_report(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    mode: Literal["standard", "deep"] = "standard",
):
    """Read the exact current QA projection; never executes checks or production stages."""
    try:
        report = await ProductionQAService(session).current(workflow_id, mode)
    except ProductionQANotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionQAPreconditionError as exc:
        raise HTTPException(404, "Relatório QA compatível ainda não disponível") from exc
    if report is None:
        raise HTTPException(404, "Relatório QA compatível ainda não disponível")
    return _qa_response(report)


@router.post("/{workflow_id}/qa-report", response_model=ProductionQAEnsureOut)
async def executar_qa_report(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    mode: Literal["standard", "deep"] = "standard",
    force: bool = False,
):
    """Execute/reuse QA only; upstream production artifacts remain immutable."""
    try:
        result = await ProductionQAService(session).ensure(workflow_id, mode=mode, force=force)
    except ProductionQANotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductionQAPreconditionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return ProductionQAEnsureOut(created=result.created, report=_qa_response(result.report))


@router.get("/{workflow_id}/video")
async def baixar_video_final(
    workflow_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    workflow = await WorkflowRepository(session).obter_por_id(workflow_id)
    if workflow is None:
        raise HTTPException(404, "Workflow não encontrado")
    render = await ProductionOrchestrationService(session).current_render(workflow_id)
    if render is None or render.status != "completed" or render.composer_render_id is None:
        raise HTTPException(404, "Vídeo final ainda não disponível")

    composer = VideoComposerClient()
    await composer.__aenter__()
    try:
        remote = await composer.open_video(
            str(render.composer_render_id), range_header=request.headers.get("range")
        )
    except VideoComposerNotFoundError as exc:
        await composer.aclose()
        raise HTTPException(404, "Vídeo final não encontrado no Composer") from exc
    except VideoComposerConflictError as exc:
        await composer.aclose()
        raise HTTPException(409, "Render remoto ainda não está concluído") from exc
    except VideoComposerError as exc:
        await composer.aclose()
        raise HTTPException(502, "Video Composer indisponível") from exc

    async def stream():
        try:
            async for chunk in remote.aiter_bytes():
                yield chunk
        finally:
            await remote.aclose()
            await composer.aclose()

    safe_headers = {
        name: value
        for name, value in remote.headers.items()
        if name.lower()
        in {
            "accept-ranges",
            "content-length",
            "content-range",
            "content-disposition",
        }
    }
    return StreamingResponse(
        stream(),
        status_code=remote.status_code,
        media_type=remote.headers.get("content-type", "video/mp4"),
        headers=safe_headers,
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
        MediaScopeOut,
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
        media_context=MediaScopeOut.model_validate(wf.roteiro_snapshot["media_context"])
        if wf.roteiro_snapshot.get("media_context")
        else None,
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
    visual_plan_data = await VisualDirectorService(session).current(workflow_id)
    visual_plan = _visual_plan_response(visual_plan_data) if visual_plan_data else None
    production_timeline_data = await ProductionTimelineService(session).current(workflow_id)
    production_timeline = (
        _production_timeline_response(production_timeline_data)
        if production_timeline_data
        else None
    )
    render_data = await ProductionOrchestrationService(session).current_render(workflow_id)
    render = _render_response(render_data) if render_data else None
    try:
        qa_report_data = await ProductionQAService(session).current(workflow_id, "standard")
    except ProductionQAPreconditionError:
        qa_report_data = None
    qa_report = _qa_response(qa_report_data) if qa_report_data else None

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
        visual_plan=visual_plan,
        production_timeline=production_timeline,
        render=render,
        qa_report=qa_report,
        metadata=metadata,
    )
