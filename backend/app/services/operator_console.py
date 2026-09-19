"""Server-owned operational projections; never mutates production state."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.models.workflow import (
    ProductionQAReport,
    ProductionTimeline,
    WorkflowExecucao,
    WorkflowRender,
)
from app.repositories.human_review_repository import HumanReviewRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.operator import (
    ActionCapabilityOut,
    ArtifactIdentityOut,
    AttentionReasonOut,
    HumanReviewRecordOut,
    HumanReviewStateOut,
    OperatorArtifactIdentitiesOut,
    OperatorWorkflowHistoryOut,
    OperatorWorkflowSnapshotOut,
    OperatorWorkflowSummaryOut,
    QAHistoryOut,
    QAIdentityOut,
    RenderIdentityOut,
    ReviewQueueItemOut,
    WorkflowRenderHistoryOut,
)
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.presentation_spec import PresentationInputError, build_presentation_spec
from app.services.visual_director_service import resolve_production_profile_config
from app.services.production_orchestration_service import ProductionOrchestrationService
from app.services.production_timeline_coverage import can_repair_production_timeline
from app.services.production_timeline_service import ProductionTimelineService
from app.services.program_audio import ProgramAudioError, build_audio_mix_plan
from app.services.visual_director_service import VisualDirectorService


def capability(
    allowed: bool, reason: str | None = None, invalidates: Sequence[str] = ()
) -> ActionCapabilityOut:
    return ActionCapabilityOut(
        allowed=allowed,
        reason=None if allowed else reason,
        invalidates=list(invalidates),
    )


def derive_capabilities(
    workflow: WorkflowExecucao,
    *,
    render: WorkflowRender | None,
    qa: ProductionQAReport | None,
    music_available: bool,
    timeline: ProductionTimeline | None = None,
) -> dict[str, ActionCapabilityOut]:
    production = workflow.production_status
    cancelled = production == "cancelled" or workflow.status == "cancelada"
    active = production in {"queued", "running"} or workflow.status in {
        "agendada",
        "gerando_audio",
    }
    failed = production == "failed" or (production is None and workflow.status == "erro")
    pending_edits = workflow.audio_edit_revision > workflow.audio_materialized_revision
    completed_render = bool(
        render
        and render.status == "completed"
        and render.render_fingerprint
        and render.output_sha256
    )
    return {
        "can_cancel": capability(active, "A produção não está ativa."),
        "can_resume": capability(
            production in {None, "blocked", "cancelled"},
            "Somente produções bloqueadas, canceladas ou legadas podem ser retomadas.",
        ),
        "can_repair_production_timeline": capability(
            can_repair_production_timeline(workflow, timeline),
            "O reparo exige uma produção bloqueada por gaps temporais reais.",
            (
                "visual_plan",
                "timeline",
                "presentation",
                "audio_mix_plan",
                "render",
                "qa",
                "human_review",
            ),
        ),
        "can_retry": capability(failed, "Não há uma falha retryable no estado atual."),
        "can_regenerate_narration": capability(
            not cancelled and not active,
            "Aguarde a operação atual ou retome a produção cancelada.",
            (
                "transcription",
                "alignment",
                "visual_plan",
                "timeline",
                "render",
                "qa",
                "human_review",
            ),
        ),
        "can_apply_audio_edits": capability(
            pending_edits and not active and not cancelled,
            "Não há edições pendentes ou a produção está ativa/cancelada.",
            (
                "transcription",
                "alignment",
                "visual_plan",
                "timeline",
                "render",
                "qa",
                "human_review",
            ),
        ),
        "can_run_standard_qa": capability(
            completed_render and not active,
            "QA exige o render final corrente e concluído.",
            ("qa",),
        ),
        "can_run_deep_qa": capability(
            completed_render and not active,
            "QA profundo exige o render final corrente e concluído.",
            ("qa",),
        ),
        "can_review": capability(
            completed_render,
            "Human Review exige o render final corrente e concluído.",
            ("human_review",),
        ),
        "can_change_music": capability(
            music_available and not active and not cancelled,
            (
                "O workflow não possui Program Audio editável."
                if not music_available
                else "Aguarde a operação atual ou retome a produção cancelada."
            ),
            ("audio_mix_plan", "render", "qa", "human_review"),
        ),
        "can_rebuild_narration_from_takes": capability(
            not cancelled and not active and bool(workflow.blocos),
            "Aguarde a operação atual, retome a produção cancelada, ou certifique-se de que há blocos.",
            (
                "transcription",
                "alignment",
                "visual_plan",
                "timeline",
                "render",
                "qa",
                "human_review",
            ),
        ),
        "can_regenerate_visuals": capability(
            not cancelled
            and not active
            and bool(workflow.blocos)
            and bool(alignment and alignment.status == "completed"),
            "Narração deve estar completa e produção inativa.",
            (
                "visual_plan",
                "timeline",
                "presentation",
                "audio_mix_plan",
                "render",
                "qa",
                "human_review",
            ),
        ),
    }


def derive_attention(
    workflow: WorkflowExecucao,
    *,
    qa: ProductionQAReport | None,
    review: HumanReviewStateOut,
    projection_error: str | None = None,
) -> list[AttentionReasonOut]:
    reasons: list[AttentionReasonOut] = []
    category = workflow.production_error_category or ""
    if workflow.status == "erro":
        reasons.append(
            AttentionReasonOut(
                code="audio_failed", severity="critical", message="A narração falhou."
            )
        )
    if workflow.production_status == "failed":
        code = (
            "music_unresolved"
            if category.startswith(("music_", "audio_mix_"))
            else "production_failed"
        )
        reasons.append(
            AttentionReasonOut(
                code=code,
                severity="critical",
                message=workflow.production_error_message or "A produção falhou.",
            )
        )
    if workflow.production_status == "blocked":
        reasons.append(
            AttentionReasonOut(
                code="production_blocked",
                severity="critical",
                message=workflow.production_error_message or "A produção está bloqueada.",
            )
        )
    if qa is not None:
        if qa.status in {"failed", "blocked"}:
            reasons.append(
                AttentionReasonOut(
                    code="qa_failed",
                    severity="critical",
                    message="O QA corrente não aprovou o render.",
                )
            )
        elif qa.status == "passed_with_warnings" or qa.warning_count:
            reasons.append(
                AttentionReasonOut(
                    code="qa_warning",
                    severity="warning",
                    message="O QA corrente possui avisos para revisão.",
                )
            )
    if review.state == "changes_requested":
        reasons.append(
            AttentionReasonOut(
                code="changes_requested",
                severity="critical",
                message="O operador solicitou alterações no render corrente.",
            )
        )
    elif review.state == "rejected":
        reasons.append(
            AttentionReasonOut(
                code="changes_requested",
                severity="critical",
                message="O render corrente foi rejeitado.",
            )
        )
    elif review.state == "stale":
        reasons.append(
            AttentionReasonOut(
                code="stale_review",
                severity="warning",
                message="A revisão anterior pertence a outro render.",
            )
        )
    elif review.state == "pending":
        reasons.append(
            AttentionReasonOut(
                code="human_review_pending",
                severity="warning",
                message="O render final corrente aguarda revisão humana.",
            )
        )
    if projection_error:
        reasons.append(
            AttentionReasonOut(
                code="service_degraded", severity="warning", message=projection_error
            )
        )
    # Stable, deterministic priority/order and no duplicate categories.
    priority = {"critical": 0, "warning": 1, "info": 2}
    deduplicated = {item.code: item for item in reasons}
    return sorted(deduplicated.values(), key=lambda item: (priority[item.severity], item.code))


class OperatorConsoleService:
    def __init__(self, session: AsyncSession, config: Settings = settings) -> None:
        self.session = session
        self.config = config
        self.workflows = WorkflowRepository(session)
        self.reviews = HumanReviewRepository(session)
        self.production = ProductionOrchestrationService(session, config=config)

    async def _current_qa(
        self, workflow_id: uuid.UUID, render: WorkflowRender | None
    ) -> ProductionQAReport | None:
        if render is None or render.output_sha256 is None:
            return None
        statement = (
            select(ProductionQAReport)
            .where(
                ProductionQAReport.workflow_execucao_id == workflow_id,
                ProductionQAReport.workflow_render_id == render.id,
                ProductionQAReport.render_output_sha256 == render.output_sha256,
            )
            .order_by(ProductionQAReport.created_at.desc(), ProductionQAReport.id.desc())
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def _review_state(
        self, workflow_id: uuid.UUID, render: WorkflowRender | None
    ) -> HumanReviewStateOut:
        history = list(await self.reviews.list_for_workflow(workflow_id))
        identity = self._render_identity(render)
        if identity is None:
            return HumanReviewStateOut(
                state="stale" if history else "unavailable",
                latest_historical=HumanReviewRecordOut.model_validate(history[0])
                if history
                else None,
            )
        current = None
        if render and render.render_fingerprint and render.output_sha256:
            current = await self.reviews.current_for_render(
                workflow_id, render.id, render.render_fingerprint, render.output_sha256
            )
        if current is not None:
            return HumanReviewStateOut(
                state=current.status,
                current=HumanReviewRecordOut.model_validate(current),
                latest_historical=(
                    HumanReviewRecordOut.model_validate(history[0]) if history else None
                ),
                current_render=identity,
            )
        return HumanReviewStateOut(
            state="stale" if history else "pending",
            latest_historical=HumanReviewRecordOut.model_validate(history[0]) if history else None,
            current_render=identity,
        )

    @staticmethod
    def _render_identity(render: WorkflowRender | None) -> RenderIdentityOut | None:
        if render is None:
            return None
        return RenderIdentityOut(
            id=render.id,
            status=render.status,
            phase=render.phase,
            progress=render.progress,
            frames_rendered=render.frames_rendered,
            total_frames=render.total_frames,
            render_fingerprint=render.render_fingerprint,
            output_sha256=render.output_sha256,
            updated_at=render.updated_at,
        )

    @staticmethod
    def _music_requested(workflow: WorkflowExecucao) -> bool:
        blocks = (workflow.roteiro_snapshot or {}).get("blocos", [])
        return any(
            isinstance(block, dict)
            and isinstance(block.get("audio_intent"), dict)
            and block["audio_intent"].get("music_enabled") is True
            for block in blocks
        )

    async def summary(self, workflow: WorkflowExecucao) -> OperatorWorkflowSummaryOut:
        render: WorkflowRender | None = None
        projection_error: str | None = None
        try:
            render = await self.production.current_render(workflow.id)
        except PresentationInputError as exc:
            projection_error = (
                f"Presentation indisponível ({exc.reason or 'presentation_input_invalid'})."
            )
        except ProgramAudioError as exc:
            projection_error = f"Program Audio indisponível ({exc.category})."
        qa = await self._current_qa(workflow.id, render)
        review = await self._review_state(workflow.id, render)
        music_available = (
            self._music_requested(workflow) and workflow.audio_final_sha256 is not None
        )
        timeline = None
        if (
            workflow.production_status == "blocked"
            and workflow.production_phase == "production_timeline"
        ):
            timeline = await self.production.timelines.current(workflow.id)
        snapshot = workflow.roteiro_snapshot or {}
        title = snapshot.get("titulo")
        if not isinstance(title, str) or not title.strip():
            title = f"Produção {str(workflow.id)[:8]}"
        duration = snapshot.get("duracao_total_segundos")
        script_duration = float(duration) if isinstance(duration, int | float) else None
        return OperatorWorkflowSummaryOut(
            id=workflow.id,
            title=title.strip(),
            script_duration_seconds=script_duration,
            audio_duration_seconds=workflow.duracao_audio_segundos,
            production_profile=workflow.perfil_producao,
            legacy_status=workflow.status,
            production_status=workflow.production_status,
            production_phase=workflow.production_phase,
            created_at=workflow.criado_em,
            updated_at=workflow.atualizado_em,
            current_error_category=workflow.production_error_category,
            current_error_message=workflow.production_error_message or workflow.erro_mensagem,
            attention=derive_attention(
                workflow, qa=qa, review=review, projection_error=projection_error
            ),
            capabilities=derive_capabilities(
                workflow, render=render, qa=qa, music_available=music_available, timeline=timeline
            ),
            render=self._render_identity(render),
            qa=(
                QAIdentityOut(
                    id=qa.id,
                    status=qa.status,
                    publication_ready=qa.publication_ready,
                    qa_mode=qa.qa_mode,
                    request_fingerprint=qa.request_fingerprint,
                    warning_count=qa.warning_count,
                    failed_count=qa.failed_count,
                    blocked_count=qa.blocked_count,
                )
                if qa
                else None
            ),
            human_review=review,
            music_override_asset_id=workflow.music_override_asset_id,
            music_override_revision=workflow.music_override_revision,
        )

    async def list_summaries(
        self,
        *,
        state: str | None = None,
        search: str | None = None,
        production_status: str | None = None,
        review_status: str | None = None,
        ordering: str = "updated_desc",
        limit: int = 100,
        offset: int = 0,
    ) -> list[OperatorWorkflowSummaryOut]:
        workflows = list(await self.workflows.listar())
        summaries = [await self.summary(workflow) for workflow in workflows]
        if search:
            needle = search.casefold().strip()
            summaries = [
                item
                for item in summaries
                if needle in item.title.casefold() or needle in str(item.id).casefold()
            ]
        if production_status:
            summaries = [item for item in summaries if item.production_status == production_status]
        if review_status:
            summaries = [item for item in summaries if item.human_review.state == review_status]
        if state:
            summaries = [item for item in summaries if self._matches_state(item, state)]
        if ordering == "title":
            summaries.sort(key=lambda item: (item.title.casefold(), str(item.id)))
        elif ordering == "created_desc":
            summaries.sort(key=lambda item: item.created_at, reverse=True)
        else:
            summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries[offset : offset + limit]

    @staticmethod
    def _matches_state(summary: OperatorWorkflowSummaryOut, state: str) -> bool:
        if state == "active":
            return summary.production_status in {"queued", "running"}
        if state == "needs_attention":
            return bool(summary.attention)
        if state == "needs_review":
            return summary.human_review.state in {
                "pending",
                "stale",
                "changes_requested",
                "rejected",
            }
        if state == "completed":
            return summary.production_status == "completed"
        if state == "failed":
            return summary.production_status == "failed" or summary.legacy_status == "erro"
        if state == "cancelled":
            return summary.production_status == "cancelled" or summary.legacy_status == "cancelada"
        return True

    async def snapshot(self, workflow_id: uuid.UUID) -> OperatorWorkflowSnapshotOut | None:
        workflow = await self.workflows.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            return None
        alignment = await NarrationAlignmentService(self.session, config=self.config).current(
            workflow_id
        )
        visual = await VisualDirectorService(self.session, config=self.config).current(workflow_id)
        timeline = await ProductionTimelineService(self.session, config=self.config).current(
            workflow_id
        )
        presentation_fingerprint = None
        audio_mix_fingerprint = None
        if timeline is not None:
            try:
                profile_config = resolve_production_profile_config(workflow.perfil_producao)
                burned_in_captions = profile_config.burned_in_captions if profile_config else True
                presentation = build_presentation_spec(
                    workflow, duration_seconds=timeline.duration_seconds, burned_in_captions=burned_in_captions
                )
                presentation_fingerprint = presentation.fingerprint if presentation else None
                audio_mix = build_audio_mix_plan(workflow, timeline, self.config)
                audio_mix_fingerprint = audio_mix.fingerprint if audio_mix else None
            except (PresentationInputError, ProgramAudioError):
                pass
        return OperatorWorkflowSnapshotOut(
            workflow=await self.summary(workflow),
            artifacts=OperatorArtifactIdentitiesOut(
                narration_alignment=(
                    ArtifactIdentityOut(
                        id=alignment.id, fingerprint=alignment.fingerprint, status=alignment.status
                    )
                    if alignment
                    else None
                ),
                visual_plan=(
                    ArtifactIdentityOut(
                        id=visual.id, fingerprint=visual.fingerprint, status=visual.status
                    )
                    if visual
                    else None
                ),
                production_timeline=(
                    ArtifactIdentityOut(
                        id=timeline.id, fingerprint=timeline.fingerprint, status=timeline.status
                    )
                    if timeline
                    else None
                ),
                presentation_fingerprint=presentation_fingerprint,
                audio_mix_plan_fingerprint=audio_mix_fingerprint,
            ),
        )

    async def review_queue(self) -> list[ReviewQueueItemOut]:
        summaries = await self.list_summaries(limit=500)
        queue: list[ReviewQueueItemOut] = []
        for item in summaries:
            codes = {reason.code for reason in item.attention}
            if "audio_failed" in codes:
                queue.append(
                    ReviewQueueItemOut(workflow=item, category="audio_attention", priority=1)
                )
            elif item.human_review.state in {"changes_requested", "rejected"}:
                queue.append(
                    ReviewQueueItemOut(workflow=item, category="changes_requested", priority=1)
                )
            elif item.qa and item.qa.status in {"failed", "blocked", "passed_with_warnings"}:
                queue.append(
                    ReviewQueueItemOut(workflow=item, category="qa_warning_review", priority=2)
                )
            elif item.render and item.human_review.state in {"pending", "stale"}:
                queue.append(
                    ReviewQueueItemOut(workflow=item, category="final_video_review", priority=3)
                )
        queue.sort(key=lambda item: (item.priority, -item.workflow.updated_at.timestamp()))
        return queue

    async def history(self, workflow_id: uuid.UUID) -> OperatorWorkflowHistoryOut | None:
        workflow = await self.workflows.obter_por_id(workflow_id)
        if workflow is None:
            return None
        renders_result = await self.session.execute(
            select(WorkflowRender)
            .where(WorkflowRender.workflow_execucao_id == workflow_id)
            .order_by(WorkflowRender.created_at.desc(), WorkflowRender.id.desc())
        )
        qa_result = await self.session.execute(
            select(ProductionQAReport)
            .where(ProductionQAReport.workflow_execucao_id == workflow_id)
            .order_by(ProductionQAReport.created_at.desc(), ProductionQAReport.id.desc())
        )
        reviews = await self.reviews.list_for_workflow(workflow_id)
        return OperatorWorkflowHistoryOut(
            workflow_id=workflow.id,
            created_at=workflow.criado_em,
            production_error_history=workflow.production_error_history or [],
            audio_edit_revision=workflow.audio_edit_revision,
            audio_materialized_revision=workflow.audio_materialized_revision,
            renders=[
                WorkflowRenderHistoryOut(
                    id=item.id,
                    status=item.status,
                    phase=item.phase,
                    render_profile=item.render_profile,
                    request_fingerprint=item.request_fingerprint,
                    render_fingerprint=item.render_fingerprint,
                    manifest_fingerprint=item.manifest_fingerprint,
                    output_sha256=item.output_sha256,
                    output_duration_seconds=item.output_duration_seconds,
                    error_category=item.error_category,
                    error_message=item.error_message,
                    created_at=item.created_at,
                    completed_at=item.completed_at,
                )
                for item in renders_result.scalars().all()
            ],
            qa_reports=[
                QAHistoryOut(
                    id=item.id,
                    workflow_render_id=item.workflow_render_id,
                    qa_mode=item.qa_mode,
                    status=item.status,
                    publication_ready=item.publication_ready,
                    request_fingerprint=item.request_fingerprint,
                    warning_count=item.warning_count,
                    failed_count=item.failed_count,
                    blocked_count=item.blocked_count,
                    error_category=item.error_category,
                    error_message=item.error_message,
                    created_at=item.created_at,
                    completed_at=item.completed_at,
                )
                for item in qa_result.scalars().all()
            ],
            human_reviews=[HumanReviewRecordOut.model_validate(item) for item in reviews],
        )
