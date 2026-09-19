"""Stage 14 deterministic Visual Director orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field, replace
from itertools import product
from typing import Any

from app.clients.media_indexer_search import (
    ImageSearchFilters,
    ImageSearchResultSet,
    MediaIndexerConflictError,
    MediaIndexerError,
    MediaIndexerSearchProvider,
    MediaSearchProvider,
    MediaSearchResultSet,
    MediaSessionMetadata,
)
from app.core.config import Settings, settings
from app.models.workflow import (
    NarrationAlignment,
    VisualDecision,
    VisualPlan,
    VisualQueryNarrationBinding,
    WorkflowExecucao,
)
from app.repositories.visual_plan_repository import VisualPlanRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.workflow import (
    VisualGameplaySourceSelectionOut,
    VisualStaticImageSourceSelectionOut,
    VisualVideoSegmentSourceSelectionOut,
)
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.visual_director_algorithm import (
    HybridVisualCandidate,
    StaticImageCandidate,
    VisualCandidate,
    direct_candidate,
    extract_game_from_query,
    filter_candidates_by_game,
    fuse_hybrid_candidates,
    normalize_query,
    overlap_ratio,
    rank_candidates,
)
from app.services.visual_diversity import (
    VisualDiversityPolicy,
    VisualSourceWindow,
    evaluate_visual_diversity,
)

__all__ = [
    "ProductionProfileConfig",
    "PRODUCTION_PROFILE_CONFIGS",
    "resolve_production_profile_config",
    "FINAL_VISUAL_PRODUCTION_PROFILES",
    "VisualDirectorService",
]

logger = logging.getLogger(__name__)

VISUAL_QUERIES_MISSING_CATEGORY = "visual_queries_missing"
VISUAL_QUERIES_MISSING_MESSAGE = (
    "Final visual production requires structured VisualQueries for non-empty narration"
)
@dataclass(frozen=True, slots=True)
class ProductionProfileConfig:
    """Production profile configuration flags."""

    profile_id: str
    burned_in_captions: bool = True


PRODUCTION_PROFILE_CONFIGS: dict[str, ProductionProfileConfig] = {
    "faceless_general": ProductionProfileConfig("faceless_general", burned_in_captions=True),
    "faceless_informational": ProductionProfileConfig(
        "faceless_informational", burned_in_captions=True
    ),
    "faceless_pop_culture": ProductionProfileConfig(
        "faceless_pop_culture", burned_in_captions=True
    ),
    "faceless_pop_culture_lists": ProductionProfileConfig(
        "faceless_pop_culture_lists", burned_in_captions=False
    ),
    "game_analysis": ProductionProfileConfig("game_analysis", burned_in_captions=True),
}


def resolve_production_profile_config(profile_id: str | None) -> ProductionProfileConfig | None:
    """Resolve the production profile configuration by ID."""

    if profile_id is None:
        return None
    return PRODUCTION_PROFILE_CONFIGS.get(profile_id)


FINAL_VISUAL_PRODUCTION_PROFILES = frozenset(PRODUCTION_PROFILE_CONFIGS.keys())


class VisualDirectorError(RuntimeError):
    pass


class VisualDirectorNotFoundError(VisualDirectorError):
    pass


class VisualDirectorInputError(VisualDirectorError):
    pass


@dataclass(frozen=True, slots=True)
class StaticImageScope:
    enabled: bool
    asset_classes: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    source_owners: tuple[str, ...] = ()
    source_classes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "asset_classes": list(self.asset_classes),
            "collections": list(self.collections),
            "source_owners": list(self.source_owners),
            "source_classes": list(self.source_classes),
        }


@dataclass(frozen=True, slots=True)
class MediaScope:
    project_id: uuid.UUID | None
    session_ids: tuple[uuid.UUID, ...]
    origin: str
    static_images: StaticImageScope | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "media_project_id": str(self.project_id) if self.project_id else None,
            "media_session_ids": [str(item) for item in self.session_ids] or None,
            "origin": self.origin,
        }
        if self.static_images is not None:
            result["static_images"] = self.static_images.to_dict()
        return result

    @property
    def hybrid_enabled(self) -> bool:
        return bool(self.project_id and self.static_images and self.static_images.enabled)


@dataclass(frozen=True, slots=True)
class QueryContext:
    id: uuid.UUID
    block_order: int
    query_order: int
    query: str
    intent: str
    importance: str
    source_note_id: uuid.UUID | None
    preferred_session_id: uuid.UUID | None
    preferred_start_seconds: float | None
    preferred_end_seconds: float | None
    desired_duration_seconds: float | None
    binding: VisualQueryNarrationBinding

    @property
    def target_duration_seconds(self) -> float | None:
        if (
            self.binding.narration_start_seconds is None
            or self.binding.narration_end_seconds is None
        ):
            return None
        return self.binding.narration_end_seconds - self.binding.narration_start_seconds


@dataclass(slots=True)
class Resolution:
    query: QueryContext
    direct: VisualCandidate | None
    candidates: list[VisualCandidate]
    result_sets: list[MediaSearchResultSet]
    image_candidates: list[StaticImageCandidate] = field(default_factory=list)
    image_result_sets: list[ImageSearchResultSet] = field(default_factory=list)
    image_error: MediaIndexerError | None = None
    error: MediaIndexerError | None = None
    reason: str | None = None



def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _uuid(value: Any, field: str, *, optional: bool = True) -> uuid.UUID | None:
    if value is None and optional:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise VisualDirectorInputError(f"{field} must be a UUID") from exc


def _optional_seconds(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise VisualDirectorInputError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise VisualDirectorInputError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise VisualDirectorInputError(f"{field} must be finite")
    return parsed


def _optional_positive_seconds(value: Any, field: str) -> float | None:
    parsed = _optional_seconds(value, field)
    if parsed is not None and parsed <= 0:
        raise VisualDirectorInputError(f"{field} must be greater than zero")
    return parsed


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise VisualDirectorInputError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise VisualDirectorInputError(f"{field} must be an integer") from exc


def _query_text(value: Any) -> str:
    try:
        return normalize_query(str(value))
    except ValueError as exc:
        raise VisualDirectorInputError(str(exc)) from exc


class VisualDirectorService:
    def __init__(
        self,
        session,
        config: Settings = settings,
        *,
        provider: MediaSearchProvider | None = None,
        workflow_repository: Any | None = None,
        alignment_service: Any | None = None,
        visual_plan_repository: Any | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.provider = provider or MediaIndexerSearchProvider(config)
        self.workflow_repository = workflow_repository or WorkflowRepository(session)
        self.alignment_service = alignment_service or NarrationAlignmentService(
            session, config=config
        )
        self.repository = visual_plan_repository or VisualPlanRepository(session)

    async def current(self, workflow_id: uuid.UUID) -> VisualPlan | None:
        workflow = await self.workflow_repository.obter_por_id(workflow_id, com_blocos=True)
        if workflow is None:
            raise VisualDirectorNotFoundError("Workflow não encontrado")
        try:
            alignment = await self.alignment_service.current(workflow_id)
            queries = self._queries(workflow, alignment) if alignment else []
            scope = self._scope(workflow.roteiro_snapshot, queries)
            request_fingerprint, _, _ = self._request_identity(workflow, alignment, queries, scope)
        except VisualDirectorInputError:
            return None
        return await self.repository.get_by_request_fingerprint(request_fingerprint)

    async def ensure(
        self, workflow_id: uuid.UUID, *, force: bool = False
    ) -> tuple[VisualPlan, bool]:
        workflow = await self.workflow_repository.obter_por_id(workflow_id, com_blocos=True)
        if workflow is None:
            raise VisualDirectorNotFoundError("Workflow não encontrado")

        alignment = await self.alignment_service.current(workflow_id)
        queries = self._queries(workflow, alignment) if alignment else []
        scope = self._scope(workflow.roteiro_snapshot, queries)
        request_fingerprint, script_fingerprint, scope_fingerprint = self._request_identity(
            workflow, alignment, queries, scope
        )
        strategy_version = self._strategy_version(scope)
        plan, created = await self.repository.create_or_get(
            workflow_execucao_id=workflow.id,
            narration_alignment_id=alignment.id if alignment else None,
            script_fingerprint=script_fingerprint,
            media_scope=scope.to_dict() if scope else None,
            media_scope_fingerprint=scope_fingerprint,
            strategy_version=strategy_version,
            request_fingerprint=request_fingerprint,
            fingerprint=request_fingerprint,
            status="pending",
        )

        if force:
            await self.repository.clear_outputs(plan)

        blocked_reason = self._prerequisite_error(workflow, alignment)
        if blocked_reason:
            await self.repository.mark_blocked(plan, blocked_reason)
            return await self.repository.reload(plan), created
        if not queries and self._requires_visual_queries(workflow):
            await self.repository.mark_blocked(
                plan,
                VISUAL_QUERIES_MISSING_MESSAGE,
                diagnostics={
                    "error_category": VISUAL_QUERIES_MISSING_CATEGORY,
                    "production_profile": workflow.perfil_producao,
                    "narration_duration_seconds": workflow.duracao_audio_segundos,
                    "visual_query_count": 0,
                },
            )
            return await self.repository.reload(plan), created
        if not created and not force:
            return plan, False
        if len(queries) > self.config.visual_director_max_queries:
            await self.repository.mark_failed(
                plan,
                "VisualQuery count exceeds configured limit "
                f"({self.config.visual_director_max_queries})",
            )
            return await self.repository.reload(plan), created

        await self.repository.mark_processing(plan)
        started = time.perf_counter()
        try:
            decisions, provenance, metrics, diagnostics, status = await self._build(
                plan, queries, scope
            )
            fingerprint = _sha256(
                {
                    "request_fingerprint": request_fingerprint,
                    "media_index_provenance": provenance,
                }
            )
            metrics["processing_seconds"] = round(time.perf_counter() - started, 6)
            await self.repository.replace_outputs(
                plan,
                decisions=decisions,
                status=status,
                fingerprint=fingerprint,
                media_index_provenance=provenance,
                metrics=metrics,
                diagnostics=diagnostics,
            )
            plan = await self.repository.reload(plan)
            if plan.status == "blocked":
                diagnostics = dict(plan.diagnostics or {})
                if scope is None:
                    diagnostics["error_category"] = "visual_plan_media_scope_missing"
                    message = "media scope is unavailable"
                else:
                    category = str(
                        diagnostics.get("error_category") or "visual_plan_insufficient_diversity"
                    )
                    message = f"Visual Plan failed deterministic quality gate: {category}"
                await self.repository.mark_blocked(
                    plan,
                    message,
                    diagnostics=diagnostics,
                )
                plan = await self.repository.reload(plan)
            logger.info(
                "visual plan finished",
                extra={
                    "workflow_id": str(workflow_id),
                    "visual_plan_id": str(plan.id),
                    "strategy_version": plan.strategy_version,
                    "status": plan.status,
                    "resolved": plan.resolved_queries,
                    "required_gaps": plan.required_gaps,
                    "media_search_calls": plan.media_search_count,
                    "direct_provenance_count": plan.direct_provenance_count,
                    "reused_source_count": plan.reused_source_count,
                    "processing_seconds": metrics["processing_seconds"],
                },
            )
            return plan, created
        except VisualDirectorInputError as exc:
            await self.repository.mark_failed(plan, str(exc))
        except Exception:
            logger.exception(
                "visual plan failed",
                extra={"workflow_id": str(workflow_id), "visual_plan_id": str(plan.id)},
            )
            await self.repository.mark_failed(plan, "Visual Director processing failed")
        return await self.repository.reload(plan), created

    def _prerequisite_error(
        self, workflow: WorkflowExecucao, alignment: NarrationAlignment | None
    ) -> str | None:
        if workflow.status != "audio_concluido" or not workflow.audio_final_sha256:
            return "Workflow final audio is not completed"
        if alignment is None:
            return "Compatible completed NarrationAlignment is required"
        if alignment.status != "completed":
            return "NarrationAlignment is not completed"
        if alignment.final_audio_sha256 != workflow.audio_final_sha256:
            return "NarrationAlignment audio identity is stale"
        return None

    @staticmethod
    def _requires_visual_queries(workflow: WorkflowExecucao) -> bool:
        return bool(
            workflow.perfil_producao in FINAL_VISUAL_PRODUCTION_PROFILES
            and workflow.duracao_audio_segundos
            and workflow.duracao_audio_segundos > 0
        )

    def _queries(
        self, workflow: WorkflowExecucao, alignment: NarrationAlignment | None
    ) -> list[QueryContext]:
        if alignment is None:
            return []
        bindings = {item.visual_query_id: item for item in alignment.visual_query_bindings}
        block_ids = {item.ordem: item.id for item in workflow.blocos}
        seen: set[uuid.UUID] = set()
        contexts: list[QueryContext] = []
        blocks = workflow.roteiro_snapshot.get("blocos")
        if not isinstance(blocks, list):
            raise VisualDirectorInputError("Script blocks must be a list")
        for fallback_order, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise VisualDirectorInputError("Script block must be an object")
            block_order = _integer(block.get("ordem", fallback_order), "block.order")
            visual_queries = block.get("visual_queries", [])
            if not isinstance(visual_queries, list):
                raise VisualDirectorInputError("visual_queries must be a list")
            for fallback_query_order, raw in enumerate(visual_queries):
                if not isinstance(raw, dict):
                    raise VisualDirectorInputError("VisualQuery must be an object")
                query_id = _uuid(raw.get("id"), "VisualQuery.id", optional=False)
                assert query_id is not None
                if query_id in seen:
                    raise VisualDirectorInputError("VisualQuery IDs must be unique")
                seen.add(query_id)
                binding = bindings.get(query_id)
                if binding is None:
                    raise VisualDirectorInputError(
                        "Compatible NarrationAlignment is missing a VisualQuery binding"
                    )
                if block_order not in block_ids or binding.audio_bloco_id != block_ids[block_order]:
                    raise VisualDirectorInputError("VisualQuery binding belongs to another block")
                intent = str(raw.get("intent", "illustration"))
                if intent not in {
                    "illustration",
                    "evidence",
                    "transition",
                    "atmosphere",
                    "comparison",
                }:
                    raise VisualDirectorInputError(f"Unsupported VisualQuery intent: {intent}")
                importance = str(raw.get("importance", "preferred"))
                if importance not in {"required", "preferred", "optional"}:
                    raise VisualDirectorInputError(
                        f"Unsupported VisualQuery importance: {importance}"
                    )
                contexts.append(
                    QueryContext(
                        id=query_id,
                        block_order=block_order,
                        query_order=_integer(
                            raw.get("order", fallback_query_order), "VisualQuery.order"
                        ),
                        query=_query_text(raw.get("query", "")),
                        intent=intent,
                        importance=importance,
                        source_note_id=_uuid(raw.get("source_note_id"), "source_note_id"),
                        preferred_session_id=_uuid(
                            raw.get("preferred_session_id"), "preferred_session_id"
                        ),
                        preferred_start_seconds=_optional_seconds(
                            raw.get("preferred_start_seconds"), "preferred_start_seconds"
                        ),
                        preferred_end_seconds=_optional_seconds(
                            raw.get("preferred_end_seconds"), "preferred_end_seconds"
                        ),
                        desired_duration_seconds=_optional_positive_seconds(
                            raw.get("desired_duration_seconds"), "desired_duration_seconds"
                        ),
                        binding=binding,
                    )
                )
        return sorted(
            contexts,
            key=lambda item: (
                item.block_order,
                item.binding.narration_start_seconds
                if item.binding.narration_start_seconds is not None
                else math.inf,
                item.query_order,
                str(item.id),
            ),
        )

    def _scope(self, snapshot: dict, queries: list[QueryContext]) -> MediaScope | None:
        raw = snapshot.get("media_context")
        if raw is not None and not isinstance(raw, dict):
            raise VisualDirectorInputError("media_context must be an object or null")
        project_id = None
        sessions: list[uuid.UUID] = []
        static_images: StaticImageScope | None = None
        origin = "script_media_context"
        if isinstance(raw, dict):
            project_id = _uuid(raw.get("media_project_id"), "media_project_id")
            session_values = raw.get("media_session_ids")
            if session_values is not None:
                if not isinstance(session_values, list):
                    raise VisualDirectorInputError("media_session_ids must be a list or null")
                for value in session_values:
                    item = _uuid(value, "media_session_ids", optional=False)
                    assert item is not None
                    sessions.append(item)
            raw_static = raw.get("static_images")
            if raw_static is not None:
                if not isinstance(raw_static, dict):
                    raise VisualDirectorInputError("static_images must be an object or null")

                def filters(name: str) -> tuple[str, ...]:
                    values = raw_static.get(name, [])
                    if values is None:
                        return ()
                    if not isinstance(values, list):
                        raise VisualDirectorInputError(f"static_images.{name} must be a list")
                    normalized = tuple(str(value).strip() for value in values)
                    if any(not value or len(value) > 200 for value in normalized):
                        raise VisualDirectorInputError(
                            f"static_images.{name} contains an invalid filter"
                        )
                    if len(set(normalized)) != len(normalized):
                        raise VisualDirectorInputError(
                            f"static_images.{name} filters must be unique"
                        )
                    return normalized

                static_images = StaticImageScope(
                    enabled=raw_static.get("enabled") is True,
                    asset_classes=filters("asset_classes"),
                    collections=filters("collections"),
                    source_owners=filters("source_owners"),
                    source_classes=filters("source_classes"),
                )
                combinations = math.prod(
                    max(1, len(value))
                    for value in (
                        static_images.asset_classes,
                        static_images.collections,
                        static_images.source_owners,
                        static_images.source_classes,
                    )
                )
                if combinations > self.config.visual_director_image_filter_combinations_max:
                    raise VisualDirectorInputError(
                        "static image filter combinations exceed configured limit"
                    )
        if not sessions and project_id is None:
            sessions = [
                item.preferred_session_id
                for item in queries
                if item.preferred_session_id is not None
            ]
            if sessions:
                origin = "legacy_visual_query_provenance"
        unique_sessions = tuple(sorted(set(sessions), key=str))
        if project_id is None and not unique_sessions:
            return None
        return MediaScope(
            project_id=project_id,
            session_ids=unique_sessions,
            origin=origin,
            static_images=static_images,
        )

    def _request_identity(
        self,
        workflow: WorkflowExecucao,
        alignment: NarrationAlignment | None,
        queries: list[QueryContext],
        scope: MediaScope | None,
    ) -> tuple[str, str, str]:
        script_value = {
            "workflow_id": str(workflow.id),
            "audio_sha256": workflow.audio_final_sha256,
            "visual_queries": [
                {
                    "id": str(item.id),
                    "block_order": item.block_order,
                    "query_order": item.query_order,
                    "query": item.query,
                    "intent": item.intent,
                    "importance": item.importance,
                    "source_note_id": str(item.source_note_id) if item.source_note_id else None,
                    "preferred_session_id": str(item.preferred_session_id)
                    if item.preferred_session_id
                    else None,
                    "preferred_start_seconds": item.preferred_start_seconds,
                    "preferred_end_seconds": item.preferred_end_seconds,
                    "desired_duration_seconds": item.desired_duration_seconds,
                }
                for item in queries
            ],
        }
        script_fingerprint = _sha256(script_value)
        scope_fingerprint = _sha256(scope.to_dict() if scope else None)
        diversity_policy = self._diversity_policy(scope)
        hybrid = bool(scope and scope.hybrid_enabled)
        request = {
            "script_fingerprint": script_fingerprint,
            "narration_alignment_id": str(alignment.id) if alignment else None,
            "narration_alignment_fingerprint": alignment.fingerprint if alignment else None,
            "media_scope_fingerprint": scope_fingerprint,
            "strategy_version": self._strategy_version(scope),
            "policy": {
                "video_window_quality": "black_window_quality_v1",
                "search_top_k": self.config.visual_director_search_top_k,
                "max_alternatives": self.config.visual_director_max_alternatives,
                "min_semantic_score": self.config.visual_director_min_semantic_score,
                "candidate_format": "visual_source_v2" if hybrid else "visual_source_v1",
                "media_search_strategy": (
                    self.config.visual_director_media_search_strategy_version
                ),
                "image_search_top_k": self.config.visual_director_image_search_top_k
                if hybrid
                else None,
                "image_min_semantic_score": self.config.visual_director_image_min_semantic_score
                if hybrid
                else None,
                "fusion": {
                    "version": self.config.hybrid_visual_fusion_version,
                    "rrf_k": self.config.hybrid_visual_fusion_rrf_k,
                    "video_weight": self.config.hybrid_visual_fusion_video_weight,
                    "image_weight": self.config.hybrid_visual_fusion_image_weight,
                }
                if hybrid
                else None,
                "diversity_policy": {
                    "version": diversity_policy.version,
                    "minimum_window_count": diversity_policy.minimum_window_count,
                    "maximum_reuse_ratio": diversity_policy.maximum_reuse_ratio,
                    "maximum_dominant_window_ratio": (
                        diversity_policy.maximum_dominant_window_ratio
                    ),
                    "maximum_dominant_static_ratio": (
                        diversity_policy.maximum_dominant_static_ratio
                    ),
                    "maximum_static_reuse_count": diversity_policy.maximum_static_reuse_count,
                    "maximum_consecutive_static_seconds": (
                        diversity_policy.maximum_consecutive_static_seconds
                    ),
                },
            },
        }
        return _sha256(request), script_fingerprint, scope_fingerprint

    async def _quality_checked_candidate(
        self,
        candidate: HybridVisualCandidate,
        query: QueryContext,
    ) -> HybridVisualCandidate | None:
        if isinstance(candidate, StaticImageCandidate):
            return candidate
        try:
            quality = await self.provider.video_window_quality(
                candidate.media_id,
                start_seconds=candidate.start_seconds,
                end_seconds=candidate.end_seconds,
            )
        except MediaIndexerError:
            return None
        if quality.status == "clean":
            return candidate
        if quality.status != "edge_black":
            return None
        start, end = quality.recommended_start_seconds, quality.recommended_end_seconds
        if start is None or end is None:
            return None
        required_duration = min(
            candidate.duration_seconds,
            query.target_duration_seconds
            or query.desired_duration_seconds
            or candidate.duration_seconds,
        )
        if not candidate.start_seconds <= start < end <= candidate.end_seconds or end - start < max(
            self.config.production_timeline_min_visual_seconds, required_duration
        ):
            return None
        return replace(candidate, start_seconds=start, end_seconds=end)

    def _strategy_version(self, scope: MediaScope | None) -> str:
        return (
            self.config.visual_director_hybrid_strategy_version
            if scope and scope.hybrid_enabled
            else self.config.visual_director_strategy_version
        )

    async def _build(
        self,
        plan: VisualPlan,
        queries: list[QueryContext],
        scope: MediaScope | None,
    ) -> tuple[list[VisualDecision], dict, dict, dict, str]:
        semaphore = asyncio.Semaphore(self.config.visual_director_search_concurrency)
        session_tasks: dict[uuid.UUID, asyncio.Task[MediaSessionMetadata]] = {}
        search_tasks: dict[tuple, asyncio.Task[MediaSearchResultSet]] = {}
        image_tasks: dict[tuple, asyncio.Task[ImageSearchResultSet]] = {}
        stats = {
            "media_search_calls": 0,
            "image_search_calls": 0,
            "search_seconds": 0.0,
            "image_search_seconds": 0.0,
            "metadata_calls": 0,
        }

        async def get_session(session_id: uuid.UUID) -> MediaSessionMetadata:
            if session_id not in session_tasks:

                async def execute() -> MediaSessionMetadata:
                    async with semaphore:
                        stats["metadata_calls"] += 1
                        return await self.provider.get_session(session_id)

                session_tasks[session_id] = asyncio.create_task(execute())
            return await session_tasks[session_id]

        async def cached_search(key: tuple, call) -> MediaSearchResultSet:
            if key not in search_tasks:

                async def execute() -> MediaSearchResultSet:
                    async with semaphore:
                        started = time.perf_counter()
                        stats["media_search_calls"] += 1
                        try:
                            return await call()
                        finally:
                            stats["search_seconds"] += time.perf_counter() - started

                search_tasks[key] = asyncio.create_task(execute())
            return await search_tasks[key]

        async def cached_image_search(key: tuple, call) -> ImageSearchResultSet:
            if key not in image_tasks:

                async def execute() -> ImageSearchResultSet:
                    async with semaphore:
                        started = time.perf_counter()
                        stats["image_search_calls"] += 1
                        try:
                            return await call()
                        finally:
                            stats["image_search_seconds"] += time.perf_counter() - started

                image_tasks[key] = asyncio.create_task(execute())
            return await image_tasks[key]

        async def resolve(item: QueryContext) -> Resolution:
            direct_reason = None
            if self._structurally_valid_preferred(item, scope):
                assert item.preferred_session_id is not None
                assert item.preferred_start_seconds is not None
                assert item.preferred_end_seconds is not None
                try:
                    metadata = await get_session(item.preferred_session_id)
                    direct_reason = self._direct_metadata_error(item, scope, metadata)
                    if direct_reason is None:
                        return Resolution(
                            query=item,
                            direct=direct_candidate(
                                project_id=metadata.project_id,
                                session_id=metadata.id,
                                media_id=metadata.media_id,
                                start_seconds=item.preferred_start_seconds,
                                end_seconds=item.preferred_end_seconds,
                                session_label=metadata.label,
                                media_filename=metadata.media_filename,
                            ),
                            candidates=[],
                            result_sets=[],
                        )
                except MediaIndexerError as exc:
                    direct_reason = f"preferred provenance validation failed: {exc.category}"

            if scope is None:
                return Resolution(
                    query=item,
                    direct=None,
                    candidates=[],
                    result_sets=[],
                    reason=direct_reason or "media scope is unavailable",
                )
            temporal_call = self._search_query(
                item,
                scope,
                cached_search,
                use_preferred_session=direct_reason
                != "preferred session belongs to another project",
            )
            temporal_result: object
            image_result: object
            if scope.hybrid_enabled:
                image_call = self._search_images(item, scope, cached_image_search)
                temporal_result, image_result = await asyncio.gather(
                    temporal_call, image_call, return_exceptions=True
                )
            else:
                temporal_results = await asyncio.gather(
                    temporal_call, return_exceptions=True
                )
                temporal_result = temporal_results[0]
                image_result = []
            temporal_error = (
                temporal_result if isinstance(temporal_result, MediaIndexerError) else None
            )
            image_error = image_result if isinstance(image_result, MediaIndexerError) else None
            if isinstance(temporal_result, Exception) and temporal_error is None:
                raise temporal_result
            if isinstance(image_result, Exception) and image_error is None:
                raise image_result
            if temporal_error:
                result_sets = []
            else:
                assert isinstance(temporal_result, list)
                result_sets = temporal_result
            if image_error:
                image_result_sets = []
            else:
                assert isinstance(image_result, list)
                image_result_sets = image_result
            assert isinstance(result_sets, list)
            assert isinstance(image_result_sets, list)
            candidates = self._normalize_candidates(result_sets, scope)
            image_candidates = self._normalize_image_candidates(image_result_sets, scope)
            branch_reasons = []
            if temporal_error:
                branch_reasons.append(f"Media Search failed: {temporal_error.category}")
            if image_error:
                branch_reasons.append(f"Image Search failed: {image_error.category}")
            return Resolution(
                query=item,
                direct=None,
                candidates=candidates,
                result_sets=result_sets,
                image_candidates=image_candidates,
                image_result_sets=image_result_sets,
                error=temporal_error,
                image_error=image_error,
                reason=direct_reason or ("; ".join(branch_reasons) if branch_reasons else None),
            )

        resolutions = await asyncio.gather(*(resolve(item) for item in queries))
        already_selected: list[HybridVisualCandidate] = []
        decisions: list[VisualDecision] = []
        reused = 0
        for resolution in resolutions:
            item = resolution.query
            selected: HybridVisualCandidate | None = resolution.direct
            alternatives: list[HybridVisualCandidate] = []
            ranked: list[HybridVisualCandidate] = []
            source_status = "resolved"
            reason = resolution.reason
            # Apply Game Entity Guard: filter candidates by game if query mentions one
            query_game = extract_game_from_query(item.query)
            game_filtered_candidates = filter_candidates_by_game(resolution.candidates, query_game)
            all_candidates = [*game_filtered_candidates, *resolution.image_candidates]
            if selected is None and all_candidates:
                if scope and scope.hybrid_enabled:
                    ranked = fuse_hybrid_candidates(
                        game_filtered_candidates,
                        resolution.image_candidates,
                        already_selected=already_selected,
                        preferred_session_id=item.preferred_session_id,
                        source_note_id=item.source_note_id,
                        rrf_k=self.config.hybrid_visual_fusion_rrf_k,
                        video_weight=self.config.hybrid_visual_fusion_video_weight,
                        image_weight=self.config.hybrid_visual_fusion_image_weight,
                        strategy_version=self.config.hybrid_visual_fusion_version,
                    )
                else:
                    ranked.extend(
                        rank_candidates(
                            game_filtered_candidates,
                            preferred_session_id=item.preferred_session_id,
                            source_note_id=item.source_note_id,
                            target_duration_seconds=item.target_duration_seconds,
                            already_selected=[
                                value
                                for value in already_selected
                                if isinstance(value, VisualCandidate)
                            ],
                            intent=item.intent,
                        )
                    )
                if (
                    ranked
                    and (
                        isinstance(ranked[0], StaticImageCandidate)
                        or (ranked[0].semantic_score or 0)
                        >= self.config.visual_director_min_semantic_score
                    )
                ):
                    selected = ranked[0]
                    alternatives = ranked[1 : 1 + self.config.visual_director_max_alternatives]
                    reason = resolution.reason
                else:
                    source_status = "unresolved"
                    reason = "No Media Search result met the semantic threshold"
            elif selected is None:
                branch_errors = (
                    [resolution.error, resolution.image_error]
                    if scope and scope.hybrid_enabled
                    else [resolution.error]
                )
                if all(
                    value is not None and not isinstance(value, MediaIndexerConflictError)
                    for value in branch_errors
                ):
                    source_status = "failed"
                else:
                    source_status = "unresolved"
                reason = reason or "Media Search returned no relevant results"

            if selected is not None:
                valid = []
                for candidate in [selected, *alternatives[:2]]:
                    checked = await self._quality_checked_candidate(candidate, item)
                    if checked is not None:
                        valid.append(checked)
                selected = valid[0] if valid else None
                alternatives = valid[1:]
                if selected is None:
                    source_status = "unresolved"
                    reason = "No bounded candidate passed video window quality"

            if selected is not None:
                if any(self._candidate_reused(selected, previous) for previous in already_selected):
                    reused += 1
                already_selected.append(selected)

            hybrid = bool(scope and scope.hybrid_enabled)
            selected_payload = (
                self._candidate_payload(selected, item, hybrid=hybrid) if selected else None
            )
            alternative_payloads = [
                self._candidate_payload(value, item, hybrid=hybrid) for value in alternatives
            ]
            decisions.append(
                VisualDecision(
                    visual_plan_id=plan.id,
                    visual_query_id=item.id,
                    audio_bloco_id=item.binding.audio_bloco_id,
                    block_order=item.block_order,
                    query_order=item.query_order,
                    query=item.query,
                    intent=item.intent,
                    importance=item.importance,
                    source_note_id=item.source_note_id,
                    desired_duration_seconds=item.desired_duration_seconds,
                    narration_binding_status=item.binding.status,
                    narration_start_seconds=item.binding.narration_start_seconds,
                    narration_end_seconds=item.binding.narration_end_seconds,
                    target_narration_duration_seconds=item.target_duration_seconds,
                    source_resolution_status=source_status,
                    selection_strategy=selected.selection_strategy if selected else None,
                    selected_source=selected_payload,
                    alternatives=alternative_payloads,
                    selection_score=selected.selection_score if selected else None,
                    score_components=selected.score_components if selected else None,
                    reason=reason,
                    diagnostics={
                        "search_scope": resolution.result_sets[0].scope
                        if resolution.result_sets
                        else None,
                        "candidate_count": len(all_candidates),
                        "temporal_candidate_count": len(resolution.candidates),
                        "image_candidate_count": len(resolution.image_candidates),
                        "temporal_branch_error": resolution.error.category
                        if resolution.error
                        else None,
                        "image_branch_error": resolution.image_error.category
                        if resolution.image_error
                        else None,
                        "fusion_ranking": [
                            {
                                "source_type": candidate.source_type,
                                "media_id": str(candidate.media_id),
                                "final_rank": rank,
                                **(candidate.score_components or {}),
                            }
                            for rank, candidate in enumerate(
                                ranked if selected is not None and all_candidates else [], 1
                            )
                        ],
                    },
                )
            )

        provenance = self._media_index_provenance(resolutions)
        selected_scores = [
            item.selection_score
            for item in decisions
            if item.selection_strategy != "preferred_provenance"
            and item.selection_score is not None
        ]
        metrics = {
            "media_search_calls": stats["media_search_calls"],
            "temporal_search_calls": stats["media_search_calls"],
            "image_search_calls": stats["image_search_calls"],
            "metadata_calls": stats["metadata_calls"],
            "search_seconds": round(stats["search_seconds"], 6),
            "average_search_seconds": round(
                stats["search_seconds"] / stats["media_search_calls"], 6
            )
            if stats["media_search_calls"]
            else 0.0,
            "reused_source_count": reused,
            "temporal_candidate_count": sum(
                len(item.candidates) for item in resolutions
            ),
            "image_candidate_count": sum(
                len(item.image_candidates) for item in resolutions
            ),
            "selected_video_count": sum(
                isinstance(item.selected_source, dict)
                and item.selected_source.get("source_type") in {"gameplay", "video_segment"}
                for item in decisions
            ),
            "selected_image_count": sum(
                isinstance(item.selected_source, dict)
                and item.selected_source.get("source_type") == "static_image"
                for item in decisions
            ),
            "average_selected_score": round(sum(selected_scores) / len(selected_scores), 6)
            if selected_scores
            else None,
        }
        candidate_counts = [
            len(item.candidates) + len(item.image_candidates) for item in resolutions
        ]
        if candidate_counts:
            metrics.update(
                {
                    "candidate_count_min": min(candidate_counts),
                    "candidate_count_avg": round(sum(candidate_counts) / len(candidate_counts), 6),
                    "candidate_count_max": max(candidate_counts),
                }
            )
        selected_windows = []
        for decision in decisions:
            source = decision.selected_source
            if decision.source_resolution_status != "resolved" or not isinstance(source, dict):
                continue
            source_type = str(source.get("source_type", "gameplay"))
            selected_windows.append(
                VisualSourceWindow(
                    session_id=str(source.get("session_id") or ""),
                    media_id=str(source["media_id"]),
                    start_seconds=float(source.get("source_start_seconds") or 0.0),
                    end_seconds=float(source.get("source_end_seconds") or 0.0),
                    explicitly_anchored=decision.selection_strategy == "preferred_provenance",
                    source_type=source_type,
                )
            )
        diversity = evaluate_visual_diversity(selected_windows, self._diversity_policy(scope))
        metrics.update(diversity.metrics)
        diagnostics = {
            "query_order": [str(item.id) for item in queries],
            "candidate_format_version": "visual_source_v2"
            if scope and scope.hybrid_enabled
            else "visual_source_v1",
            "hybrid_fusion_version": self.config.hybrid_visual_fusion_version
            if scope and scope.hybrid_enabled
            else None,
            "selection_policy": {
                "preferred_provenance": "hierarchical priority",
                "preferred_session_bonus": 0.05,
                "source_note_bonus": 0.03,
                "duration_fit_weight": 0.02,
                "overlap_penalty_weight": 0.12,
                "exact_reuse_penalty": 0.18,
                "consecutive_exact_reuse_penalty": 0.08,
                "evidence_semantic_band": 0.02,
            },
            "visual_diversity": diversity.to_dict(),
        }
        failed = sum(item.source_resolution_status == "failed" for item in decisions)
        resolved = sum(item.source_resolution_status == "resolved" for item in decisions)
        if diversity.status == "failed":
            status = "blocked"
            diagnostics["error_category"] = diversity.reasons[0]
        elif not decisions or resolved == len(decisions):
            status = "completed"
        elif failed == len(decisions):
            status = "failed"
        elif resolved == 0 and scope is None:
            status = "blocked"
        else:
            status = "completed_with_gaps"
        return decisions, provenance, metrics, diagnostics, status

    def _diversity_policy(self, scope: MediaScope | None = None) -> VisualDiversityPolicy:
        hybrid = bool(scope and scope.hybrid_enabled)
        return VisualDiversityPolicy(
            version=self.config.visual_diversity_hybrid_policy_version
            if hybrid
            else self.config.visual_diversity_policy_version,
            minimum_window_count=self.config.visual_diversity_minimum_window_count,
            maximum_reuse_ratio=self.config.visual_diversity_maximum_reuse_ratio,
            maximum_dominant_window_ratio=(
                self.config.visual_diversity_maximum_dominant_window_ratio
            ),
            maximum_dominant_static_ratio=(
                self.config.visual_diversity_maximum_dominant_static_ratio
                if hybrid
                else 1.0
            ),
            maximum_static_reuse_count=(
                self.config.visual_diversity_maximum_static_reuse_count
                if hybrid
                else 1_000_000
            ),
            maximum_consecutive_static_seconds=(
                self.config.static_image_maximum_consecutive_seconds
                if hybrid
                else 1_000_000.0
            ),
        )

    def _structurally_valid_preferred(self, item: QueryContext, scope: MediaScope | None) -> bool:
        if (
            item.preferred_session_id is None
            or item.preferred_start_seconds is None
            or item.preferred_end_seconds is None
        ):
            return False
        if (
            item.preferred_start_seconds < 0
            or item.preferred_end_seconds <= item.preferred_start_seconds
        ):
            return False
        return not (
            scope and scope.session_ids and item.preferred_session_id not in scope.session_ids
        )

    def _direct_metadata_error(
        self, item: QueryContext, scope: MediaScope | None, metadata: MediaSessionMetadata
    ) -> str | None:
        if metadata.status != "ready":
            return "preferred session is not ready"
        if scope and scope.project_id and metadata.project_id != scope.project_id:
            return "preferred session belongs to another project"
        assert item.preferred_end_seconds is not None
        if (
            metadata.duration_seconds is not None
            and item.preferred_end_seconds > metadata.duration_seconds + 0.25
        ):
            return "preferred range exceeds media duration"
        return None

    async def _search_query(
        self,
        item: QueryContext,
        scope: MediaScope,
        cached_search,
        *,
        use_preferred_session: bool,
    ) -> list[MediaSearchResultSet]:
        max_results = self.config.visual_director_search_top_k
        min_score = self.config.visual_director_min_semantic_score
        if (
            use_preferred_session
            and item.preferred_session_id
            and (not scope.session_ids or item.preferred_session_id in scope.session_ids)
        ):
            session_id = item.preferred_session_id
            key: tuple[Any, ...] = (
                "session",
                str(session_id),
                item.query,
                max_results,
                min_score,
            )
            return [
                await cached_search(
                    key,
                    lambda: self.provider.search_session(
                        session_id,
                        query=item.query,
                        max_results=max_results,
                        min_score=min_score,
                    ),
                )
            ]
        if scope.project_id is not None:
            project_id = scope.project_id
            session_ids = list(scope.session_ids) or None
            key = (
                "project",
                str(project_id),
                tuple(str(value) for value in scope.session_ids),
                item.query,
                max_results,
                min_score,
            )
            return [
                await cached_search(
                    key,
                    lambda: self.provider.search_project(
                        project_id,
                        query=item.query,
                        max_results=max_results,
                        min_score=min_score,
                        session_ids=session_ids,
                    ),
                )
            ]
        if scope.session_ids:
            calls = []
            for session_id in scope.session_ids:
                key = ("session", str(session_id), item.query, max_results, min_score)
                calls.append(
                    cached_search(
                        key,
                        lambda session_id=session_id: self.provider.search_session(
                            session_id,
                            query=item.query,
                            max_results=max_results,
                            min_score=min_score,
                        ),
                    )
                )
            results = await asyncio.gather(*calls, return_exceptions=True)
            successes = [value for value in results if isinstance(value, MediaSearchResultSet)]
            if successes:
                return successes
            errors = [value for value in results if isinstance(value, MediaIndexerError)]
            if errors:
                raise errors[0]
        raise MediaIndexerConflictError("No searchable project or session scope")

    async def _search_images(
        self,
        item: QueryContext,
        scope: MediaScope,
        cached_image_search,
    ) -> list[ImageSearchResultSet]:
        if not scope.hybrid_enabled or scope.project_id is None or scope.static_images is None:
            return []
        image_scope = scope.static_images
        dimensions = (
            image_scope.source_owners or (None,),
            image_scope.source_classes or (None,),
            image_scope.asset_classes or (None,),
            image_scope.collections or (None,),
        )
        calls = []
        for source_owner, source_class, asset_class, collection in product(*dimensions):
            filters = ImageSearchFilters(
                source_owner=source_owner,
                source_class=source_class,
                asset_class=asset_class,
                collection=collection,
            )
            key = (
                "image",
                str(scope.project_id),
                item.query,
                self.config.visual_director_image_search_top_k,
                self.config.visual_director_image_min_semantic_score,
                tuple(sorted(filters.to_dict().items())),
            )
            calls.append(
                cached_image_search(
                    key,
                    lambda filters=filters: self.provider.search_images(
                        scope.project_id,
                        query=item.query,
                        max_results=self.config.visual_director_image_search_top_k,
                        min_score=self.config.visual_director_image_min_semantic_score,
                        filters=filters,
                    ),
                )
            )
        results = await asyncio.gather(*calls, return_exceptions=True)
        errors = [value for value in results if isinstance(value, MediaIndexerError)]
        if errors:
            raise errors[0]
        return [value for value in results if isinstance(value, ImageSearchResultSet)]

    def _normalize_candidates(
        self, result_sets: list[MediaSearchResultSet], scope: MediaScope
    ) -> list[VisualCandidate]:
        candidates: list[VisualCandidate] = []
        for result_set in result_sets:
            if (
                result_set.strategy
                != self.config.visual_director_media_search_strategy_version
            ):
                raise VisualDirectorInputError(
                    "Media Search strategy is incompatible with Visual Director policy"
                )
            if scope.project_id is not None and result_set.project_id != scope.project_id:
                raise VisualDirectorInputError("Media Search returned another project")
            for item in result_set.results:
                if scope.session_ids and item.session_id not in scope.session_ids:
                    continue
                candidates.append(
                    VisualCandidate(
                        project_id=result_set.project_id,
                        session_id=item.session_id,
                        media_id=item.media_id,
                        start_seconds=item.start_seconds,
                        end_seconds=item.end_seconds,
                        semantic_score=item.score,
                        rank=item.rank,
                        session_label=item.session_label,
                        media_filename=item.media_filename,
                        temporal_index_id=item.temporal_index_id,
                        retrieval_candidate_id=item.retrieval_candidate_id,
                        transcript_preview=item.transcript_preview,
                        matched_note_ids=item.matched_note_ids,
                        search_strategy=result_set.strategy,
                        provenance=item.provenance,
                    )
                )
        return candidates

    def _normalize_image_candidates(
        self, result_sets: list[ImageSearchResultSet], scope: MediaScope
    ) -> list[StaticImageCandidate]:
        candidates: list[StaticImageCandidate] = []
        for result_set in result_sets:
            if scope.project_id is None or result_set.project_id != scope.project_id:
                raise VisualDirectorInputError("Image Search returned another project")
            for item in result_set.results:
                if item.project_id != scope.project_id:
                    raise VisualDirectorInputError("Image Search candidate escaped project scope")
                candidates.append(
                    StaticImageCandidate(
                        project_id=item.project_id,
                        media_id=item.media_id,
                        image_semantic_index_id=item.image_semantic_index_id,
                        semantic_score=item.score,
                        rank=item.rank,
                        media_filename=item.original_filename,
                        content_type=item.content_type,
                        byte_size=item.byte_size,
                        sha256=item.sha256,
                        width=item.width,
                        height=item.height,
                        format_name=item.format_name,
                        provenance=item.provenance,
                        semantic_preview=item.semantic_preview,
                        semantic_index=item.semantic_index,
                    )
                )
        deduplicated: dict[uuid.UUID, StaticImageCandidate] = {}
        for candidate in sorted(
            candidates,
            key=lambda value: (-value.semantic_score, value.rank, str(value.media_id)),
        ):
            deduplicated.setdefault(candidate.media_id, candidate)
        return [
            replace(candidate, rank=rank)
            for rank, candidate in enumerate(deduplicated.values(), 1)
        ]

    @staticmethod
    def _candidate_reused(
        candidate: HybridVisualCandidate, previous: HybridVisualCandidate
    ) -> bool:
        if isinstance(candidate, StaticImageCandidate) or isinstance(
            previous, StaticImageCandidate
        ):
            return (
                isinstance(candidate, StaticImageCandidate)
                and isinstance(previous, StaticImageCandidate)
                and candidate.media_id == previous.media_id
            )
        return overlap_ratio(candidate, previous) > 0.5

    def _candidate_payload(
        self,
        candidate: HybridVisualCandidate,
        item: QueryContext,
        *,
        hybrid: bool,
    ) -> dict[str, Any]:
        if isinstance(candidate, StaticImageCandidate):
            return VisualStaticImageSourceSelectionOut(
                project_id=candidate.project_id,
                media_id=candidate.media_id,
                image_semantic_index_id=candidate.image_semantic_index_id,
                semantic_score=candidate.semantic_score,
                selection_score=candidate.selection_score,
                rank=candidate.rank,
                media_filename=candidate.media_filename,
                content_type=candidate.content_type,
                byte_size=candidate.byte_size,
                sha256=candidate.sha256,
                width=candidate.width,
                height=candidate.height,
                format_name=candidate.format_name,
                semantic_preview=candidate.semantic_preview,
                provenance=candidate.provenance,
                semantic_index=candidate.semantic_index or {},
            ).model_dump(mode="json")
        target = item.target_duration_seconds
        duration_fit = None
        if target and target > 0:
            duration_fit = min(
                candidate.duration_seconds / target,
                target / candidate.duration_seconds,
            )
        source_class = (
            VisualVideoSegmentSourceSelectionOut
            if hybrid
            else VisualGameplaySourceSelectionOut
        )
        values = {
            "project_id": candidate.project_id,
            "session_id": candidate.session_id,
            "media_id": candidate.media_id,
            "source_start_seconds": candidate.start_seconds,
            "source_end_seconds": candidate.end_seconds,
            "source_duration_seconds": candidate.duration_seconds,
            "semantic_score": candidate.semantic_score,
            "selection_score": candidate.selection_score,
            "rank": candidate.rank,
            "session_label": candidate.session_label,
            "media_filename": candidate.media_filename,
            "temporal_index_id": candidate.temporal_index_id,
            "retrieval_candidate_id": candidate.retrieval_candidate_id,
            "transcript_preview": candidate.transcript_preview,
            "matched_note_ids": list(candidate.matched_note_ids),
            "media_search_strategy": candidate.search_strategy,
            "duration_fit_ratio": round(duration_fit, 6) if duration_fit is not None else None,
            "provenance": candidate.provenance or {},
        }
        if hybrid:
            values["candidate_start_seconds"] = candidate.start_seconds
            values["candidate_end_seconds"] = candidate.end_seconds
        payload = source_class(**values)
        return payload.model_dump(mode="json")

    def _media_index_provenance(self, resolutions: list[Resolution]) -> dict[str, Any]:
        result_sets = [value for resolution in resolutions for value in resolution.result_sets]
        temporal_index_ids = sorted(
            {
                str(candidate.temporal_index_id)
                for resolution in resolutions
                for candidate in resolution.candidates
                if candidate.temporal_index_id is not None
            }
        )
        compatibility_signatures = sorted(
            {
                str(candidate.provenance["compatibility_signature"])
                for resolution in resolutions
                for candidate in resolution.candidates
                if candidate.provenance and candidate.provenance.get("compatibility_signature")
            }
        )
        summaries: dict[str, dict[str, Any]] = {}
        for result_set in result_sets:
            for summary in result_set.session_summaries:
                summaries[str(summary["id"])] = {
                    "id": str(summary["id"]),
                    "searched": bool(summary["searched"]),
                    "result_count": int(summary["result_count"]),
                    "skipped": not bool(summary["searched"]),
                }
        embedding_profiles = sorted(
            {
                (
                    value.embedding_provider,
                    value.embedding_model,
                    value.embedding_dimensions,
                )
                for value in result_sets
            }
        )
        image_indexes = sorted(
            {
                str(candidate.image_semantic_index_id)
                for resolution in resolutions
                for candidate in resolution.image_candidates
            }
        )
        image_profiles = sorted(
            {
                (
                    str((candidate.semantic_index or {}).get("embedding_provider", "")),
                    str((candidate.semantic_index or {}).get("embedding_model", "")),
                    int((candidate.semantic_index or {}).get("embedding_dimensions", 0)),
                )
                for resolution in resolutions
                for candidate in resolution.image_candidates
            }
        )
        return {
            "temporal_index_ids": temporal_index_ids,
            "compatibility_signatures": compatibility_signatures,
            "media_search_strategies": sorted({value.strategy for value in result_sets}),
            "embedding_profiles": [
                {"provider": provider, "model": model, "dimensions": dimensions}
                for provider, model, dimensions in embedding_profiles
            ],
            "image_semantic_index_ids": image_indexes,
            "image_embedding_profiles": [
                {"provider": provider, "model": model, "dimensions": dimensions}
                for provider, model, dimensions in image_profiles
            ],
            "retrieval_branches": {
                "temporal": {
                    "result_sets": len(result_sets),
                    "failures": sum(item.error is not None for item in resolutions),
                },
                "static_image": {
                    "result_sets": sum(
                        len(item.image_result_sets) for item in resolutions
                    ),
                    "failures": sum(item.image_error is not None for item in resolutions),
                },
            },
            "session_summaries": [summaries[key] for key in sorted(summaries)],
        }
