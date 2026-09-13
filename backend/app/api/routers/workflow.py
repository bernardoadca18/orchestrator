"""
Endpoints de workflow: listar roteiros aprovados disponíveis no ideas-hub,
enfileirar geração de áudio, acompanhar progresso e baixar o resultado.
"""

import datetime as dt
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
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
    AudioBloco,
    AudioChunk,
    AudioChunkTake,
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
    AudioActionIn,
    AudioEditApplyOut,
    AudioEditUpdateOut,
    MediaScopeOut,
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
from app.services.audio_service import derive_manual_take_seed
from app.services.audio_serving import (
    AudioArtifactExpectation,
    AudioServingIntegrityError,
    audio_file_response,
    verify_audio_artifact,
)
from app.services.block_materialization import active_block_is_current
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
from app.services.pronunciation_candidates import PronunciationCandidateService
from app.services.speaking_rate_calibration import SpeakingRateCalibrationService
from app.services.visual_director_service import (
    VisualDirectorInputError,
    VisualDirectorNotFoundError,
    VisualDirectorService,
)
from app.tasks.audio_edit_tasks import aplicar_edicoes_audio
from app.tasks.audio_tasks import gerar_audio_workflow
from app.tasks.production_tasks import processar_producao_workflow

router = APIRouter()
VISUAL_SOURCE_ADAPTER: TypeAdapter[Any] = TypeAdapter(VisualSourceSelectionOut)
TIMELINE_SOURCE_ADAPTER: TypeAdapter[Any] = TypeAdapter(TimelineSourceOut)


@router.get("/calibration/speaking-rate")
async def get_speaking_rate_calibration(
    db: Annotated[AsyncSession, Depends(get_db)],
    production_profile: str | None = None,
):
    """Expose a reproducible accepted-take calibration; never invokes TTS."""

    return await SpeakingRateCalibrationService(db).snapshot(production_profile)


