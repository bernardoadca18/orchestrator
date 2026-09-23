"""Stage 18 deterministic QA orchestration; reads artifacts and writes only its report."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.video_composer_client import (
    ComposerRenderProfile,
    VideoComposerClient,
    VideoComposerError,
    VideoComposerTransientError,
)
from app.core.config import Settings, settings
from app.models.workflow import (
    NarrationAlignment,
    ProductionQAReport,
    ProductionTimeline,
    TranscricaoAudio,
    VisualPlan,
    WorkflowExecucao,
    WorkflowRender,
)
from app.repositories.narration_alignment_repository import NarrationAlignmentRepository
from app.repositories.production_qa_repository import ProductionQARepository
from app.repositories.production_timeline_repository import ProductionTimelineRepository
from app.repositories.visual_plan_repository import VisualPlanRepository
from app.repositories.workflow_render_repository import WorkflowRenderRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.production_qa_checks import (
    ProductionQACheck,
    ProductionQAInputs,
    aggregate_checks,
    deep_ffprobe_check,
    deep_sha_check,
    delivery_checks,
    standard_local_checks,
)
from app.services.production_qa_policy import ProductionQAPolicy, resolve_qa_policy
from app.services.visual_diversity import VisualDiversityPolicy

logger = logging.getLogger(__name__)

QA_STRATEGY_VERSION = "production_qa_v1"
MISSING_SHA256 = "0" * 64


class ProductionQANotFoundError(LookupError):
    pass


class ProductionQAPreconditionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EnsureProductionQAResult:
    report: ProductionQAReport
    created: bool


@dataclass(frozen=True, slots=True)
class _Upstream:
    workflow: WorkflowExecucao
    transcription: TranscricaoAudio | None
    alignment: NarrationAlignment | None
    visual_plan: VisualPlan | None
    timeline: ProductionTimeline
    render: WorkflowRender
    policy: ProductionQAPolicy
    fingerprint: str


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class ProductionQAService:
    def __init__(
        self,
        session: AsyncSession,
        config: Settings = settings,
        *,
        workflow_repository: Any | None = None,
        qa_repository: Any | None = None,
        alignment_service: Any | None = None,
        visual_service: Any | None = None,
        timeline_service: Any | None = None,
        orchestration_service: Any | None = None,
        composer_client_factory: Callable[[], VideoComposerClient] | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.workflows = workflow_repository or WorkflowRepository(session)
        self.reports = qa_repository or ProductionQARepository(session)
        self.alignments: Any = alignment_service or NarrationAlignmentRepository(session)
        self.visual: Any = visual_service or VisualPlanRepository(session)
        self.timelines: Any = timeline_service or ProductionTimelineRepository(session)
        self.orchestration: Any = orchestration_service or WorkflowRenderRepository(session)
        self.composer_client_factory = composer_client_factory or (
            lambda: VideoComposerClient(config)
        )

    async def current(
        self, workflow_id: uuid.UUID, mode: Literal["standard", "deep"] = "standard"
    ) -> ProductionQAReport | None:
        upstream = await self._load_upstream(workflow_id, mode)
        return await self.reports.get_by_fingerprint(upstream.fingerprint)

    async def ensure(
        self,
        workflow_id: uuid.UUID,
        *,
        mode: Literal["standard", "deep"] = "standard",
        force: bool = False,
    ) -> EnsureProductionQAResult:
        upstream = await self._load_upstream(workflow_id, mode)
        report, created = await self.reports.create_or_get(
            workflow_execucao_id=workflow_id,
            workflow_render_id=upstream.render.id,
            production_timeline_id=upstream.timeline.id,
            visual_plan_id=upstream.visual_plan.id if upstream.visual_plan else None,
            narration_alignment_id=upstream.alignment.id if upstream.alignment else None,
            final_audio_sha256=upstream.workflow.audio_final_sha256 or MISSING_SHA256,
            render_output_sha256=upstream.render.output_sha256 or MISSING_SHA256,
            qa_mode=mode,
            policy_name=upstream.policy.name,
            policy_version=upstream.policy.version,
            policy_identity=upstream.policy.identity,
            strategy_version=self.config.production_qa_strategy_version,
            request_fingerprint=upstream.fingerprint,
            status="pending",
            publication_ready=False,
            checks=[],
        )
        if not created and not force and report.status not in {"pending", "running"}:
            return EnsureProductionQAResult(report=report, created=False)

        await self.reports.mark_running(report)
        started = time.perf_counter()
        try:
            checks, metrics, dependency_unavailable = await self._run(upstream, mode)
            aggregate = aggregate_checks(checks)
            metrics["total_qa_time_ms"] = round((time.perf_counter() - started) * 1000, 3)
            error_category = "video_composer_unavailable" if dependency_unavailable else None
            error_message = (
                "Video Composer was unavailable during QA validation"
                if dependency_unavailable
                else None
            )
            await self.reports.complete(
                report,
                checks=checks,
                aggregate=aggregate,
                metrics=metrics,
                error_category=error_category,
                error_message=error_message,
            )
        except Exception as exc:
            logger.exception(
                "production QA execution failed",
                extra={"workflow_id": str(workflow_id), "qa_report_id": str(report.id)},
            )
            await self.reports.fail(report, "qa_execution_failure", type(exc).__name__)
        report = await self.reports.reload(report)
        logger.info(
            "production QA completed",
            extra={
                "workflow_id": str(workflow_id),
                "qa_report_id": str(report.id),
                "qa_mode": mode,
                "policy": report.policy_name,
                "strategy_version": report.strategy_version,
                "check_count": report.check_count,
                "warnings": report.warning_count,
                "failures": report.failed_count,
                "blocked": report.blocked_count,
                "publication_ready": report.publication_ready,
                "duration_ms": (report.metrics or {}).get("total_qa_time_ms"),
            },
        )
        return EnsureProductionQAResult(report=report, created=created)

    async def _load_upstream(
        self, workflow_id: uuid.UUID, mode: Literal["standard", "deep"]
    ) -> _Upstream:
        workflow = await self.workflows.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            raise ProductionQANotFoundError("Workflow não encontrado")
        if hasattr(self.orchestration, "current_render"):
            render = await self.orchestration.current_render(workflow_id)
        else:
            renders = await self.orchestration.list_for_workflow(workflow_id)
            render = next((item for item in renders if item.mode == "final"), None)
        if render is None:
            raise ProductionQAPreconditionError(
                "Current WorkflowRender is required before QA can be represented"
            )
        if hasattr(self.timelines, "get_by_id"):
            timeline = await self.timelines.get_by_id(render.production_timeline_id)
        else:
            timeline = await self.timelines.current(workflow_id)
        if timeline is None:
            raise ProductionQAPreconditionError("WorkflowRender ProductionTimeline is unavailable")
        if timeline.visual_plan_id is None:
            visual_plan = None
        elif hasattr(self.visual, "get_by_id"):
            visual_plan = await self.visual.get_by_id(timeline.visual_plan_id)
        else:
            visual_plan = await self.visual.current(workflow_id)
        alignment_id = timeline.narration_alignment_id or (
            visual_plan.narration_alignment_id if visual_plan is not None else None
        )
        if alignment_id is None:
            alignment = None
        elif hasattr(self.alignments, "get_by_id"):
            alignment = await self.alignments.get_by_id(alignment_id, eager=True)
        else:
            alignment = await self.alignments.current(workflow_id)
        transcription = getattr(workflow, "transcricao", None)
        policy = resolve_qa_policy(
            workflow.perfil_producao,
            getattr(timeline, "schema_version", None),
            static_image_minimum_hold_seconds=(self.config.static_image_minimum_hold_seconds),
            static_image_maximum_hold_seconds=(self.config.static_image_maximum_hold_seconds),
            static_image_maximum_consecutive_seconds=(
                self.config.static_image_maximum_consecutive_seconds
            ),
        )
        fingerprint = self._request_fingerprint(
            workflow,
            alignment,
            visual_plan,
            timeline,
            render,
            mode,
            policy,
        )
        return _Upstream(
            workflow=workflow,
            transcription=transcription,
            alignment=alignment,
            visual_plan=visual_plan,
            timeline=timeline,
            render=render,
            policy=policy,
            fingerprint=fingerprint,
        )

    def _request_fingerprint(
        self,
        workflow: WorkflowExecucao,
        alignment: NarrationAlignment | None,
        plan: VisualPlan | None,
        timeline: ProductionTimeline,
        render: WorkflowRender,
        mode: str,
        policy: ProductionQAPolicy,
    ) -> str:
        return _canonical_hash(
            {
                "contract": "production_qa_report_v1",
                "workflow_id": str(workflow.id),
                "script_identity": _canonical_hash(workflow.roteiro_snapshot),
                "final_audio_sha256": workflow.audio_final_sha256,
                "narration_alignment_id": str(alignment.id) if alignment else None,
                "narration_alignment_fingerprint": alignment.fingerprint if alignment else None,
                "visual_plan_id": str(plan.id) if plan else None,
                "visual_plan_fingerprint": plan.fingerprint if plan else None,
                "production_timeline_id": str(timeline.id),
                "production_timeline_fingerprint": timeline.fingerprint,
                "workflow_render_id": str(render.id),
                "render_output_sha256": render.output_sha256,
                "render_profile": render.render_profile,
                "render_profile_identity": render.render_profile_identity,
                "qa_mode": mode,
                "qa_policy_identity": policy.identity,
                "strategy_version": self.config.production_qa_strategy_version,
            }
        )

    async def _run(
        self, upstream: _Upstream, mode: Literal["standard", "deep"]
    ) -> tuple[list[ProductionQACheck], dict[str, object], bool]:
        metrics: dict[str, object] = {}
        dependency_unavailable = False
        profile: ComposerRenderProfile | None = None
        profile_unavailable = False
        local_started = time.perf_counter()

        async with self.composer_client_factory() as composer:
            try:
                profiles = await composer.get_render_profiles()
                profile = next(
                    (item for item in profiles if item.name == upstream.render.render_profile),
                    None,
                )
            except VideoComposerTransientError:
                profile_unavailable = True
                dependency_unavailable = True
            except VideoComposerError:
                profile = None

            inputs = ProductionQAInputs(
                workflow=upstream.workflow,
                transcription=upstream.transcription,
                alignment=upstream.alignment,
                visual_plan=upstream.visual_plan,
                timeline=upstream.timeline,
                render=upstream.render,
                policy=upstream.policy,
                render_profile=profile,
                visual_diversity_policy=VisualDiversityPolicy(
                    version=(
                        self.config.visual_diversity_hybrid_policy_version
                        if getattr(upstream.timeline, "schema_version", None)
                        == self.config.production_timeline_hybrid_schema_version
                        else self.config.visual_diversity_policy_version
                    ),
                    minimum_window_count=self.config.visual_diversity_minimum_window_count,
                    maximum_reuse_ratio=self.config.visual_diversity_maximum_reuse_ratio,
                    maximum_dominant_window_ratio=(
                        self.config.visual_diversity_maximum_dominant_window_ratio
                    ),
                    maximum_dominant_static_ratio=(
                        self.config.visual_diversity_maximum_dominant_static_ratio
                    ),
                    maximum_static_reuse_count=(
                        self.config.visual_diversity_maximum_static_reuse_count
                    ),
                    maximum_consecutive_static_seconds=(
                        self.config.static_image_maximum_consecutive_seconds
                    ),
                ),
                profile_unavailable=profile_unavailable,
            )
            checks = standard_local_checks(inputs)
            from app.services.production_capabilities import capabilities_for
            if capabilities_for(upstream.workflow)["visual_mode"] == "storyboard":
                from app.services.production_orchestration_service import ProductionOrchestrationService
                errors = await ProductionOrchestrationService(self.session, self.config)._validate_static_image_assets(upstream.timeline)
                checks.append(ProductionQACheck("storyboard_asset_registry", "storyboard", "failed" if errors else "passed", "Media Indexer image identities verified", actual=errors))
            metrics["local_check_time_ms"] = round((time.perf_counter() - local_started) * 1000, 3)
            range_started = time.perf_counter()
            if upstream.policy.validate_video_delivery:
                if profile_unavailable:
                    checks.extend(delivery_checks(unavailable=True))
                else:
                    try:
                        checks.extend(await self._range_checks(composer, upstream.render))
                    except VideoComposerTransientError:
                        checks.extend(delivery_checks(unavailable=True))
                        dependency_unavailable = True
                    except VideoComposerError:
                        checks.extend(delivery_checks())
            else:
                checks.extend(
                    [
                        ProductionQACheck(
                            "final_video_accessible",
                            "delivery",
                            "skipped",
                            "Video delivery validation is disabled.",
                        ),
                        ProductionQACheck(
                            "final_video_range_support",
                            "delivery",
                            "skipped",
                            "Video delivery validation is disabled.",
                        ),
                    ]
                )
            metrics["range_check_time_ms"] = round((time.perf_counter() - range_started) * 1000, 3)

            if mode == "deep":
                deep_checks, deep_metrics, deep_unavailable = await self._deep_checks(
                    composer, inputs
                )
                checks.extend(deep_checks)
                metrics.update(deep_metrics)
                dependency_unavailable = dependency_unavailable or deep_unavailable
        return checks, metrics, dependency_unavailable

    async def _range_checks(
        self, composer: VideoComposerClient, render: WorkflowRender
    ) -> list[ProductionQACheck]:
        if render.composer_render_id is None:
            return delivery_checks()
        response = await composer.open_video(
            str(render.composer_render_id), range_header="bytes=0-0"
        )
        count = 0
        try:
            async for chunk in response.aiter_bytes():
                count += len(chunk)
                if count > 1:
                    break
            return delivery_checks(
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                content_range=response.headers.get("content-range"),
                accept_ranges=response.headers.get("accept-ranges"),
                byte_count=count,
            )
        finally:
            await response.aclose()

    async def _deep_checks(
        self, composer: VideoComposerClient, inputs: ProductionQAInputs
    ) -> tuple[list[ProductionQACheck], dict[str, object], bool]:
        render = inputs.render
        limit = self.config.production_qa_deep_max_video_bytes
        if render.output_byte_size is None or render.output_byte_size > limit:
            return (
                [
                    ProductionQACheck(
                        "deep_content_size",
                        "delivery",
                        "blocked",
                        "Rendered MP4 exceeds the configured deep-QA size limit."
                        if render.output_byte_size is not None
                        else "Rendered MP4 size is unavailable for deep-QA safety.",
                        expected={"maximum_bytes": limit},
                        actual=render.output_byte_size,
                    ),
                    ProductionQACheck(
                        "full_content_sha",
                        "delivery",
                        "blocked",
                        "Full-content hash was not attempted because deep QA is size-blocked.",
                    ),
                    ProductionQACheck(
                        "deep_ffprobe_validation",
                        "render",
                        "blocked",
                        "Independent ffprobe was not attempted because deep QA is size-blocked.",
                    ),
                ],
                {},
                False,
            )
        if render.composer_render_id is None:
            return self._deep_dependency_blocked("Completed Composer render identity is missing")

        descriptor, temporary_name = tempfile.mkstemp(prefix="neovids-qa-", suffix=".mp4")
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            download_started = time.perf_counter()
            hash_seconds = 0.0
            digest = hashlib.sha256()
            byte_count = 0
            try:
                response = await composer.open_video(str(render.composer_render_id))
                try:
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            byte_count += len(chunk)
                            if byte_count > limit:
                                return (
                                    [
                                        ProductionQACheck(
                                            "deep_content_size",
                                            "delivery",
                                            "blocked",
                                            "Stream exceeded the configured deep-QA size limit.",
                                            expected={"maximum_bytes": limit},
                                            actual=byte_count,
                                        ),
                                        ProductionQACheck(
                                            "full_content_sha",
                                            "delivery",
                                            "blocked",
                                            "Full-content hash was aborted by the size guard.",
                                        ),
                                        ProductionQACheck(
                                            "deep_ffprobe_validation",
                                            "render",
                                            "blocked",
                                            "Independent ffprobe was skipped after the size guard.",
                                        ),
                                    ],
                                    {},
                                    False,
                                )
                            hash_started = time.perf_counter()
                            digest.update(chunk)
                            hash_seconds += time.perf_counter() - hash_started
                            await asyncio.to_thread(output.write, chunk)
                finally:
                    await response.aclose()
            except VideoComposerTransientError:
                return self._deep_dependency_blocked("Video Composer is unavailable for deep QA")
            except VideoComposerError:
                return (
                    [
                        ProductionQACheck(
                            "full_content_sha",
                            "delivery",
                            "failed",
                            "Rendered MP4 could not be streamed for deep QA.",
                        ),
                        ProductionQACheck(
                            "deep_ffprobe_validation",
                            "render",
                            "blocked",
                            "Independent ffprobe had no complete MP4 input.",
                        ),
                    ],
                    {},
                    False,
                )
            download_ms = round((time.perf_counter() - download_started) * 1000, 3)
            checks = [
                ProductionQACheck(
                    "deep_content_size",
                    "delivery",
                    "passed",
                    "Rendered MP4 is within the configured deep-QA size limit.",
                    expected={"maximum_bytes": limit},
                    actual=byte_count,
                ),
                deep_sha_check(render.output_sha256, digest.hexdigest(), byte_count),
            ]
            probe_started = time.perf_counter()
            probe, probe_error = await self._ffprobe(temporary)
            ffprobe_ms = round((time.perf_counter() - probe_started) * 1000, 3)
            if probe is None:
                checks.append(
                    ProductionQACheck(
                        "deep_ffprobe_validation",
                        "render",
                        "blocked" if probe_error == "ffprobe_unavailable" else "failed",
                        "ffprobe is unavailable in the QA runtime."
                        if probe_error == "ffprobe_unavailable"
                        else "ffprobe could not independently validate the rendered MP4.",
                        actual=probe_error,
                    )
                )
            else:
                checks.append(deep_ffprobe_check(probe, inputs))
            metrics: dict[str, object] = {
                "deep_download_time_ms": download_ms,
                "deep_sha_time_ms": round(hash_seconds * 1000, 3),
                "deep_ffprobe_time_ms": ffprobe_ms,
                "deep_streamed_bytes": byte_count,
            }
            return checks, metrics, False
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _deep_dependency_blocked(
        message: str,
    ) -> tuple[list[ProductionQACheck], dict[str, object], bool]:
        return (
            [
                ProductionQACheck("full_content_sha", "delivery", "blocked", message),
                ProductionQACheck(
                    "deep_ffprobe_validation",
                    "render",
                    "blocked",
                    "Independent ffprobe had no complete MP4 input.",
                ),
            ],
            {},
            True,
        )

    @staticmethod
    async def _ffprobe(path: Path) -> tuple[dict[str, Any] | None, str | None]:
        executable = shutil.which("ffprobe")
        if executable is None:
            return None, "ffprobe_unavailable"
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                os.fspath(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
        except (OSError, TimeoutError):
            return None, "ffprobe_execution_failed"
        if process.returncode != 0:
            return None, "ffprobe_execution_failed"
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "ffprobe_invalid_json"
        return payload if isinstance(payload, dict) else None, None
