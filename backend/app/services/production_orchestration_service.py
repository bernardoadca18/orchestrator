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
from app.services.production_timeline_service import ProductionTimelineService
from app.services.visual_director_service import VisualDirectorService
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
                    timeline, _ = await self.timelines.ensure(workflow.id, force=False)
                    if timeline.status == "completed" and timeline.gap_count == 0:
                        await self._advance(workflow, "render")
                        continue
                    if timeline.status == "completed_with_gaps" or timeline.gap_count > 0:
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
                    if timeline is None or timeline.status != "completed" or timeline.gap_count:
                        await self._block(
                            workflow,
                            "render",
                            "stale_render_input",
                            "Current render input is unavailable",
                        )
                        return OrchestrationResult(terminal=True)
                    diversity = timeline_visual_diversity(
                        timeline.placements,
                        self._visual_diversity_policy(),
                    )
                    if diversity.status == "failed":
                        await self._block(
                            workflow,
                            "render",
                            "visual_diversity_gate_failed",
                            "ProductionTimeline failed pre-render visual diversity gate: "
                            + ", ".join(diversity.reasons),
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
        if self._timed_out(render):
            await self.renders.mark_failed(
                render, "composer_timeout", "Video Composer render timed out"
            )
            await self._fail(workflow, "composer_timeout", "Video Composer render timed out")
            return OrchestrationResult(terminal=True)
        try:
            remote = await self._reconcile_or_submit(render, retry_failed=False)
            return await self._apply_remote(workflow, render, remote)
        except VideoComposerTransientError as exc:
            logger.warning(
                "transient Video Composer polling failure; scheduling bounded reconciliation",
                extra={
                    "workflow_id": str(workflow_id),
                    "render_id": str(render.id),
                    "error_category": type(exc).__name__,
                },
            )
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
        phase = workflow.production_phase
        if workflow.production_status is None:
            phase = await self._earliest_missing_phase(workflow)
        await self.workflows.atualizar_producao(
            workflow.id, status="queued", phase=phase or "audio"
        )

    async def current_render(self, workflow_id: uuid.UUID) -> WorkflowRender | None:
        workflow = await self.workflows.obter_por_id(workflow_id)
        if workflow is None or workflow.production_phase not in {"render", "completed"}:
            return None
        timeline = await self.timelines.current(workflow_id)
        if timeline is None:
            return None
        fingerprint = self.render_request_fingerprint(workflow_id, timeline)
        return await self.renders.get_by_request_fingerprint(fingerprint)

    async def _ensure_local_render(
        self, workflow: WorkflowExecucao, timeline: ProductionTimeline
    ) -> WorkflowRender:
        fingerprint = self.render_request_fingerprint(workflow.id, timeline)
        key = f"neovids-render:{workflow.id}:{fingerprint[:32]}"
        render, _ = await self.renders.create_or_get(
            workflow_execucao_id=workflow.id,
            production_timeline_id=timeline.id,
            production_timeline_fingerprint=timeline.fingerprint,
            render_profile=self.config.video_composer_render_profile,
            mode="final",
            request_fingerprint=fingerprint,
            idempotency_key=key,
            status="pending",
            phase="pending",
        )
        return render

    def render_request_fingerprint(
        self, workflow_id: uuid.UUID, timeline: ProductionTimeline
    ) -> str:
        payload = {
            "contract": "stage_17_workflow_render_v1",
            "workflow_id": str(workflow_id),
            "production_timeline_id": str(timeline.id),
            "production_timeline_fingerprint": timeline.fingerprint,
            "render_profile": self.config.video_composer_render_profile,
            "mode": "final",
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _visual_diversity_policy(self) -> VisualDiversityPolicy:
        return VisualDiversityPolicy(
            version=self.config.visual_diversity_policy_version,
            minimum_window_count=self.config.visual_diversity_minimum_window_count,
            maximum_reuse_ratio=self.config.visual_diversity_maximum_reuse_ratio,
            maximum_dominant_window_ratio=(
                self.config.visual_diversity_maximum_dominant_window_ratio
            ),
        )

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
        if timeline is None or timeline.status not in {"completed", "completed_with_gaps"}:
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

    @staticmethod
    def _audio_is_reusable(workflow: WorkflowExecucao) -> bool:
        return bool(
            workflow.status == "audio_concluido"
            # A valid consolidated artifact remains reusable when alignment/ASR
            # could not validate it. The next Stage 17 phase owns that decision;
            # treating it as an audio-generation failure discards valid work and
            # creates a misleading active error.
            and getattr(workflow, "audio_phase", "completed")
            in {"completed", "quality_unvalidated"}
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