@router.get("/pronunciation/candidates")
async def get_pronunciation_candidates(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 100,
):
    """Aggregate recurring real-take disagreements for manual Pronunciation Lab review."""

    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return await PronunciationCandidateService(db).aggregate(limit)


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
        media_scope=(MediaScopeOut.model_validate(plan.media_scope) if plan.media_scope else None),
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
                selected_source=VISUAL_SOURCE_ADAPTER.validate_python(item.selected_source)
                if item.selected_source
                else None,
                alternatives=[
                    VISUAL_SOURCE_ADAPTER.validate_python(candidate)
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


def _timeline_source_response(item):
    source_type = getattr(item, "source_type", None) or "gameplay"
    if source_type == "static_image":
        provenance = item.source_provenance or {}
        payload = {
            "source_type": "static_image",
            "project_id": item.source_project_id,
            "media_id": item.source_media_id,
            "image_semantic_index_id": item.source_image_semantic_index_id,
            "expected_sha256": provenance.get("expected_sha256"),
            "expected_byte_size": provenance.get("expected_byte_size"),
            "content_type": provenance.get("content_type"),
            "provenance": provenance,
        }
    else:
        payload = {
            "source_type": source_type,
            "project_id": item.source_project_id,
            "session_id": item.source_session_id,
            "media_id": item.source_media_id,
            "candidate_start_seconds": item.candidate_start_seconds,
            "candidate_end_seconds": item.candidate_end_seconds,
            "source_start_seconds": item.source_start_seconds,
            "source_end_seconds": item.source_end_seconds,
            "provenance": item.source_provenance,
        }
    return TIMELINE_SOURCE_ADAPTER.validate_python(payload)


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
                        source=_timeline_source_response(item),
                        playback_rate=item.playback_rate,
                        presentation=getattr(item, "presentation", None),
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
    from psycopg2.errors import UniqueViolation  # type: ignore[import-untyped]
    from sqlalchemy.exc import IntegrityError

    repo = WorkflowRepository(session)

    roteiro = await ideas_hub_client.obter_roteiro(payload.roteiro_id)
    if roteiro["status"] != "aprovado":
        raise HTTPException(
            400, "Roteiro precisa estar aprovado no ideas-hub para entrar na fila de produção"
        )

    readiness = await ideas_hub_client.obter_production_readiness(payload.roteiro_id)
    if readiness["status"] != "ready":
        raise HTTPException(
            409,
            detail={
                "category": "production_script_not_ready",
                "message": (
                    "Roteiro aprovado não satisfaz os requisitos do ProductionProfile. "
                    "Corrija os issues e aprove a revisão antes da produção."
                ),
                "production_readiness": readiness,
            },
        )
    perfil_producao = readiness["profile"]

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


def _action_key(value: str | None) -> str:
    if value is None or not value.strip() or len(value) > 200:
        raise HTTPException(400, "Idempotency-Key é obrigatório para ações de narração")
    return value.strip()


@router.post("/{workflow_id}/audio/blocks/{ordem}/retry", response_model=WorkflowDetailOut)
async def retry_audio_block(
    workflow_id: uuid.UUID,
    ordem: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = _action_key(idempotency_key)
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.status == "cancelada" or wf.production_status == "cancelled":
        raise HTTPException(409, "Workflow cancelado não aceita novas narrações")
    block = next((item for item in wf.blocos if item.ordem == ordem), None)
    if block is None:
        raise HTTPException(404, "Bloco de áudio não encontrado")
    failed_requested = next(
        (
            take
            for take in block.takes
            if take.id == block.requested_take_id and take.status == "failed"
        ),
        None,
    )
    if block.status != "erro" and failed_requested is None:
        raise HTTPException(409, "Retry independente só é permitido para bloco com erro")
    attempt_index = len(block.takes)
    seed = derive_manual_take_seed(
        block.id, block.synthesis_identity or "", attempt_index, "manual_retry"
    )
    try:
        _, _, created = await repo.solicitar_take(
            workflow_id=workflow_id,
            ordem=ordem,
            kind="manual_retry",
            request_key=key,
            idempotency_key=f"{key}:voice",
            requested_seed=seed,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if created:
        processar_producao_workflow.delay(str(workflow_id))
    result = await repo.obter_por_id(workflow_id, com_blocos=True)
    assert result is not None
    return result


@router.post("/{workflow_id}/audio/blocks/{ordem}/regenerate", response_model=WorkflowDetailOut)
async def regenerate_audio_block(
    workflow_id: uuid.UUID,
    ordem: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = _action_key(idempotency_key)
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.status == "cancelada" or wf.production_status == "cancelled":
        raise HTTPException(409, "Workflow cancelado não aceita novas narrações")
    block = next((item for item in wf.blocos if item.ordem == ordem), None)
    if block is None:
        raise HTTPException(404, "Bloco de áudio não encontrado")
    seed = derive_manual_take_seed(
        block.id, block.synthesis_identity or "", len(block.takes), "manual_regeneration"
    )
    try:
        _, _, created = await repo.solicitar_take(
            workflow_id=workflow_id,
            ordem=ordem,
            kind="manual_regeneration",
            request_key=key,
            idempotency_key=f"{key}:voice",
            requested_seed=seed,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if created:
        processar_producao_workflow.delay(str(workflow_id))
    result = await repo.obter_por_id(workflow_id, com_blocos=True)
    assert result is not None
    return result


@router.post("/{workflow_id}/audio/regenerate", response_model=WorkflowDetailOut)
async def regenerate_full_audio(
    workflow_id: uuid.UUID,
    payload: AudioActionIn,
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not payload.confirm:
        raise HTTPException(400, "Regeneração completa exige confirm=true")
    key = _action_key(idempotency_key)
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.status == "cancelada" or wf.production_status == "cancelled":
        raise HTTPException(409, "Workflow cancelado não aceita novas narrações")
    if not wf.blocos:
        raise HTTPException(409, "Workflow não possui blocos narrativos")
    requested: dict[int, int | None] = {
        block.ordem: derive_manual_take_seed(
            block.id, block.synthesis_identity or "", len(block.takes), "manual_regeneration"
        )
        for block in wf.blocos
    }
    try:
        requested_takes = await repo.solicitar_takes(
            workflow_id=workflow_id,
            kind="manual_regeneration",
            request_key=key,
            idempotency_key_prefix=f"{key}:voice",
            requested_seed_by_order=requested,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if requested_takes:
        processar_producao_workflow.delay(str(workflow_id))
    result = await repo.obter_por_id(workflow_id, com_blocos=True)
    assert result is not None
    return result


@router.patch("/{workflow_id}/retry", response_model=WorkflowOut)
async def repetir_workflow(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.production_status == "failed":
        if wf.production_phase == "audio":
            if ProductionOrchestrationService._audio_is_reusable(wf):
                # A Stage 17 false/stale audio error must not demote a valid final
                # narration to the legacy `agendada` state or spend GPU time again.
                # Queue normal orchestration; it will verify/reuse audio and resume
                # at narration alignment.
                await ProductionOrchestrationService(session).explicit_retry(wf)
                processar_producao_workflow.delay(str(wf.id))
                resumed = await repo.obter_por_id(wf.id)
                assert resumed is not None
                return resumed
            if wf.tentativas >= wf.max_tentativas:
                raise HTTPException(400, f"Limite de tentativas atingido ({wf.max_tentativas})")
            await repo.incrementar_tentativa(wf.id)
            wf = await repo.obter_por_id(wf.id, com_blocos=True)
            assert wf is not None
            failed_blocks = [block for block in wf.blocos if block.status == "erro"]
            if failed_blocks:
                for block in failed_blocks:
                    key = f"workflow-retry:{wf.id}:{wf.tentativas}:{block.ordem}"
                    seed = derive_manual_take_seed(
                        block.id, block.synthesis_identity or "", len(block.takes), "manual_retry"
                    )
                    await repo.solicitar_take(
                        workflow_id=wf.id,
                        ordem=block.ordem,
                        kind="manual_retry",
                        request_key=key,
                        idempotency_key=f"{key}:voice",
                        requested_seed=seed,
                    )
                processar_producao_workflow.delay(str(wf.id))
                result = await repo.obter_por_id(wf.id)
                assert result is not None
                return result
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
    from sqlalchemy.exc import IntegrityError

    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id_para_atualizacao(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.production_status not in {None, "blocked", "cancelled"}:
        raise HTTPException(409, "A produção não está bloqueada, cancelada nem em modo legado")
    roteiro_id = wf.roteiro_id
    try:
        await ProductionOrchestrationService(session).resume(wf)
    except IntegrityError as exc:
        await session.rollback()
        active = await repo.obter_ativo_por_roteiro(roteiro_id)
        detail = (
            f"Outro workflow já está ativo para este roteiro (id={active.id})"
            if active is not None
            else "A retomada conflitou com outra transição de produção"
        )
        raise HTTPException(409, detail) from exc
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


@router.api_route("/{workflow_id}/audio", methods=["GET", "HEAD"])
async def baixar_audio_final(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None or not wf.audio_final_path or wf.status != "audio_concluido":
        raise HTTPException(404, "Áudio final ainda não disponível para este workflow")
    if wf.audio_edit_revision != wf.audio_materialized_revision:
        raise HTTPException(409, "Áudio final está stale em relação às edições físicas")
    try:
        if not all([await active_block_is_current(session, block) for block in wf.blocos]):
            raise HTTPException(
                409, "Áudio final referencia bloco sem proveniência de assembly atual"
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(409, "Proveniência do áudio final falhou") from exc
    try:
        path, metadata = verify_audio_artifact(
            wf.audio_final_path,
            AudioArtifactExpectation(
                sha256=wf.audio_final_sha256,
                byte_size=wf.audio_final_byte_size,
                duration_seconds=wf.duracao_audio_segundos,
                sample_rate=wf.audio_final_sample_rate,
                channels=wf.audio_final_channels,
                frame_count=getattr(wf, "audio_final_frame_count", None),
                subtype=getattr(wf, "audio_final_subtype", None),
            ),
        )
    except AudioServingIntegrityError as exc:
        raise HTTPException(409, "Áudio final ativo falhou na verificação de integridade") from exc
    return audio_file_response(path, metadata, filename=f"{workflow_id}.wav")


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
    if not wf.audio_final_sha256 or transcricao.audio_sha256 != wf.audio_final_sha256:
        raise HTTPException(409, "Transcrição pertence a outra versão da narração final")

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


@router.api_route("/{workflow_id}/blocos/{bloco_id}/audio", methods=["GET", "HEAD"])
async def baixar_audio_bloco(
    workflow_id: uuid.UUID, bloco_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Retorna o áudio de um bloco específico."""
    repo = WorkflowRepository(session)
    bloco = await repo.obter_bloco_por_id(bloco_id)

    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    if not bloco.audio_path or bloco.status != "concluido":
        raise HTTPException(404, "Áudio do bloco ainda não disponível")
    if bloco.current_take_id is not None:
        take = await repo.obter_take(bloco.current_take_id)
        if (
            take is None
            or take.status != "completed"
            or not take.is_current
            or take.audio_path != bloco.audio_path
            or take.audio_duration_seconds != bloco.duracao_segundos
            or take.audio_sha256 != bloco.audio_sha256
            or take.audio_byte_size != bloco.audio_byte_size
            or (
                take.audio_sample_rate is not None
                and take.audio_sample_rate != bloco.audio_sample_rate
            )
            or (take.audio_channels is not None and take.audio_channels != bloco.audio_channels)
            or (
                take.audio_frame_count is not None
                and take.audio_frame_count != bloco.audio_frame_count
            )
            or (take.audio_subtype is not None and take.audio_subtype != bloco.audio_subtype)
        ):
            raise HTTPException(409, "Ponteiro do take ativo está inconsistente")
    try:
        if not await active_block_is_current(session, bloco):
            raise HTTPException(
                409,
                "Áudio do bloco está stale em relação aos physical chunks ativos",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(409, "Proveniência do assembly do bloco falhou na verificação") from exc
    try:
        path, metadata = verify_audio_artifact(
            bloco.audio_path,
            AudioArtifactExpectation(
                sha256=bloco.audio_sha256,
                byte_size=bloco.audio_byte_size,
                duration_seconds=bloco.duracao_segundos,
                sample_rate=bloco.audio_sample_rate,
                channels=bloco.audio_channels,
                frame_count=getattr(bloco, "audio_frame_count", None),
                subtype=getattr(bloco, "audio_subtype", None),
            ),
        )
    except AudioServingIntegrityError as exc:
        raise HTTPException(
            409, "Áudio do take ativo falhou na verificação de integridade"
        ) from exc
    return audio_file_response(path, metadata, filename=f"bloco_{bloco.ordem:02d}.wav")


@router.get("/{workflow_id}/blocos/{bloco_id}/chunks")
async def listar_chunks_bloco(
    workflow_id: uuid.UUID,
    bloco_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """List all chunks for a block with their takes."""
    repo = WorkflowRepository(session)
    bloco = await repo.obter_bloco_por_id(bloco_id)
    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    chunks = await repo.listar_chunks(bloco_id)
    return [
        {
            "id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
            "chunk_id": chunk.chunk_id,
            "canonical_text": chunk.canonical_text,
            "synthesis_text_snapshot": chunk.synthesis_text_snapshot,
            "seed": chunk.seed,
            "duration_seconds": chunk.duration_seconds,
            "sample_rate": chunk.sample_rate,
            "status": chunk.status,
            "boundary": chunk.boundary,
            "pause_after_frames": chunk.pause_after_frames,
            "current_take_id": str(chunk.current_take_id) if chunk.current_take_id else None,
            "takes": [
                {
                    "id": str(take.id),
                    "take_number": take.take_number,
                    "kind": take.kind,
                    "status": (
                        "active"
                        if take.id == chunk.current_take_id
                        else "candidate"
                        if take.kind == "regeneration" and take.status == "completed"
                        else take.status
                    ),
                    "seed": take.seed,
                    "audio_url": f"/workflow/{workflow_id}/chunks/{take.id}/audio"
                    if take.audio_path and take.status in {"completed", "superseded", "rejected"}
                    else None,
                    "audio_sha256": take.audio_sha256,
                    "audio_duration_seconds": take.audio_duration_seconds,
                    "audio_frame_count": take.audio_frame_count,
                    "audio_sample_rate": take.audio_sample_rate,
                    "audio_channels": take.audio_channels,
                    "audio_subtype": take.audio_subtype,
                    "source_job_id": take.source_job_id,
                    "provider_metadata": take.provider_metadata,
                    "speaker_similarity": take.speaker_similarity,
                    "speaker_similarity_reference_identity": (
                        take.speaker_similarity_reference_identity
                    ),
                    "speaker_similarity_policy_version": (take.speaker_similarity_policy_version),
                    "speaker_identity_status": take.speaker_identity_status,
                    "accent_quality": take.accent_quality,
                    "quality_rejection_reason": take.quality_rejection_reason,
                    "created_at": take.created_at.isoformat() if take.created_at else None,
                    "completed_at": take.completed_at.isoformat() if take.completed_at else None,
                    "error_message": take.error_message,
                }
                for take in chunk.takes
            ],
        }
        for chunk in chunks
    ]


@router.post("/{workflow_id}/blocos/{bloco_id}/chunks/{chunk_index}/regenerate")
async def regenerar_chunk(
    workflow_id: uuid.UUID,
    bloco_id: uuid.UUID,
    chunk_index: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Regenerate a single chunk with a new seed.

    Creates a candidate take that must be explicitly promoted.
    Does NOT modify the active audio.
    """
    from app.clients.voice_client import VoiceClient
    from app.core.config import settings
    from app.services.accent_qa import AccentDetector
    from app.services.audio_service import derive_manual_chunk_seed
    from app.services.production_audio import (
        ProductionAudioError,
        publish_immutable_source_audio,
    )

    if idempotency_key is None or not (1 <= len(idempotency_key) <= 200):
        raise HTTPException(400, "Idempotency-Key válido é obrigatório para regenerar um chunk")

    repo = WorkflowRepository(session)
    bloco = await repo.obter_bloco_por_id(bloco_id)
    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    if bloco.status != "concluido":
        raise HTTPException(400, "Bloco ainda não foi gerado")

    chunk = await repo.obter_chunk_por_indice(bloco_id, chunk_index)
    if chunk is None:
        raise HTTPException(404, "Chunk não encontrado")
    loaded_chunk = await repo.obter_chunk(chunk.id)
    assert loaded_chunk is not None
    chunk = loaded_chunk

    existing = await repo.obter_chunk_take_por_idempotency(idempotency_key)
    if existing is not None:
        if existing.audio_chunk_id != chunk.id:
            raise HTTPException(409, "Idempotency-Key já foi usado para outro chunk")
        take = existing
        new_seed = take.seed
        if take.status == "rejected" and take.quality_rejection_reason == "accent_likely_pt_pt":
            raise HTTPException(
                422,
                "Take já rejeitado pelo accent QA; o take ativo foi preservado",
            )
    else:
        new_seed = derive_manual_chunk_seed(
            block_id=bloco.id,
            chunk_id=chunk.id,
            synthesis_text_snapshot=chunk.synthesis_text_snapshot,
            idempotency_key=idempotency_key,
        )
        try:
            take = await repo.criar_chunk_take(
                chunk.id,
                new_seed,
                kind="regeneration",
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    if take.status == "rejected":
        raise HTTPException(409, "A operação idempotente já foi rejeitada")

    try:
        if take.status != "completed":
            await repo.atualizar_chunk_take(take.id, status="running", error_message=None)
            async with VoiceClient(settings) as voice:
                result = await voice.regenerate_chunk(
                    chunk.source_job_id,
                    chunk_index,
                    new_seed,
                    idempotency_key=idempotency_key,
                )
                if result.get("synthesis_text") != chunk.synthesis_text_snapshot:
                    raise ProductionAudioError(
                        "Voice Service candidate changed the frozen synthesis text"
                    )
                if result.get("seed") != new_seed:
                    raise ProductionAudioError("Voice Service candidate returned a different seed")
                if (
                    result.get("job_id") != chunk.source_job_id
                    or result.get("chunk_index") != chunk_index
                    or result.get("status") != "completed"
                ):
                    raise ProductionAudioError("Voice Service candidate identity is inconsistent")
                candidate_id = result.get("candidate_id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    raise ProductionAudioError("Voice Service candidate identity is missing")
                if (
                    take.provider_candidate_id is not None
                    and take.provider_candidate_id != candidate_id
                ):
                    raise ProductionAudioError(
                        "Voice Service changed an idempotent candidate identity"
                    )

                audio_bytes = await voice.download_chunk_candidate_audio(
                    chunk.source_job_id, candidate_id
                )

            source_dir = Path(settings.audio_storage_path) / str(workflow_id) / "blocks"
            chunks_dir = source_dir / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            chunk_path = chunks_dir / f"chunk_{chunk_index:04d}_take_{take.id}.wav"
            expectation = AudioArtifactExpectation(
                sha256=result.get("sha256"),
                byte_size=result.get("byte_size"),
                duration_seconds=result.get("duration_seconds"),
                sample_rate=result.get("sample_rate"),
                channels=None,
                frame_count=result.get("frame_count"),
            )
            if chunk_path.exists():
                _, local_metadata = verify_audio_artifact(chunk_path, expectation)
            else:
                local_metadata = publish_immutable_source_audio(
                    audio_bytes,
                    chunk_path,
                    expected_sha256=result.get("sha256"),
                    expected_byte_size=result.get("byte_size"),
                    expected_duration_seconds=result.get("duration_seconds"),
                    expected_sample_rate=result.get("sample_rate"),
                    expected_frame_count=result.get("frame_count"),
                )

            accent_quality = AccentDetector(
                getattr(
                    settings,
                    "audio_accent_qa_model_path",
                    Path("config/accent_detector_model.json"),
                ),
                getattr(settings, "audio_accent_qa_mode", "shadow"),
            ).analyze(chunk_path)
            rejected_by_accent = accent_quality.get("blocks_promotion") is True

            await repo.atualizar_chunk_take(
                take.id,
                status="rejected" if rejected_by_accent else "completed",
                audio_path=str(chunk_path),
                audio_sha256=local_metadata.sha256,
                audio_byte_size=local_metadata.byte_size,
                audio_sample_rate=local_metadata.sample_rate,
                audio_duration_seconds=local_metadata.duration_seconds,
                audio_frame_count=local_metadata.frame_count,
                audio_channels=local_metadata.channels,
                audio_subtype=local_metadata.subtype,
                source_job_id=chunk.source_job_id,
                artifact_integrity=local_metadata.__dict__,
                provider_metadata={
                    "source_job_id": chunk.source_job_id,
                    "provider_candidate_id": candidate_id,
                    "requested_language": settings.voice_language,
                    "resolved_language": result.get("language", settings.voice_language),
                    "speaker_embedding_seconds": result.get("speaker_embedding_seconds"),
                },
                speaker_similarity=result.get("speaker_similarity"),
                speaker_similarity_reference_identity=result.get(
                    "speaker_similarity_reference_identity"
                ),
                speaker_similarity_policy_version=result.get("speaker_similarity_policy_version"),
                speaker_identity_status=result.get("speaker_identity_status"),
                accent_quality=accent_quality,
                quality_rejection_reason=(
                    str(accent_quality.get("reason_code")) if rejected_by_accent else None
                ),
                provider_candidate_id=candidate_id,
                completed_at=dt.datetime.now(dt.UTC),
            )
            if rejected_by_accent:
                raise HTTPException(
                    422,
                    "Take rejeitado pelo accent QA; o take ativo foi preservado",
                )

        await session.refresh(take)
        await session.refresh(chunk)

        return {
            "take_id": str(take.id),
            "chunk_id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
            "take_number": take.take_number,
            "seed": take.seed,
            "status": "candidate",
            "audio_url": f"/workflow/{workflow_id}/chunks/{take.id}/audio",
            "duration_seconds": take.audio_duration_seconds,
            "accent_quality": take.accent_quality,
        }

    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        await repo.atualizar_chunk_take(take.id, status="failed", error_message=str(exc))
        raise HTTPException(500, f"Falha na regeneração do chunk: {exc}") from exc


@router.post("/{workflow_id}/chunk-takes/{take_id}/promote")
async def promover_chunk_take(
    workflow_id: uuid.UUID,
    take_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Promote a candidate take to active.

    Selection is transactional and durable, but materialization is deferred to
    the explicit batch Update Pipeline operation.
    """
    from app.core.config import settings

    repo = WorkflowRepository(session)

    take = await session.get(AudioChunkTake, take_id)
    if take is None:
        raise HTTPException(404, "Take não encontrado")

    chunk = await repo.obter_chunk(take.audio_chunk_id)
    if chunk is None:
        raise HTTPException(404, "Chunk não encontrado")

    bloco = await repo.obter_bloco_por_id(chunk.audio_bloco_id)
    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    if take.status != "completed":
        raise HTTPException(400, "Take não está pronto para promoção")
    if (
        getattr(settings, "audio_accent_qa_mode", "shadow") == "enforce"
        and isinstance(take.accent_quality, dict)
        and take.accent_quality.get("classification") == "likely_pt_pt"
    ):
        raise HTTPException(409, "Take bloqueado pelo accent QA")

    workflow_before = await repo.obter_por_id(workflow_id)
    assert workflow_before is not None
    old_final_sha256 = workflow_before.audio_final_sha256
    success = await repo.promover_chunk_take(take_id, workflow_id=workflow_id)
    if not success:
        raise HTTPException(409, "Take já foi superado ou rejeitado")

    workflow = await repo.obter_por_id(workflow_id)
    assert workflow is not None
    if workflow.audio_final_sha256 != old_final_sha256:
        raise RuntimeError("chunk promotion unexpectedly changed final narration")

    return {
        "take_id": str(take_id),
        "status": "promoted",
        "chunk_status": "active",
        "audio_edits_pending": True,
        "audio_edit_revision": workflow.audio_edit_revision,
        "final_audio_sha256": workflow.audio_final_sha256,
        "message": "Take selecionado. Use Update Pipeline para publicar as edições.",
    }


@router.post(
    "/{workflow_id}/audio-edits/apply",
    response_model=AudioEditApplyOut,
    status_code=202,
)
async def aplicar_edicoes_pendentes(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Queue one durable materialization of all currently pending chunk edits."""
    key = _action_key(idempotency_key)
    repo = WorkflowRepository(session)
    try:
        update, created = await repo.request_audio_edit_update(workflow_id, key)
    except LookupError as exc:
        raise HTTPException(404, "Workflow não encontrado") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    workflow = await repo.obter_por_id(workflow_id)
    assert workflow is not None
    if created and update is not None:
        aplicar_edicoes_audio.delay(str(update.id))
    return AudioEditApplyOut(
        workflow_id=workflow.id,
        update_id=update.id if update is not None else None,
        status=update.status if update is not None else "no_pending",
        created=created,
        audio_edit_revision=workflow.audio_edit_revision,
        audio_materialized_revision=workflow.audio_materialized_revision,
        audio_edits_pending=(workflow.audio_edit_revision > workflow.audio_materialized_revision),
        final_audio_sha256=workflow.audio_final_sha256,
    )


@router.get(
    "/{workflow_id}/audio-edits/updates/{update_id}",
    response_model=AudioEditUpdateOut,
)
async def obter_atualizacao_edicoes_audio(
    workflow_id: uuid.UUID,
    update_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    update = await WorkflowRepository(session).get_audio_edit_update(update_id)
    if update is None or update.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Atualização de áudio não encontrada")
    return update


@router.post("/{workflow_id}/blocos/{bloco_id}/reassemble", deprecated=True)
async def remontar_bloco(
    workflow_id: uuid.UUID,
    bloco_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Retained as a compatibility guard; publication is now batch-only."""
    repo = WorkflowRepository(session)
    bloco = await repo.obter_bloco_por_id(bloco_id)
    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")
    raise HTTPException(
        409,
        "Remontagem isolada foi substituída por POST /workflow/{id}/audio-edits/apply",
    )


@router.post("/{workflow_id}/chunk-takes/{take_id}/discard")
async def descartar_chunk_take(
    workflow_id: uuid.UUID,
    take_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Discard a candidate take (mark as rejected)."""
    repo = WorkflowRepository(session)

    take = await session.get(AudioChunkTake, take_id)
    if take is None:
        raise HTTPException(404, "Take não encontrado")

    chunk = await repo.obter_chunk(take.audio_chunk_id)
    if chunk is None:
        raise HTTPException(404, "Chunk não encontrado")

    bloco = await repo.obter_bloco_por_id(chunk.audio_bloco_id)
    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    success = await repo.descartar_chunk_take(take_id)
    if not success:
        raise HTTPException(409, "Take não pode ser descartado")

    return {"take_id": str(take_id), "status": "rejected"}


@router.api_route("/{workflow_id}/chunks/{take_id}/audio", methods=["GET", "HEAD"])
async def baixar_chunk_audio(
    workflow_id: uuid.UUID,
    take_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Download audio for a specific chunk take."""
    take = await session.get(AudioChunkTake, take_id)
    if take is None:
        raise HTTPException(404, "Take não encontrado")

    chunk = await session.get(AudioChunk, take.audio_chunk_id)
    if chunk is None:
        raise HTTPException(404, "Chunk não encontrado")

    bloco = await session.get(AudioBloco, chunk.audio_bloco_id)
    if bloco is None or bloco.workflow_execucao_id != workflow_id:
        raise HTTPException(404, "Bloco não encontrado")

    if take.status not in {"completed", "superseded", "rejected"} or not take.audio_path:
        raise HTTPException(404, "Áudio do take não disponível")

    try:
        path, metadata = verify_audio_artifact(
            take.audio_path,
            AudioArtifactExpectation(
                sha256=take.audio_sha256,
                byte_size=take.audio_byte_size,
                duration_seconds=take.audio_duration_seconds,
                sample_rate=take.audio_sample_rate,
                channels=getattr(take, "audio_channels", None),
                frame_count=take.audio_frame_count,
                subtype=getattr(take, "audio_subtype", None),
            ),
        )
    except AudioServingIntegrityError as exc:
        raise HTTPException(409, "Áudio do take falhou na verificação de integridade") from exc

    return audio_file_response(
        path,
        metadata,
        filename=f"chunk_{chunk.chunk_index:04d}_take_{take.take_number:02d}.wav",
        immutable=True,
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
            texto=getattr(bloco, "texto", None),
            normalized_text=getattr(bloco, "normalized_text", None),
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
            initial_resolved_seed=getattr(bloco, "initial_resolved_seed", None),
            voice_chunking_identity=getattr(bloco, "voice_chunking_identity", None),
            generation_attempt=bloco.generation_attempt,
            text_normalizer_version=getattr(bloco, "text_normalizer_version", None),
            text_normalizer_identity=getattr(bloco, "text_normalizer_identity", None),
            pronunciation_glossary_identity=getattr(bloco, "pronunciation_glossary_identity", None),
            pronunciation_replacements=getattr(bloco, "pronunciation_replacements", None),
            retry_seed_strategy=getattr(bloco, "retry_seed_strategy", None),
            generation_attempt_history=getattr(bloco, "generation_attempt_history", None),
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
    if transcricao_data and transcricao_data.audio_sha256 == wf.audio_final_sha256:
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
