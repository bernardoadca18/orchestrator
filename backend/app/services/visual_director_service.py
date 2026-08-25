"""Stage 14 deterministic Visual Director orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.clients.media_indexer_search import (
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
from app.schemas.workflow import VisualSourceSelectionOut
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.visual_director_algorithm import (
    VisualCandidate,
    direct_candidate,
    normalize_query,
    overlap_ratio,
    rank_candidates,
)

logger = logging.getLogger(__name__)


class VisualDirectorError(RuntimeError):
    pass


class VisualDirectorNotFoundError(VisualDirectorError):
    pass


class VisualDirectorInputError(VisualDirectorError):
    pass


@dataclass(frozen=True, slots=True)
class MediaScope:
    project_id: uuid.UUID | None
    session_ids: tuple[uuid.UUID, ...]
    origin: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_project_id": str(self.project_id) if self.project_id else None,
            "media_session_ids": [str(item) for item in self.session_ids] or None,
            "origin": self.origin,
        }


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
        plan, created = await self.repository.create_or_get(
            workflow_execucao_id=workflow.id,
            narration_alignment_id=alignment.id if alignment else None,
            script_fingerprint=script_fingerprint,
            media_scope=scope.to_dict() if scope else None,
            media_scope_fingerprint=scope_fingerprint,
            strategy_version=self.config.visual_director_strategy_version,
            request_fingerprint=request_fingerprint,
            fingerprint=request_fingerprint,
            status="pending",
        )

        if not created and not force:
            return plan, False
        if force:
            await self.repository.clear_outputs(plan)

        blocked_reason = self._prerequisite_error(workflow, alignment)
        if blocked_reason:
            await self.repository.mark_blocked(plan, blocked_reason)
            return await self.repository.reload(plan), created
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
        return MediaScope(project_id=project_id, session_ids=unique_sessions, origin=origin)

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
        request = {
            "script_fingerprint": script_fingerprint,
            "narration_alignment_id": str(alignment.id) if alignment else None,
            "narration_alignment_fingerprint": alignment.fingerprint if alignment else None,
            "media_scope_fingerprint": scope_fingerprint,
            "strategy_version": self.config.visual_director_strategy_version,
            "policy": {
                "search_top_k": self.config.visual_director_search_top_k,
                "max_alternatives": self.config.visual_director_max_alternatives,
                "min_semantic_score": self.config.visual_director_min_semantic_score,
                "candidate_format": "visual_source_v1",
            },
        }
        return _sha256(request), script_fingerprint, scope_fingerprint

    async def _build(
        self,
        plan: VisualPlan,
        queries: list[QueryContext],
        scope: MediaScope | None,
    ) -> tuple[list[VisualDecision], dict, dict, dict, str]:
        semaphore = asyncio.Semaphore(self.config.visual_director_search_concurrency)
        session_tasks: dict[uuid.UUID, asyncio.Task[MediaSessionMetadata]] = {}
        search_tasks: dict[tuple, asyncio.Task[MediaSearchResultSet]] = {}
        stats = {"media_search_calls": 0, "search_seconds": 0.0, "metadata_calls": 0}

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
            try:
                result_sets = await self._search_query(
                    item,
                    scope,
                    cached_search,
                    use_preferred_session=direct_reason
                    != "preferred session belongs to another project",
                )
            except MediaIndexerError as exc:
                return Resolution(
                    query=item,
                    direct=None,
                    candidates=[],
                    result_sets=[],
                    error=exc,
                    reason=f"Media Search failed: {exc.category}",
                )
            candidates = self._normalize_candidates(result_sets, scope)
            return Resolution(
                query=item,
                direct=None,
                candidates=candidates,
                result_sets=result_sets,
                reason=direct_reason,
            )

        resolutions = await asyncio.gather(*(resolve(item) for item in queries))
        already_selected: list[VisualCandidate] = []
        decisions: list[VisualDecision] = []
        reused = 0
        for resolution in resolutions:
            item = resolution.query
            selected: VisualCandidate | None = resolution.direct
            alternatives: list[VisualCandidate] = []
            source_status = "resolved"
            reason = resolution.reason
            if selected is None and resolution.candidates:
                ranked = rank_candidates(
                    resolution.candidates,
                    preferred_session_id=item.preferred_session_id,
                    source_note_id=item.source_note_id,
                    target_duration_seconds=item.target_duration_seconds,
                    already_selected=already_selected,
                )
                if (
                    ranked
                    and (ranked[0].semantic_score or 0)
                    >= self.config.visual_director_min_semantic_score
                ):
                    selected = ranked[0]
                    alternatives = ranked[1 : 1 + self.config.visual_director_max_alternatives]
                    reason = None
                else:
                    source_status = "unresolved"
                    reason = "No Media Search result met the semantic threshold"
            elif selected is None:
                if resolution.error is not None and not isinstance(
                    resolution.error, MediaIndexerConflictError
                ):
                    source_status = "failed"
                else:
                    source_status = "unresolved"
                reason = reason or "Media Search returned no relevant results"

            if selected is not None:
                if any(overlap_ratio(selected, previous) > 0.5 for previous in already_selected):
                    reused += 1
                already_selected.append(selected)

            selected_payload = self._candidate_payload(selected, item) if selected else None
            alternative_payloads = [self._candidate_payload(value, item) for value in alternatives]
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
                        "candidate_count": len(resolution.candidates),
                    },
                )
            )

        provenance = self._media_index_provenance(resolutions)
        selected_scores = [
            item.selection_score
            for item in decisions
            if item.selection_strategy == "media_search" and item.selection_score is not None
        ]
        metrics = {
            "media_search_calls": stats["media_search_calls"],
            "metadata_calls": stats["metadata_calls"],
            "search_seconds": round(stats["search_seconds"], 6),
            "average_search_seconds": round(
                stats["search_seconds"] / stats["media_search_calls"], 6
            )
            if stats["media_search_calls"]
            else 0.0,
            "reused_source_count": reused,
            "average_selected_score": round(sum(selected_scores) / len(selected_scores), 6)
            if selected_scores
            else None,
        }
        diagnostics = {
            "query_order": [str(item.id) for item in queries],
            "candidate_format_version": "visual_source_v1",
            "selection_policy": {
                "preferred_provenance": "hierarchical priority",
                "preferred_session_bonus": 0.05,
                "source_note_bonus": 0.03,
                "duration_fit_weight": 0.02,
                "reuse_penalty_weight": 0.08,
            },
        }
        failed = sum(item.source_resolution_status == "failed" for item in decisions)
        resolved = sum(item.source_resolution_status == "resolved" for item in decisions)
        if not decisions or resolved == len(decisions):
            status = "completed"
        elif failed == len(decisions):
            status = "failed"
        elif resolved == 0 and scope is None:
            status = "blocked"
        else:
            status = "completed_with_gaps"
        return decisions, provenance, metrics, diagnostics, status

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

    def _normalize_candidates(
        self, result_sets: list[MediaSearchResultSet], scope: MediaScope
    ) -> list[VisualCandidate]:
        candidates: list[VisualCandidate] = []
        for result_set in result_sets:
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

    def _candidate_payload(self, candidate: VisualCandidate, item: QueryContext) -> dict[str, Any]:
        target = item.target_duration_seconds
        duration_fit = None
        if target and target > 0:
            duration_fit = min(
                candidate.duration_seconds / target,
                target / candidate.duration_seconds,
            )
        payload = VisualSourceSelectionOut(
            project_id=candidate.project_id,
            session_id=candidate.session_id,
            media_id=candidate.media_id,
            source_start_seconds=candidate.start_seconds,
            source_end_seconds=candidate.end_seconds,
            source_duration_seconds=candidate.duration_seconds,
            semantic_score=candidate.semantic_score,
            selection_score=candidate.selection_score,
            rank=candidate.rank,
            session_label=candidate.session_label,
            media_filename=candidate.media_filename,
            temporal_index_id=candidate.temporal_index_id,
            retrieval_candidate_id=candidate.retrieval_candidate_id,
            transcript_preview=candidate.transcript_preview,
            matched_note_ids=list(candidate.matched_note_ids),
            media_search_strategy=candidate.search_strategy,
            duration_fit_ratio=round(duration_fit, 6) if duration_fit is not None else None,
            provenance=candidate.provenance or {},
        )
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
        return {
            "temporal_index_ids": temporal_index_ids,
            "compatibility_signatures": compatibility_signatures,
            "media_search_strategies": sorted({value.strategy for value in result_sets}),
            "embedding_profiles": [
                {"provider": provider, "model": model, "dimensions": dimensions}
                for provider, model, dimensions in embedding_profiles
            ],
            "session_summaries": [summaries[key] for key in sorted(summaries)],
        }
