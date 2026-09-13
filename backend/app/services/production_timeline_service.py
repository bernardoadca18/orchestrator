"""Stage 15 orchestration for a persisted deterministic edit-decision plan."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from app.core.config import Settings, settings
from app.models.workflow import (
    NarrationAlignment,
    ProductionTimeline,
    TimelineGap,
    TimelinePlacement,
    VisualDecision,
    VisualPlan,
    WorkflowExecucao,
)
from app.repositories.production_timeline_repository import ProductionTimelineRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.production_timeline_algorithm import (
    DecisionInput,
    SourceWindow,
    StaticImageSource,
    TimelinePlanningError,
    TimelinePolicy,
    build_production_timeline,
    seconds_to_us,
    us_to_seconds,
)
from app.services.visual_director_service import VisualDirectorService

logger = logging.getLogger(__name__)


class ProductionTimelineError(RuntimeError):
    pass


class ProductionTimelineNotFoundError(ProductionTimelineError):
    pass


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _uuid(value: Any, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise TimelinePlanningError(f"{field_name} must be a UUID") from exc


def _safe_source_provenance(payload: dict[str, Any], selection_strategy: str) -> dict[str, Any]:
    keys = {
        "format_version",
        "temporal_index_id",
        "retrieval_candidate_id",
        "semantic_score",
        "selection_score",
        "rank",
        "matched_note_ids",
        "media_search_strategy",
        "duration_fit_ratio",
        "image_semantic_index_id",
        "semantic_index",
        "fusion_score",
        "fusion_version",
    }
    result = {key: payload[key] for key in sorted(keys & payload.keys())}
    result["stage14_selection_strategy"] = selection_strategy
    return result


class ProductionTimelineService:
    def __init__(
        self,
        session,
        config: Settings = settings,
        *,
        workflow_repository: Any | None = None,
        alignment_service: Any | None = None,
        visual_director_service: Any | None = None,
        timeline_repository: Any | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.workflows = workflow_repository or WorkflowRepository(session)
        self.alignments = alignment_service or NarrationAlignmentService(session, config=config)
        self.visual_director = visual_director_service or VisualDirectorService(
            session, config=config
        )
        self.repository = timeline_repository or ProductionTimelineRepository(session)

    def _is_hybrid(self, visual_plan: VisualPlan | None) -> bool:
        return bool(
            visual_plan
            and visual_plan.strategy_version
            == self.config.visual_director_hybrid_strategy_version
        )

    def _versions(self, hybrid: bool) -> tuple[str, str, str]:
        if hybrid:
            return (
                self.config.production_timeline_hybrid_strategy_version,
                self.config.production_timeline_hybrid_schema_version,
                self.config.production_timeline_hybrid_validator_version,
            )
        return (
            self.config.production_timeline_strategy_version,
            self.config.production_timeline_schema_version,
            self.config.production_timeline_validator_version,
        )

    def _policy(self, *, hybrid: bool = False) -> TimelinePolicy:
        return TimelinePolicy(
            minimum_visual_duration_us=seconds_to_us(
                self.config.production_timeline_min_visual_seconds,
                "production timeline minimum visual duration",
            ),
            maximum_sources_per_query=(
                self.config.production_timeline_hybrid_max_sources_per_query
                if hybrid
                else self.config.production_timeline_max_sources_per_query
            ),
            maximum_visual_queries=self.config.visual_director_max_queries,
            gap_fill_enabled=self.config.production_timeline_gap_fill_enabled,
            coalesce_contiguous=self.config.production_timeline_coalesce_contiguous,
            hybrid_enabled=hybrid,
            static_image_minimum_hold_us=seconds_to_us(
                self.config.static_image_minimum_hold_seconds
            ),
            static_image_preferred_hold_us=seconds_to_us(
                self.config.static_image_preferred_hold_seconds
            ),
            static_image_maximum_hold_us=seconds_to_us(
                self.config.static_image_maximum_hold_seconds
            ),
            static_image_maximum_consecutive_us=seconds_to_us(
                self.config.static_image_maximum_consecutive_seconds
            ),
        )

    def _identity(
        self,
        workflow: WorkflowExecucao,
        alignment: NarrationAlignment | None,
        visual_plan: VisualPlan | None,
        policy: TimelinePolicy,
        *,
        hybrid: bool,
    ) -> tuple[str, str]:
        script_fingerprint = (
            visual_plan.script_fingerprint
            if visual_plan is not None
            else _canonical_hash(workflow.roteiro_snapshot)
        )
        strategy_version, schema_version, validator_version = self._versions(hybrid)
        payload = {
            "contract": (
                "stage_15_production_timeline_identity_v2"
                if hybrid
                else "stage_15_production_timeline_identity_v1"
            ),
            "workflow_id": str(workflow.id),
            "final_audio_sha256": workflow.audio_final_sha256,
            "final_audio_duration_seconds": workflow.duracao_audio_segundos,
            "narration_alignment_id": str(alignment.id) if alignment else None,
            "narration_alignment_fingerprint": alignment.fingerprint if alignment else None,
            "visual_plan_id": str(visual_plan.id) if visual_plan else None,
            "visual_plan_fingerprint": visual_plan.fingerprint if visual_plan else None,
            "script_fingerprint": script_fingerprint,
            "strategy_version": strategy_version,
            "schema_version": schema_version,
            "validator_version": validator_version,
            "policy": policy.to_dict(),
        }
        return _canonical_hash(payload), script_fingerprint

    async def _upstream(
        self, workflow_id: uuid.UUID
    ) -> tuple[WorkflowExecucao, NarrationAlignment | None, VisualPlan | None]:
        workflow = await self.workflows.obter_por_id(workflow_id, com_blocos=True)
        if workflow is None:
            raise ProductionTimelineNotFoundError("Workflow não encontrado")
        alignment = await self.alignments.current(workflow_id)
        visual_plan = await self.visual_director.current(workflow_id)
        return workflow, alignment, visual_plan

    async def current(self, workflow_id: uuid.UUID) -> ProductionTimeline | None:
        workflow, alignment, visual_plan = await self._upstream(workflow_id)
        hybrid = self._is_hybrid(visual_plan)
        policy = self._policy(hybrid=hybrid)
        request_fingerprint, _ = self._identity(
            workflow, alignment, visual_plan, policy, hybrid=hybrid
        )
        return await self.repository.get_by_request_fingerprint(request_fingerprint)

    def _prerequisite_error(
        self,
        workflow: WorkflowExecucao,
        alignment: NarrationAlignment | None,
        visual_plan: VisualPlan | None,
    ) -> str | None:
        if (
            workflow.status != "audio_concluido"
            or not workflow.audio_final_sha256
            or workflow.duracao_audio_segundos is None
            or workflow.duracao_audio_segundos <= 0
        ):
            return "Completed final narration with a positive duration is required"
        if alignment is None or alignment.status != "completed":
            return "Compatible completed NarrationAlignment is required"
        if alignment.final_audio_sha256 != workflow.audio_final_sha256:
            return "NarrationAlignment audio identity is stale"
        if visual_plan is None or visual_plan.status not in {
            "completed",
            "completed_with_gaps",
        }:
            return "Compatible completed VisualPlan is required"
        if visual_plan.narration_alignment_id != alignment.id:
            return "VisualPlan belongs to another NarrationAlignment"
        if visual_plan.workflow_execucao_id != workflow.id:
            return "VisualPlan belongs to another workflow"
        return None

    def _source_window(
        self, payload: dict[str, Any], selection_strategy: str, *, hybrid: bool
    ) -> SourceWindow | StaticImageSource:
        source_type = payload.get("source_type", "gameplay")
        if source_type == "static_image":
            if not hybrid:
                raise TimelinePlanningError("static image requires hybrid timeline V2")
            forbidden = (
                "session_id",
                "source_start_seconds",
                "source_end_seconds",
                "candidate_start_seconds",
                "candidate_end_seconds",
            )
            if any(payload.get(key) is not None for key in forbidden):
                raise TimelinePlanningError("static image cannot carry temporal fields")
            sha256 = str(payload.get("sha256", ""))
            content_type = str(payload.get("content_type", ""))
            if len(sha256) != 64 or content_type not in {
                "image/jpeg",
                "image/png",
                "image/webp",
            }:
                raise TimelinePlanningError("static image integrity metadata is invalid")
            return StaticImageSource(
                project_id=_uuid(payload.get("project_id"), "source.project_id"),
                media_id=_uuid(payload.get("media_id"), "source.media_id"),
                image_semantic_index_id=_uuid(
                    payload.get("image_semantic_index_id"),
                    "source.image_semantic_index_id",
                ),
                sha256=sha256,
                byte_size=int(payload.get("byte_size", -1)),
                content_type=content_type,
                width=int(payload.get("width", 0)),
                height=int(payload.get("height", 0)),
                selection_strategy=selection_strategy,
                provenance={
                    **_safe_source_provenance(payload, selection_strategy),
                    **(
                        payload.get("provenance", {})
                        if isinstance(payload.get("provenance"), dict)
                        else {}
                    ),
                },
            )
        allowed_video_type = "video_segment" if hybrid else "gameplay"
        if source_type != allowed_video_type:
            raise TimelinePlanningError(
                f"timeline source type must be {allowed_video_type}"
            )
        raw_start = payload.get("source_start_seconds")
        raw_end = payload.get("source_end_seconds")
        if raw_start is None or raw_end is None:
            raise TimelinePlanningError("Stage 14 source window is missing a range")
        start = seconds_to_us(raw_start, "source_start_seconds")
        end = seconds_to_us(raw_end, "source_end_seconds")
        source = SourceWindow(
            project_id=_uuid(payload.get("project_id"), "source.project_id"),
            session_id=_uuid(payload.get("session_id"), "source.session_id"),
            media_id=_uuid(payload.get("media_id"), "source.media_id"),
            start_us=start,
            end_us=end,
            selection_strategy=selection_strategy,
            provenance={
                **_safe_source_provenance(payload, selection_strategy),
                **(
                    payload.get("provenance", {})
                    if isinstance(payload.get("provenance"), dict)
                    else {}
                ),
            },
        )
        if source.start_us < 0 or source.end_us <= source.start_us:
            raise TimelinePlanningError("Stage 14 source window is invalid")
        return source

    def _decision_input(self, decision: VisualDecision, *, hybrid: bool) -> DecisionInput:
        selected = (
            self._source_window(
                decision.selected_source,
                decision.selection_strategy or "unknown",
                hybrid=hybrid,
            )
            if decision.selected_source
            else None
        )
        alternatives = tuple(
            self._source_window(item, "media_search_alternative", hybrid=hybrid)
            for item in decision.alternatives[
                : (
                    self.config.production_timeline_hybrid_max_sources_per_query
                    if hybrid
                    else self.config.production_timeline_max_sources_per_query
                )
            ]
        )
        return DecisionInput(
            decision_id=decision.id,
            visual_query_id=decision.visual_query_id,
            block_order=decision.block_order,
            query_order=decision.query_order,
            importance=decision.importance,
            binding_status=decision.narration_binding_status,
            source_status=decision.source_resolution_status,
            timeline_start_us=seconds_to_us(
                decision.narration_start_seconds, "narration_start_seconds"
            )
            if decision.narration_start_seconds is not None
            else None,
            timeline_end_us=seconds_to_us(decision.narration_end_seconds, "narration_end_seconds")
            if decision.narration_end_seconds is not None
            else None,
            selected=selected,
            alternatives=alternatives,
        )

    def _static_presentation(
        self, source: StaticImageSource, placement_index: int
    ) -> dict[str, Any]:
        aspect_ratio = source.width / source.height
        contained = not 1.4 <= aspect_ratio <= 2.1
        motions = ("slow_zoom_in", "slow_zoom_out", "none")
        return {
            "policy_version": self.config.static_image_presentation_policy_version,
            "fit": "contain" if contained else "cover",
            "background": "blurred_fill" if contained else "black",
            "motion": motions[placement_index % len(motions)],
            "zoom_amount": 0.04,
        }

    def _placement_model(
        self,
        timeline: ProductionTimeline,
        item,
        index: int,
        *,
        hybrid: bool,
    ) -> TimelinePlacement:
        static = isinstance(item.source, StaticImageSource)
        source_type = "static_image" if static else "video_segment" if hybrid else "gameplay"
        source_start = (
            us_to_seconds(item.source_start_us)
            if item.source_start_us is not None
            else None
        )
        source_end = (
            us_to_seconds(item.source_end_us) if item.source_end_us is not None else None
        )
        identity_tail = (
            f"image:{item.source.image_semantic_index_id}"
            if static
            else f"video:{source_start}:{source_end}"
        )
        presentation = self._static_presentation(item.source, index) if static else None
        provenance = dict(item.source_provenance)
        if static:
            provenance.update(
                {
                    "expected_sha256": item.source.sha256,
                    "expected_byte_size": item.source.byte_size,
                    "content_type": item.source.content_type,
                    "width": item.source.width,
                    "height": item.source.height,
                    "image_semantic_index_id": str(item.source.image_semantic_index_id),
                }
            )
        return TimelinePlacement(
            id=uuid.uuid5(
                timeline.id,
                "placement:"
                f"{index}:{item.timeline_start_us}:{item.timeline_end_us}:"
                f"{item.source.media_id}:{identity_tail}",
            ),
            production_timeline_id=timeline.id,
            order=index,
            visual_decision_id=item.visual_decision_id,
            visual_query_ids=[str(value) for value in item.visual_query_ids],
            visual_decision_ids=[str(value) for value in item.visual_decision_ids],
            source_type=source_type,
            source_project_id=item.source.project_id,
            source_session_id=(item.source.session_id if not static else None),
            source_media_id=item.source.media_id,
            source_image_semantic_index_id=(
                item.source.image_semantic_index_id if static else None
            ),
            timeline_start_seconds=us_to_seconds(item.timeline_start_us),
            timeline_end_seconds=us_to_seconds(item.timeline_end_us),
            candidate_start_seconds=(
                us_to_seconds(item.source.start_us) if not static else None
            ),
            candidate_end_seconds=(
                us_to_seconds(item.source.end_us) if not static else None
            ),
            source_start_seconds=source_start,
            source_end_seconds=source_end,
            playback_rate=None if static else item.playback_rate,
            presentation=presentation,
            transition=item.transition,
            strategy=item.strategy,
            importance=item.importance,
            source_provenance=provenance,
            diagnostics={
                **item.diagnostics,
                "allowed_candidate_ranges": [
                    {
                        "source_start_seconds": us_to_seconds(start),
                        "source_end_seconds": us_to_seconds(end),
                    }
                    for start, end in item.allowed_source_ranges
                ],
            },
        )

    async def ensure(
        self, workflow_id: uuid.UUID, *, force: bool = False
    ) -> tuple[ProductionTimeline, bool]:
        workflow, alignment, visual_plan = await self._upstream(workflow_id)
        hybrid = self._is_hybrid(visual_plan)
        policy = self._policy(hybrid=hybrid)
        strategy_version, schema_version, validator_version = self._versions(hybrid)
        request_fingerprint, script_fingerprint = self._identity(
            workflow, alignment, visual_plan, policy, hybrid=hybrid
        )
        timeline, created = await self.repository.create_or_get(
            workflow_execucao_id=workflow.id,
            narration_alignment_id=alignment.id if alignment else None,
            visual_plan_id=visual_plan.id if visual_plan else None,
            final_audio_sha256=workflow.audio_final_sha256,
            narration_sample_rate=workflow.audio_final_sample_rate,
            narration_channels=workflow.audio_final_channels,
            narration_byte_size=workflow.audio_final_byte_size,
            script_fingerprint=script_fingerprint,
            strategy_version=strategy_version,
            schema_version=schema_version,
            validator_version=validator_version,
            policy=policy.to_dict(),
            request_fingerprint=request_fingerprint,
            fingerprint=request_fingerprint,
            status="pending",
            duration_seconds=workflow.duracao_audio_segundos or 0.0,
        )
        if not created and not force:
            return timeline, False
        if force:
            await self.repository.clear_outputs(timeline)
        blocked_reason = self._prerequisite_error(workflow, alignment, visual_plan)
        if blocked_reason:
            await self.repository.mark_blocked(timeline, blocked_reason)
            return await self.repository.reload(timeline), created

        assert alignment is not None
        assert visual_plan is not None
        assert workflow.duracao_audio_segundos is not None
        await self.repository.mark_processing(timeline)
        started = time.perf_counter()
        try:
            result = build_production_timeline(
                workflow.duracao_audio_segundos,
                [self._decision_input(item, hybrid=hybrid) for item in visual_plan.decisions],
                policy,
            )
            placements = [
                self._placement_model(timeline, item, index, hybrid=hybrid)
                for index, item in enumerate(result.placements)
            ]
            gaps = [
                TimelineGap(
                    id=uuid.uuid5(
                        timeline.id,
                        f"gap:{index}:{item.timeline_start_us}:{item.timeline_end_us}:"
                        f"{item.reason}:{item.severity}",
                    ),
                    production_timeline_id=timeline.id,
                    order=index,
                    timeline_start_seconds=us_to_seconds(item.timeline_start_us),
                    timeline_end_seconds=us_to_seconds(item.timeline_end_us),
                    reason=item.reason,
                    severity=item.severity,
                    related_visual_query_ids=[
                        str(value) for value in item.related_visual_query_ids
                    ],
                    diagnostics=item.diagnostics,
                )
                for index, item in enumerate(result.gaps)
            ]
            outcomes = [item.to_dict() for item in result.outcomes]
            output_identity = {
                "request_fingerprint": request_fingerprint,
                "placements": [
                    {
                        "timeline": [item.timeline_start_seconds, item.timeline_end_seconds],
                        "source": [item.source_start_seconds, item.source_end_seconds],
                        "candidate": [item.candidate_start_seconds, item.candidate_end_seconds],
                        "source_type": item.source_type,
                        "media_id": str(item.source_media_id),
                        "image_semantic_index_id": (
                            str(item.source_image_semantic_index_id)
                            if item.source_image_semantic_index_id
                            else None
                        ),
                        "expected_sha256": item.source_provenance.get("expected_sha256"),
                        "presentation": item.presentation,
                        "queries": item.visual_query_ids,
                        "strategy": item.strategy,
                    }
                    for item in placements
                ],
                "gaps": [
                    {
                        "timeline": [item.timeline_start_seconds, item.timeline_end_seconds],
                        "reason": item.reason,
                        "severity": item.severity,
                        "queries": item.related_visual_query_ids,
                    }
                    for item in gaps
                ],
                "query_outcomes": outcomes,
            }
            planning_ms = round((time.perf_counter() - started) * 1000, 3)
            metrics = {
                "planning_time_ms": planning_ms,
                "visual_coverage_seconds": us_to_seconds(result.visual_coverage_us),
                "visual_gap_seconds": us_to_seconds(result.visual_gap_us),
                "visual_coverage_ratio": result.coverage_ratio,
                "placement_count": len(placements),
                "gap_count": len(gaps),
                "cut_count": result.cut_count,
                "average_shot_duration_seconds": result.average_shot_duration_seconds,
                "video_segment_count": sum(
                    item.source_type in {"gameplay", "video_segment"}
                    for item in placements
                ),
                "static_image_count": sum(
                    item.source_type == "static_image" for item in placements
                ),
                "required_gap_seconds": us_to_seconds(result.required_gap_us),
                "preferred_gap_seconds": us_to_seconds(result.preferred_gap_us),
                "optional_gap_seconds": us_to_seconds(result.optional_gap_us),
                "background_gap_seconds": us_to_seconds(result.background_gap_us),
            }
            diagnostics = {
                "narration_track": {
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": workflow.duracao_audio_segundos,
                    "audio_sha256": workflow.audio_final_sha256,
                },
                "query_placement_outcomes": outcomes,
                "range_semantics": "[start,end)",
                "timeline_time_semantics": (
                    "final_video_equals_final_narration_v2_hybrid"
                    if hybrid
                    else "final_video_equals_final_narration_v1"
                ),
                "renderer_decisions_deferred": ["fps", "resolution", "codec"],
            }
            await self.repository.replace_outputs(
                timeline,
                placements=placements,
                gaps=gaps,
                status=result.status,
                fingerprint=_canonical_hash(output_identity),
                coverage_seconds=us_to_seconds(result.visual_coverage_us),
                gap_seconds=us_to_seconds(result.visual_gap_us),
                coverage_ratio=result.coverage_ratio,
                required_gap_seconds=us_to_seconds(result.required_gap_us),
                preferred_gap_seconds=us_to_seconds(result.preferred_gap_us),
                optional_gap_seconds=us_to_seconds(result.optional_gap_us),
                background_gap_seconds=us_to_seconds(result.background_gap_us),
                cut_count=result.cut_count,
                unplaced_query_count=sum(
                    item.status not in {"fully_placed", "not_applicable"}
                    for item in result.outcomes
                ),
                average_shot_duration_seconds=result.average_shot_duration_seconds,
                metrics=metrics,
                diagnostics=diagnostics,
            )
            timeline = await self.repository.reload(timeline)
            logger.info(
                "production timeline finished",
                extra={
                    "workflow_id": str(workflow.id),
                    "production_timeline_id": str(timeline.id),
                    "strategy_version": timeline.strategy_version,
                    "status": timeline.status,
                    "duration_seconds": timeline.duration_seconds,
                    "placement_count": timeline.placement_count,
                    "gap_count": timeline.gap_count,
                    "coverage_ratio": timeline.visual_coverage_ratio,
                    "planning_time_ms": planning_ms,
                },
            )
            return timeline, created
        except TimelinePlanningError as exc:
            await self.repository.mark_failed(timeline, str(exc))
        except Exception:
            logger.exception(
                "production timeline failed",
                extra={"workflow_id": str(workflow.id), "timeline_id": str(timeline.id)},
            )
            await self.repository.mark_failed(timeline, "Production Timeline planning failed")
        return await self.repository.reload(timeline), created
