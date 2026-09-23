"""Stage 17 durable state machine from approved script to validated MP4."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.media_indexer_search import MediaIndexerSearchProvider, MediaSearchProvider
from app.clients.video_composer_client import (
    ComposerRender,
    VideoComposerClient,
    VideoComposerError,
    VideoComposerNotFoundError,
    VideoComposerTransientError,
)
from app.core.config import Settings, settings
from app.models.workflow import ProductionTimeline, WorkflowExecucao, WorkflowRender
from app.repositories.workflow_render_repository import WorkflowRenderRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.audio_service import processar_workflow
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.presentation_spec import PresentationInputError, build_presentation_spec
from app.services.production_qa_checks import visual_plan_semantic_metrics
from app.services.production_timeline_coverage import (
    can_repair_production_timeline,
    temporal_gap_details,
    timeline_has_temporal_gaps,
    timeline_is_gap_free,
)
from app.services.production_timeline_service import ProductionTimelineService
from app.services.program_audio import ProgramAudioError, build_audio_mix_plan
from app.services.visual_director_service import (
    VisualDirectorService,
    resolve_production_profile_config,
)
from app.services.visual_diversity import (
    VisualDiversityPolicy,
    timeline_visual_diversity,
)

logger = logging.getLogger(__name__)

GAP_MESSAGE = "Final Video Composer profile requires a gap-free ProductionTimeline"


@dataclass(frozen=True)
class OrchestrationResult:
    terminal: bool
    poll_render_id: uuid.UUID | None = None
    qa_workflow_id: uuid.UUID | None = None


class ProductionOrchestrationService:
    def __init__(
        self,
        session: AsyncSession,
        config: Settings = settings,
        *,
        workflow_repository: Any | None = None,
        render_repository: Any | None = None,
        alignment_service: Any | None = None,
        visual_service: Any | None = None,
        timeline_service: Any | None = None,
        audio_processor: Callable[..., Any] = processar_workflow,
        composer_client_factory: Callable[[], VideoComposerClient] | None = None,
        media_provider: MediaSearchProvider | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.workflows = workflow_repository or WorkflowRepository(session)
        self.renders = render_repository or WorkflowRenderRepository(session)
        self.alignments = alignment_service or NarrationAlignmentService(session, config=config)
        self.visual = visual_service or VisualDirectorService(session, config=config)
        self.timelines = timeline_service or ProductionTimelineService(session, config=config)
        self.audio_processor = audio_processor
        self.composer_client_factory = composer_client_factory or (
            lambda: VideoComposerClient(config)
        )
        self.media_provider = media_provider or MediaIndexerSearchProvider(config)

    async def process(self, workflow_id: uuid.UUID) -> OrchestrationResult:
        workflow = await self.workflows.obter_por_id(workflow_id)
        if workflow is None or workflow.production_status in {"cancelled", "completed"}:
            return OrchestrationResult(terminal=True)
        if workflow.production_status not in {"queued", "running"}:
            return OrchestrationResult(terminal=True)
        phase = workflow.production_phase or "audio"
        await self.workflows.atualizar_producao(
            workflow.id, status="running", phase=phase, start=True
        )
        render_id_for_retry: uuid.UUID | None = None

        try:
            while True:
                workflow = await self.workflows.obter_por_id(workflow.id)
                if workflow is None or self._cancelled(workflow):
                    return OrchestrationResult(terminal=True)
                phase = workflow.production_phase or "audio"
                if phase == "audio":
                    if not self._audio_is_reusable(workflow):
                        await self.audio_processor(self.session, workflow.id)
                        workflow = await self.workflows.obter_por_id(workflow.id)
                        if workflow is None or self._cancelled(workflow):
                            return OrchestrationResult(terminal=True)
                        if not self._audio_is_reusable(workflow):
                            await self._fail(
                                workflow,
                                "audio",
                                workflow.erro_mensagem or "Final narration production failed",
                            )
                            return OrchestrationResult(terminal=True)
                    await self._advance(workflow, "narration_alignment")
                    continue

                if phase == "narration_alignment":
                    alignment, _ = await self.alignments.ensure(workflow.id, force=False)
                    if alignment.status == "completed":
                        await self._advance(workflow, "visual_plan")
                        continue
                    await self._upstream_terminal(
                        workflow, "narration_alignment", alignment.status, alignment.error_message
                    )
                    return OrchestrationResult(terminal=True)

                if phase == "visual_plan":
                    plan, _ = await self.visual.ensure(workflow.id, force=False)
                    if plan.status in {"completed", "completed_with_gaps"}:
                        await self._advance(workflow, "production_timeline")
                        continue
                    diagnostics = getattr(plan, "diagnostics", None) or {}
                    stable_category = diagnostics.get("error_category")
                    if plan.status == "blocked" and isinstance(stable_category, str):
                        await self._block(
                            workflow,
                            "visual_plan",
                            stable_category,
                            plan.error_message or "visual_plan blocked",
                        )
                        return OrchestrationResult(terminal=True)
                    await self._upstream_terminal(
                        workflow, "visual_plan", plan.status, plan.error_message
                    )
                    return OrchestrationResult(terminal=True)

                if phase == "production_timeline":
                    timeline = await self.timelines.current(workflow.id)
                    if timeline is None:
                        timeline, _ = await self.timelines.ensure(workflow.id, force=False)
                    if timeline_is_gap_free(timeline):
                        await self._advance(workflow, "render")
                        continue
                    if timeline_has_temporal_gaps(timeline):
                        await self._block(
                            workflow,
                            "production_timeline",
                            "production_timeline_has_gaps",
                            GAP_MESSAGE,
                        )
                    else:
                        await self._upstream_terminal(
                            workflow,
                            "production_timeline",
                            timeline.status,
                            timeline.error_message,
                        )
                    return OrchestrationResult(terminal=True)

                if phase == "render":
                    timeline = await self.timelines.current(workflow.id)
                    if timeline_has_temporal_gaps(timeline):
                        await self._block(
                            workflow,
                            "production_timeline",
                            "production_timeline_has_gaps",
                            GAP_MESSAGE,
                        )
                        return OrchestrationResult(terminal=True)
                    if not timeline_is_gap_free(timeline):
                        await self._block(
                            workflow,
                            "render",
                            "stale_render_input",
                            "Current render input is unavailable",
                        )
                        return OrchestrationResult(terminal=True)
                    assert timeline is not None
                    hybrid_errors = self._hybrid_timeline_errors(timeline)
                    if hybrid_errors:
                        await self._block(
                            workflow,
                            "render",
                            "hybrid_timeline_gate_failed",
                            "ProductionTimeline failed hybrid source contract gate: "
                            + ", ".join(hybrid_errors),
                        )
                        return OrchestrationResult(terminal=True)
                    image_errors = await self._validate_static_image_assets(timeline)
                    if image_errors:
                        await self._block(
                            workflow,
                            "render",
                            "image_asset_validation_failed",
                            "Static image integrity validation failed: " + ", ".join(image_errors),
                        )
                        return OrchestrationResult(terminal=True)
                    diversity = timeline_visual_diversity(
                        timeline.placements,
                        self._visual_diversity_policy(timeline),
                    )
                    visual_plan = await self.visual.current(workflow.id)
                    semantic = visual_plan_semantic_metrics(visual_plan)
                    semantic_failed = bool(
                        semantic["entity_violation_count"]
                        or (
                            getattr(visual_plan, "decisions", None)
                            and not semantic["semantic_coverage"]
                        )
                    )
                    if diversity.status == "failed" or semantic_failed:
                        reasons = list(diversity.reasons)
                        if semantic["entity_violation_count"]:
                            reasons.append("selected source conflicts with query game identity")
                        if (
                            getattr(visual_plan, "decisions", None)
                            and not semantic["semantic_coverage"]
                        ):
                            reasons.append("all visual queries lack selected sources")
                        await self._block(
                            workflow,
                            "render",
                            "visual_diversity_gate_failed",
                            "ProductionTimeline failed pre-render visual QA gate: "
                            + ", ".join(reasons),
                        )
                        return OrchestrationResult(terminal=True)
                    render = await self._ensure_local_render(workflow, timeline)
                    render_id_for_retry = render.id
                    remote = await self._reconcile_or_submit(
                        render,
                        retry_failed=(
                            render.status == "pending" and render.composer_render_id is not None
                        ),
                    )
                    return await self._apply_remote(workflow, render, remote)

                return OrchestrationResult(terminal=True)
        except VideoComposerTransientError as exc:
            logger.warning(
                "transient Video Composer failure; scheduling bounded reconciliation",
                extra={
                    "workflow_id": str(workflow_id),
                    "render_id": str(render_id_for_retry) if render_id_for_retry else None,
                    "error_category": type(exc).__name__,
                },
            )
            return OrchestrationResult(
                terminal=render_id_for_retry is None,
                poll_render_id=render_id_for_retry,
            )
        except VideoComposerError as exc:
            current = await self.workflows.obter_por_id(workflow_id)
            if current is not None and not self._cancelled(current):
                await self._fail(current, "composer_request", self._safe_message(exc))
            return OrchestrationResult(terminal=True)
        except ProgramAudioError as exc:
            current = await self.workflows.obter_por_id(workflow_id)
            if current is not None and not self._cancelled(current):
                await self._fail(current, exc.category, str(exc))
            return OrchestrationResult(terminal=True)
        except PresentationInputError as exc:
            current = await self.workflows.obter_por_id(workflow_id)
            timeline = await self.timelines.current(workflow_id)
            logger.error(
                "presentation spec input rejected",
                extra={
                    "workflow_id": str(workflow_id),
                    "alignment_id": (
                        str(getattr(timeline, "narration_alignment_id", None))
                        if timeline is not None
                        and getattr(timeline, "narration_alignment_id", None)
                        else None
                    ),
                    "timeline_id": str(timeline.id) if timeline is not None else None,
                    "timeline_duration": (
                        timeline.duration_seconds if timeline is not None else None
                    ),
                    **exc.log_fields,
                },
            )
            if current is not None and not self._cancelled(current):
                await self._fail(current, "presentation_input_invalid", str(exc))
            return OrchestrationResult(terminal=True)
        except Exception as exc:
            logger.exception(
                "production orchestration failed", extra={"workflow_id": str(workflow_id)}
            )
            current = await self.workflows.obter_por_id(workflow_id)
            if current is not None and not self._cancelled(current):
                await self._fail(current, "orchestration_failure", self._safe_message(exc))
            return OrchestrationResult(terminal=True)

    async def poll(self, workflow_id: uuid.UUID, render_id: uuid.UUID) -> OrchestrationResult:
        workflow = await self.workflows.obter_por_id(workflow_id)
        render = await self.renders.get_by_id(render_id)
        if workflow is None or render is None or self._cancelled(workflow):
            return OrchestrationResult(terminal=True)
        if workflow.production_status != "running" or workflow.production_phase != "render":
            return OrchestrationResult(terminal=True)
        timeout_candidate = self._timed_out(render)
        try:
            remote = await self._reconcile_or_submit(render, retry_failed=False)
            result = await self._apply_remote(workflow, render, remote)
            if timeout_candidate and not result.terminal:
                await self.renders.mark_failed(
                    render, "composer_timeout", "Video Composer render timed out"
                )
                await self._fail(
                    workflow, "composer_timeout", "Video Composer render timed out"
                )
                return OrchestrationResult(terminal=True)
            return result
        except VideoComposerTransientError as exc:
            logger.warning(
                "transient Video Composer polling failure; scheduling bounded reconciliation",
                extra={
                    "workflow_id": str(workflow_id),
                    "render_id": str(render.id),
                    "error_category": type(exc).__name__,
                },
            )
            if timeout_candidate and self._timeout_grace_exhausted(render):
                await self.renders.mark_failed(
                    render,
                    "composer_timeout",
                    "Video Composer render timed out after reconciliation grace",
                )
                await self._fail(
                    workflow,
                    "composer_timeout",
                    "Video Composer render timed out after reconciliation grace",
                )
                return OrchestrationResult(terminal=True)
            return OrchestrationResult(terminal=False, poll_render_id=render.id)
        except VideoComposerError as exc:
            await self._fail(workflow, "composer_request", self._safe_message(exc))
            return OrchestrationResult(terminal=True)

    async def explicit_retry(self, workflow: WorkflowExecucao) -> None:
        if workflow.production_phase == "render":
            render = await self.current_render(workflow.id)
            if render is not None and render.status == "failed":
                # Keep the remote id for a mandatory GET-before-POST reconciliation.
                await self.renders.reset_for_explicit_retry(render)
        await self.workflows.atualizar_producao(
            workflow.id,
            status="queued",
            phase=workflow.production_phase or "audio",
        )

    async def resume(self, workflow: WorkflowExecucao) -> None:
        source_status = workflow.production_status
        # The legacy status is an independent cancellation guard. Restore only
        # the state justified by the durable audio artifact; never claim that
        # audio completed when it is not reusable.
        if source_status == "cancelled" and workflow.status == "cancelada":
            workflow.status = (
                "audio_concluido" if self._audio_artifact_is_reusable(workflow) else "agendada"
            )

        phase = workflow.production_phase
        if source_status in {None, "cancelled"}:
            phase = await self._earliest_missing_phase(workflow)

        # A cancelled render may have finished or failed remotely while polls
        # were correctly ignoring it. Mark its local mirror pending inside the
        # same transaction so the resumed worker performs mandatory GET-before-
        # POST reconciliation and may retry a confirmed remote failure.
        if source_status == "cancelled" and phase == "render":
            render = await self._current_render_for_timeline(workflow.id)
            if render is not None and render.status != "completed":
                await self.renders.reset_for_explicit_retry(render, commit=False)
        await self.workflows.atualizar_producao(
            workflow.id, status="queued", phase=phase or "audio"
        )

    async def repair_timeline(self, workflow: WorkflowExecucao) -> None:
        timeline = await self.timelines.current(workflow.id)
        if not can_repair_production_timeline(workflow, timeline):
            raise ValueError("O reparo exige uma produção bloqueada por gaps temporais reais.")
        # The endpoint locks the workflow before claiming this explicit operation.
        await self.workflows.atualizar_producao(
            workflow.id, status="running", phase="production_timeline"
        )
        try:
            timeline, _ = await self.timelines.ensure(workflow.id, force=True)
            if timeline_has_temporal_gaps(timeline) and any(
                gap.reason in {"source_unresolved", "source_too_short"} for gap in timeline.gaps
            ):
                # Quality-guard rejections are recorded as unresolved sources by the planner.
                await self.visual.ensure(workflow.id, force=True)
                timeline, _ = await self.timelines.ensure(workflow.id, force=True)
        except Exception:
            await self.session.rollback()
            latest = await self.workflows.obter_por_id(workflow.id)
            if latest is not None and not self._cancelled(latest):
                await self._block(
                    latest,
                    "production_timeline",
                    "production_timeline_has_gaps",
                    "O reparo da timeline falhou; os artefatos de narração foram preservados.",
                )
            return
        latest = await self.workflows.obter_por_id(workflow.id)
        if latest is None or self._cancelled(latest):
            return
        if timeline_is_gap_free(timeline):
            await self.resume(latest)
        elif timeline_has_temporal_gaps(timeline):
            await self._block(
                latest,
                "production_timeline",
                "production_timeline_has_gaps",
                GAP_MESSAGE + ": " + json.dumps(temporal_gap_details(timeline)),
            )
        else:
            await self._block(
                latest,
                "production_timeline",
                "production_timeline_repair_failed",
                "Não foi possível concluir o replanejamento da timeline.",
            )

    async def force_visual_regeneration(self, workflow: WorkflowExecucao) -> None:
        """Force VisualPlan and Timeline regeneration while preserving narration.

        Used when visual search parameters change (e.g., Game Entity Guard)
        and we want to re-run visual selection without changing audio.
        """
        # Start from visual_plan phase, preserving audio and alignment
        await self.workflows.atualizar_producao(
            workflow.id, status="running", phase="visual_plan"
        )
        try:
            # Force regeneration of visual plan
            plan, _ = await self.visual.ensure(workflow.id, force=True)
            if plan.status not in {"completed", "completed_with_gaps"}:
                diagnostics = getattr(plan, "diagnostics", None) or {}
                stable_category = diagnostics.get("error_category")
                if plan.status == "blocked" and isinstance(stable_category, str):
                    await self._block(
                        workflow,
                        "visual_plan",
                        stable_category,
                        plan.error_message or "visual_plan blocked",
                    )
                else:
                    await self._upstream_terminal(
                        workflow, "visual_plan", plan.status, plan.error_message
                    )
                return

            # Force regeneration of timeline
            timeline, _ = await self.timelines.ensure(workflow.id, force=True)
            if timeline_is_gap_free(timeline):
                await self.resume(workflow)
            elif timeline_has_temporal_gaps(timeline):
                await self._block(
                    workflow,
                    "production_timeline",
                    "production_timeline_has_gaps",
                    GAP_MESSAGE + ": " + json.dumps(temporal_gap_details(timeline)),
                )
            else:
                await self._block(
                    workflow,
                    "production_timeline",
                    "production_timeline_regeneration_failed",
                    "Não foi possível concluir o replanejamento da timeline.",
                )
        except Exception:
            await self.session.rollback()
            latest = await self.workflows.obter_por_id(workflow.id)
            if latest is not None and not self._cancelled(latest):
                await self._fail(
                    latest,
                    "visual_regeneration",
                    "Falha na regeneração de visuais; os artefatos de narração foram preservados.",
                )

    async def current_render(self, workflow_id: uuid.UUID) -> WorkflowRender | None:
        workflow = await self.workflows.obter_por_id(workflow_id)
        if workflow is None or workflow.production_phase not in {"render", "completed"}:
            return None
        return await self._current_render_for_timeline(workflow_id)

    async def _current_render_for_timeline(self, workflow_id: uuid.UUID) -> WorkflowRender | None:
        timeline = await self.timelines.current(workflow_id)
        if timeline is None:
            return None
        presentation_fingerprint = await self._presentation_fingerprint(workflow_id, timeline)
        audio_mix_plan_fingerprint = await self._audio_mix_plan_fingerprint(workflow_id, timeline)
        render_profile = self._render_profile(audio_mix_plan_fingerprint)
        fingerprint = self.render_request_fingerprint(
            workflow_id,
            timeline,
            presentation_fingerprint=presentation_fingerprint,
            audio_mix_plan_fingerprint=audio_mix_plan_fingerprint,
            render_profile=render_profile,
        )
        return await self.renders.get_by_request_fingerprint(fingerprint)

    async def _ensure_local_render(
        self, workflow: WorkflowExecucao, timeline: ProductionTimeline
    ) -> WorkflowRender:
        presentation_fingerprint = await self._presentation_fingerprint(workflow.id, timeline)
        audio_mix_plan_fingerprint = await self._audio_mix_plan_fingerprint(workflow.id, timeline)
        render_profile = self._render_profile(audio_mix_plan_fingerprint)
        fingerprint = self.render_request_fingerprint(
            workflow.id,
            timeline,
            presentation_fingerprint=presentation_fingerprint,
            audio_mix_plan_fingerprint=audio_mix_plan_fingerprint,
            render_profile=render_profile,
        )
        key = f"neovids-render:{workflow.id}:{fingerprint[:32]}"
        render, _ = await self.renders.create_or_get(
            workflow_execucao_id=workflow.id,
            production_timeline_id=timeline.id,
            production_timeline_fingerprint=timeline.fingerprint,
            render_profile=render_profile,
            mode="final",
            request_fingerprint=fingerprint,
            idempotency_key=key,
            status="pending",
            phase="pending",
        )
        return render

    def render_request_fingerprint(
        self,
        workflow_id: uuid.UUID,
        timeline: ProductionTimeline,
        *,
        presentation_fingerprint: str | None = None,
        audio_mix_plan_fingerprint: str | None = None,
        render_profile: str | None = None,
    ) -> str:
        effective_profile = render_profile or self._render_profile(audio_mix_plan_fingerprint)
        payload = {
            "contract": (
                "stage_17_workflow_render_v3_program_audio"
                if audio_mix_plan_fingerprint
                else (
                    "stage_17_workflow_render_v2_presentation"
                    if presentation_fingerprint
                    else "stage_17_workflow_render_v1"
                )
            ),
            "workflow_id": str(workflow_id),
            "production_timeline_id": str(timeline.id),
            "production_timeline_fingerprint": timeline.fingerprint,
            "render_profile": effective_profile,
            "mode": "final",
        }
        if presentation_fingerprint:
            payload["presentation_fingerprint"] = presentation_fingerprint
        if audio_mix_plan_fingerprint:
            payload["audio_mix_plan_fingerprint"] = audio_mix_plan_fingerprint
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    async def _presentation_fingerprint(
        self, workflow_id: uuid.UUID, timeline: ProductionTimeline
    ) -> str | None:
        workflow = await self.workflows.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            return None
        profile_config = resolve_production_profile_config(workflow.perfil_producao)
        burned_in_captions = profile_config.burned_in_captions if profile_config else True
        presentation = build_presentation_spec(
            workflow,
            duration_seconds=timeline.duration_seconds,
            burned_in_captions=burned_in_captions,
        )
        return presentation.fingerprint if presentation else None

    async def _audio_mix_plan_fingerprint(
        self, workflow_id: uuid.UUID, timeline: ProductionTimeline
    ) -> str | None:
        workflow = await self.workflows.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            return None
        plan = build_audio_mix_plan(workflow, timeline, self.config)
        return plan.fingerprint if plan else None

    def _render_profile(self, audio_mix_plan_fingerprint: str | None) -> str:
        if audio_mix_plan_fingerprint:
            return self.config.video_composer_program_audio_render_profile
        return self.config.video_composer_render_profile

    def _visual_diversity_policy(
        self, timeline: ProductionTimeline | None = None
    ) -> VisualDiversityPolicy:
        hybrid = bool(
            timeline
            and getattr(timeline, "schema_version", None)
            == self.config.production_timeline_hybrid_schema_version
        )
        return VisualDiversityPolicy(
            version=(
                self.config.visual_diversity_hybrid_policy_version
                if hybrid
                else self.config.visual_diversity_policy_version
            ),
            minimum_window_count=self.config.visual_diversity_minimum_window_count,
            maximum_reuse_ratio=self.config.visual_diversity_maximum_reuse_ratio,
            maximum_dominant_window_ratio=(
                self.config.visual_diversity_maximum_dominant_window_ratio
            ),
            maximum_dominant_static_ratio=(
                self.config.visual_diversity_maximum_dominant_static_ratio if hybrid else 1.0
            ),
            maximum_static_reuse_count=(
                self.config.visual_diversity_maximum_static_reuse_count if hybrid else 2**31 - 1
            ),
            maximum_consecutive_static_seconds=(
                self.config.static_image_maximum_consecutive_seconds if hybrid else float("inf")
            ),
        )

    def _hybrid_timeline_errors(self, timeline: ProductionTimeline) -> list[str]:
        if (
            getattr(timeline, "schema_version", None)
            != self.config.production_timeline_hybrid_schema_version
        ):
            return []
        errors: list[str] = []
        consecutive_static = 0.0
        previous_end: float | None = None
        for item in sorted(timeline.placements, key=lambda value: value.order):
            duration = item.timeline_end_seconds - item.timeline_start_seconds
            if item.source_type == "static_image":
                if any(
                    value is not None
                    for value in (
                        item.source_session_id,
                        item.candidate_start_seconds,
                        item.candidate_end_seconds,
                        item.source_start_seconds,
                        item.source_end_seconds,
                        item.playback_rate,
                    )
                ):
                    errors.append(f"placement_{item.order}_image_has_temporal_fields")
                provenance = item.source_provenance or {}
                presentation = item.presentation or {}
                if not (
                    item.source_image_semantic_index_id
                    and provenance.get("expected_sha256")
                    and provenance.get("expected_byte_size") is not None
                    and provenance.get("content_type") in {"image/jpeg", "image/png", "image/webp"}
                ):
                    errors.append(f"placement_{item.order}_image_identity_invalid")
                zoom_amount = presentation.get("zoom_amount")
                if not (
                    presentation.get("policy_version")
                    == self.config.static_image_presentation_policy_version
                    and presentation.get("fit") in {"cover", "contain"}
                    and presentation.get("background") in {"black", "blurred_fill"}
                    and presentation.get("motion") in {"none", "slow_zoom_in", "slow_zoom_out"}
                    and isinstance(zoom_amount, int | float)
                    and 0 <= zoom_amount <= 0.1
                ):
                    errors.append(f"placement_{item.order}_presentation_invalid")
                if not (
                    self.config.static_image_minimum_hold_seconds
                    <= duration
                    <= self.config.static_image_maximum_hold_seconds
                ):
                    errors.append(f"placement_{item.order}_image_hold_invalid")
                if previous_end != item.timeline_start_seconds:
                    consecutive_static = 0.0
                consecutive_static += duration
                if consecutive_static > self.config.static_image_maximum_consecutive_seconds:
                    errors.append("consecutive_static_hold_exceeded")
            elif item.source_type == "video_segment":
                consecutive_static = 0.0
                if (
                    item.source_session_id is None
                    or item.source_image_semantic_index_id is not None
                    or item.source_start_seconds is None
                    or item.source_end_seconds is None
                    or item.playback_rate != 1.0
                    or item.presentation is not None
                ):
                    errors.append(f"placement_{item.order}_video_contract_invalid")
            else:
                errors.append(f"placement_{item.order}_source_type_invalid")
            previous_end = item.timeline_end_seconds
        return sorted(set(errors))

    async def _validate_static_image_assets(self, timeline: ProductionTimeline) -> list[str]:
        expected: dict[uuid.UUID, tuple[uuid.UUID, str, int, str]] = {}
        for item in timeline.placements:
            if getattr(item, "source_type", "gameplay") != "static_image":
                continue
            provenance = item.source_provenance or {}
            expected[item.source_media_id] = (
                item.source_project_id,
                str(provenance.get("expected_sha256", "")),
                int(provenance.get("expected_byte_size", -1)),
                str(provenance.get("content_type", "")),
            )
        errors: list[str] = []
        for media_id, identity in sorted(expected.items(), key=lambda value: str(value[0])):
            try:
                asset = await self.media_provider.get_asset(media_id)
            except Exception as exc:
                errors.append(f"{media_id}:provider_{type(exc).__name__}")
                continue
            project_id, sha256, byte_size, content_type = identity
            if (
                asset.project_id != project_id
                or asset.media_kind != "image"
                or asset.sha256 != sha256
                or asset.byte_size != byte_size
                or asset.content_type != content_type
            ):
                errors.append(f"{media_id}:identity_mismatch")
        return errors

    async def _reconcile_or_submit(
        self, render: WorkflowRender, *, retry_failed: bool
    ) -> ComposerRender:
        async with self.composer_client_factory() as composer:
            if render.composer_render_id is not None:
                try:
                    remote = await composer.get_render(str(render.composer_render_id))
                except VideoComposerNotFoundError:
                    remote = await composer.create_render(
                        workflow_id=render.workflow_execucao_id,
                        render_profile=render.render_profile,
                        mode="final",
                        idempotency_key=render.idempotency_key,
                    )
                    await self.renders.bind_remote(render, remote)
                    return remote
                if remote.status != "failed" or not retry_failed:
                    return remote
                remote = await composer.create_render(
                    workflow_id=render.workflow_execucao_id,
                    render_profile=render.render_profile,
                    mode="final",
                    idempotency_key=None,
                )
                await self.renders.bind_remote(render, remote)
                return remote
            remote = await composer.create_render(
                workflow_id=render.workflow_execucao_id,
                render_profile=render.render_profile,
                mode="final",
                idempotency_key=render.idempotency_key,
            )
            await self.renders.bind_remote(render, remote)
            return remote

    async def _apply_remote(
        self, workflow: WorkflowExecucao, render: WorkflowRender, remote: ComposerRender
    ) -> OrchestrationResult:
        if not self._remote_identity_matches(render, remote):
            await self.renders.mark_failed(
                render,
                "composer_contract_mismatch",
                "Video Composer returned another render identity",
            )
            await self._fail(
                workflow,
                "composer_contract_mismatch",
                "Video Composer returned another render identity",
            )
            return OrchestrationResult(terminal=True)
        await self.renders.sync_remote(render, remote)
        if remote.status == "failed":
            category = f"composer:{remote.error.category if remote.error else 'failed'}"
            message = (
                remote.error.message
                if remote.error and remote.error.message
                else "Video Composer render failed"
            )
            await self._fail(workflow, category, message)
            return OrchestrationResult(terminal=True)
        if remote.status != "completed":
            return OrchestrationResult(terminal=False, poll_render_id=render.id)

        current = await self.timelines.current(workflow.id)
        if (
            current is None
            or current.id != render.production_timeline_id
            or current.fingerprint != render.production_timeline_fingerprint
        ):
            await self._block(
                workflow,
                "render",
                "stale_render_input",
                "Completed render no longer matches the current ProductionTimeline",
            )
            return OrchestrationResult(terminal=True)
        current_presentation_fingerprint = await self._presentation_fingerprint(
            workflow.id, current
        )
        current_audio_mix_plan_fingerprint = await self._audio_mix_plan_fingerprint(
            workflow.id, current
        )
        if remote.presentation_fingerprint != current_presentation_fingerprint:
            await self._block(
                workflow,
                "render",
                "composer_presentation_mismatch",
                "Video Composer returned another PresentationSpec identity",
            )
            return OrchestrationResult(terminal=True)
        if (
            getattr(remote, "audio_mix_plan_fingerprint", None)
            != current_audio_mix_plan_fingerprint
        ):
            await self._block(
                workflow,
                "render",
                "composer_audio_mix_plan_mismatch",
                "Video Composer returned another AudioMixPlan identity",
            )
            return OrchestrationResult(terminal=True)
        if render.request_fingerprint != self.render_request_fingerprint(
            workflow.id,
            current,
            presentation_fingerprint=current_presentation_fingerprint,
            audio_mix_plan_fingerprint=current_audio_mix_plan_fingerprint,
            render_profile=render.render_profile,
        ):
            await self._block(
                workflow,
                "render",
                "stale_render_presentation",
                "Completed render belongs to another PresentationSpec",
            )
            return OrchestrationResult(terminal=True)
        latest = await self.workflows.obter_por_id(workflow.id)
        if latest is None or self._cancelled(latest):
            return OrchestrationResult(terminal=True)
        await self.workflows.atualizar_producao(
            workflow.id, status="completed", phase="completed", complete=True
        )
        return OrchestrationResult(terminal=True, qa_workflow_id=workflow.id)

    @staticmethod
    def _remote_identity_matches(render: WorkflowRender, remote: ComposerRender) -> bool:
        return (
            str(remote.workflow_id) == str(render.workflow_execucao_id)
            and str(remote.production_timeline_id) == str(render.production_timeline_id)
            and remote.production_timeline_fingerprint == render.production_timeline_fingerprint
            and remote.render_profile == render.render_profile
            and remote.mode == "final"
        )

    async def _earliest_missing_phase(self, workflow: WorkflowExecucao) -> str:
        if not self._audio_is_reusable(workflow):
            return "audio"
        alignment = await self.alignments.current(workflow.id)
        if alignment is None or alignment.status != "completed":
            return "narration_alignment"
        plan = await self.visual.current(workflow.id)
        if plan is None or plan.status not in {"completed", "completed_with_gaps"}:
            return "visual_plan"
        timeline = await self.timelines.current(workflow.id)
        if not timeline_is_gap_free(timeline):
            return "production_timeline"
        return "render"

    async def _advance(self, workflow: WorkflowExecucao, phase: str) -> None:
        await self.workflows.atualizar_producao(workflow.id, status="running", phase=phase)

    async def _upstream_terminal(
        self, workflow: WorkflowExecucao, phase: str, status: str, message: str | None
    ) -> None:
        if status == "blocked":
            await self._block(workflow, phase, f"{phase}_blocked", message or f"{phase} blocked")
        else:
            await self._fail(workflow, f"{phase}_failed", message or f"{phase} failed")

    async def _block(
        self, workflow: WorkflowExecucao, phase: str, category: str, message: str
    ) -> None:
        await self.workflows.atualizar_producao(
            workflow.id,
            status="blocked",
            phase=phase,
            error_category=category,
            error_message=message,
        )

    async def _fail(self, workflow: WorkflowExecucao, category: str, message: str) -> None:
        await self.workflows.atualizar_producao(
            workflow.id,
            status="failed",
            phase=workflow.production_phase or "audio",
            error_category=category,
            error_message=message,
            complete=True,
        )

    def _timed_out(self, render: WorkflowRender) -> bool:
        anchor = render.started_at or render.created_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=dt.UTC)
        return (
            dt.datetime.now(dt.UTC) - anchor
        ).total_seconds() > self.config.video_composer_render_timeout_seconds

    def _timeout_grace_exhausted(self, render: WorkflowRender) -> bool:
        anchor = render.started_at or render.created_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=dt.UTC)
        return (dt.datetime.now(dt.UTC) - anchor).total_seconds() > (
            self.config.video_composer_render_timeout_seconds
            + self.config.video_composer_timeout_grace_seconds
        )

    @staticmethod
    def _audio_is_reusable(workflow: WorkflowExecucao) -> bool:
        return bool(
            workflow.status == "audio_concluido"
            and ProductionOrchestrationService._audio_artifact_is_reusable(workflow)
        )

    @staticmethod
    def _audio_artifact_is_reusable(workflow: WorkflowExecucao) -> bool:
        return bool(
            # A valid consolidated artifact remains reusable when alignment/ASR
            # could not validate it. The next Stage 17 phase owns that decision;
            # treating it as an audio-generation failure discards valid work and
            # creates a misleading active error.
            getattr(workflow, "audio_phase", "completed") in {"completed", "quality_unvalidated"}
            and workflow.audio_final_path
            and workflow.audio_final_sha256
            and workflow.duracao_audio_segundos
            and workflow.duracao_audio_segundos > 0
        )

    @staticmethod
    def _cancelled(workflow: WorkflowExecucao) -> bool:
        return workflow.status == "cancelada" or workflow.production_status == "cancelled"

    @staticmethod
    def _safe_message(exc: Exception) -> str:
        if isinstance(exc, VideoComposerError):
            return str(exc)
        return type(exc).__name__
