"""Read-only visual retrieval evaluation and GTA media-scope regression support."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.clients.ideas_hub_client import IdeasHubClient
from app.clients.media_indexer_search import (
    ImageSearchCandidate,
    ImageSearchFilters,
    MediaIndexerError,
    MediaIndexerSearchProvider,
    MediaProjectMetadata,
    MediaSearchCandidate,
    MediaSessionSummary,
)
from app.core.config import Settings, settings

DATASET_SCHEMA_VERSION = "visual_retrieval_dataset_v1"
REPORT_SCHEMA_VERSION = "visual_retrieval_report_v1"
REVIEW_SCHEMA_VERSION = "visual_retrieval_review_v1"
SUMMARY_SCHEMA_VERSION = "visual_retrieval_summary_v1"
HARNESS_VERSION = "gta_visual_retrieval_evaluation_v1"
CANONICAL_PROJECT_NAME = "GTA / Rockstar Canonical Corpus"
UNKNOWN_GAME = "UNKNOWN_GAME"
HUMAN_LABELS = frozenset(
    {
        "GOOD",
        "ACCEPTABLE",
        "BAD",
        "WRONG_GAME",
        "WRONG_CONTEXT",
        "UNRESOLVED",
        "QUERY_BAD",
        "QUERY_AMBIGUOUS",
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def canonical_game_from_session_label(label: str) -> str:
    normalized = label.strip().casefold().replace("-", "_").replace(" ", "_")
    for game in ("gta_vi", "gta_iv", "gta_v"):
        if normalized == game or normalized.startswith(f"{game}_"):
            return game
    return UNKNOWN_GAME


def canonical_game_from_provenance(provenance: dict[str, Any]) -> str:
    value = provenance.get("game")
    if not isinstance(value, str):
        return UNKNOWN_GAME
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "gta_4": "gta_iv",
        "gta_iv": "gta_iv",
        "gta_5": "gta_v",
        "gta_v": "gta_v",
        "gta_6": "gta_vi",
        "gta_vi": "gta_vi",
    }
    return aliases.get(normalized, UNKNOWN_GAME)


def temporal_scope_status(session_id: UUID, allowed: frozenset[UUID]) -> str:
    if not allowed:
        return "PROJECT_WIDE"
    return "IN_SCOPE" if session_id in allowed else "OUT_OF_SCOPE"


def expected_game_status(game: str, expected_game: str | None) -> str:
    if game == UNKNOWN_GAME:
        return UNKNOWN_GAME
    if expected_game is None:
        return "NOT_APPLICABLE"
    return "EXPECTED_GAME" if game == expected_game else "WRONG_GAME"


class StaticFilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_owner: str | None = None
    source_class: str | None = None
    asset_class: str | None = None
    collection: str | None = None
    game: str | None = None

    def client_filter(self) -> ImageSearchFilters:
        return ImageSearchFilters(**self.model_dump())


class StaticScopeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    filters: list[StaticFilterSpec] = Field(default_factory=list)


class EvaluationScopeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_project_id: UUID | None = None
    media_project_name: str | None = CANONICAL_PROJECT_NAME
    media_session_ids: list[UUID] | None = None
    games: list[Literal["gta_iv", "gta_v", "gta_vi"]] = Field(default_factory=list)
    static_images: StaticScopeSpec = Field(default_factory=StaticScopeSpec)

    @model_validator(mode="after")
    def validate_selectors(self) -> EvaluationScopeSpec:
        if self.media_project_id is None and not self.media_project_name:
            raise ValueError("media project id or name is required")
        if self.games and self.media_session_ids:
            raise ValueError("use games or media_session_ids, not both")
        if len(set(self.games)) != len(self.games):
            raise ValueError("games must be unique")
        if self.media_session_ids and len(set(self.media_session_ids)) != len(
            self.media_session_ids
        ):
            raise ValueError("media_session_ids must be unique")
        return self


class EvaluationQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2000)
    intent: str = "illustration"
    importance: str = "preferred"
    expected_game: Literal["gta_iv", "gta_v", "gta_vi"] | None = None
    expected_source_type: Literal["temporal", "static"] | None = None
    notes: str | None = None
    scope: EvaluationScopeSpec | None = None
    preferred_session_id: UUID | None = None
    preferred_game: Literal["gta_iv", "gta_v", "gta_vi"] | None = None


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["visual_retrieval_dataset_v1"] = "visual_retrieval_dataset_v1"
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    default_scope: EvaluationScopeSpec
    queries: list[EvaluationQuerySpec] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_ids(self) -> EvaluationDataset:
        query_ids = [item.id for item in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("query ids must be unique")
        return self

    @property
    def dataset_identity(self) -> str:
        return identity(self.model_dump(mode="json", exclude_none=False))


class CandidateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_identity: str
    source_type: Literal["temporal", "static"]
    project_id: UUID
    media_id: UUID
    session_id: UUID | None = None
    game: str = UNKNOWN_GAME
    scope_status: str
    game_status: str
    rank: int | None = None
    semantic_score: float | None = None
    selection_score: float | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    filename: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    search_identity: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class QueryEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    intent: str
    importance: str
    expected_game: str | None = None
    expected_source_type: str | None = None
    notes: str | None = None
    scope: dict[str, Any]
    selection_status: str
    selection_strategy: str | None = None
    selected: CandidateReport | None = None
    alternatives: list[CandidateReport] = Field(default_factory=list)
    raw_temporal: list[CandidateReport] = Field(default_factory=list)
    raw_static: list[CandidateReport] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    latency_seconds: float = Field(ge=0)


class VisualRetrievalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["visual_retrieval_report_v1"] = "visual_retrieval_report_v1"
    harness_version: Literal["gta_visual_retrieval_evaluation_v1"] = (
        "gta_visual_retrieval_evaluation_v1"
    )
    report_identity: str
    generated_at: datetime
    mode: Literal["dataset", "roteiro", "workflow"]
    input_identity: str
    dataset_identity: str
    media_projects: list[dict[str, Any]]
    discovered_sessions: list[dict[str, Any]]
    strategy_identity: dict[str, Any]
    queries: list[QueryEvaluationReport]
    metrics: dict[str, Any]
    violations: list[dict[str, Any]]
    findings: list[dict[str, Any]] = Field(default_factory=list)


class ResolvedScope(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    project: MediaProjectMetadata
    sessions: tuple[MediaSessionSummary, ...]
    allowed_session_ids: frozenset[UUID]
    allowed_games: frozenset[str]
    explicit_temporal: bool
    static_filters: tuple[ImageSearchFilters, ...]
    static_enabled: bool

    def report_value(self) -> dict[str, Any]:
        return {
            "media_project_id": str(self.project.id),
            "media_project_name": self.project.name,
            "media_session_ids": (
                [str(value) for value in sorted(self.allowed_session_ids, key=str)]
                if self.explicit_temporal
                else None
            ),
            "allowed_games": sorted(self.allowed_games),
            "temporal_semantics": (
                "EXPLICIT_ALLOWED_SESSIONS" if self.explicit_temporal else "PROJECT_WIDE"
            ),
            "static_semantics": (
                "PROJECT_FILTERS" if self.static_enabled else "DISABLED"
            ),
            "static_filters": [item.to_dict() for item in self.static_filters],
        }


def load_dataset(path: Path) -> EvaluationDataset:
    text = path.read_text(encoding="utf-8")
    if path.suffix.casefold() == ".jsonl":
        queries = [json.loads(line) for line in text.splitlines() if line.strip()]
        payload = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "name": path.stem,
            "default_scope": {"media_project_name": CANONICAL_PROJECT_NAME},
            "queries": queries,
        }
    else:
        payload = json.loads(text)
    return EvaluationDataset.model_validate(payload)


def _script_static_scope(raw: Any) -> StaticScopeSpec:
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return StaticScopeSpec()
    names = ("source_owners", "source_classes", "asset_classes", "collections")
    dimensions = [raw.get(name) or [None] for name in names]
    filters = [
        StaticFilterSpec(
            source_owner=values[0],
            source_class=values[1],
            asset_class=values[2],
            collection=values[3],
        )
        for values in product(*dimensions)
    ]
    return StaticScopeSpec(enabled=True, filters=filters)


def dataset_from_script(script: dict[str, Any]) -> EvaluationDataset:
    structure = script["estrutura"]
    media = structure.get("media_context") or {}
    scope = EvaluationScopeSpec(
        media_project_id=media.get("media_project_id"),
        media_project_name=None if media.get("media_project_id") else CANONICAL_PROJECT_NAME,
        media_session_ids=media.get("media_session_ids"),
        static_images=_script_static_scope(media.get("static_images")),
    )
    queries: list[EvaluationQuerySpec] = []
    for block_index, block in enumerate(structure.get("blocos") or []):
        for query_index, raw in enumerate(block.get("visual_queries") or []):
            queries.append(
                EvaluationQuerySpec(
                    id=str(raw.get("id") or f"block-{block_index}-query-{query_index}"),
                    query=str(raw.get("query") or ""),
                    intent=str(raw.get("intent") or "illustration"),
                    importance=str(raw.get("importance") or "preferred"),
                    notes=str(raw.get("narration_excerpt") or "") or None,
                    preferred_session_id=raw.get("preferred_session_id"),
                )
            )
    return EvaluationDataset(
        name=f"roteiro-{script['id']}",
        description="VisualQueries imutáveis do roteiro aprovado",
        default_scope=scope,
        queries=queries,
    )


def dataset_from_visual_plan(plan: dict[str, Any]) -> EvaluationDataset:
    media = plan.get("media_scope") or {}
    scope = EvaluationScopeSpec(
        media_project_id=media.get("media_project_id"),
        media_project_name=None if media.get("media_project_id") else CANONICAL_PROJECT_NAME,
        media_session_ids=media.get("media_session_ids"),
        static_images=_script_static_scope(media.get("static_images")),
    )
    queries = [
        EvaluationQuerySpec(
            id=str(item["visual_query_id"]),
            query=item["query"],
            intent=item["intent"],
            importance=item["importance"],
            notes=item.get("reason"),
        )
        for item in plan.get("decisions") or []
    ]
    return EvaluationDataset(
        name=f"workflow-{plan['workflow_id']}",
        description="VisualQueries e scope do VisualPlan persistido",
        default_scope=scope,
        queries=queries,
    )


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 6),
        "median": round(statistics.median(values), 6),
        "mean": round(statistics.fmean(values), 6),
        "max": round(max(values), 6),
    }


def objective_metrics(queries: list[QueryEvaluationReport]) -> dict[str, Any]:
    observed = [item for item in queries if item.selection_status != "not_observed"]
    selected = [item.selected for item in observed if item.selected is not None]
    identities = [item.candidate_identity for item in selected]
    source_counts = Counter(item.source_type for item in selected)
    asset_counts = Counter(str(item.media_id) for item in selected)
    dominant = max(asset_counts.values(), default=0)
    raw_temporal = [candidate for item in queries for candidate in item.raw_temporal]
    raw_static = [candidate for item in queries for candidate in item.raw_static]
    raw_candidates = [*raw_temporal, *raw_static]
    raw_asset_counts = Counter(str(item.media_id) for item in raw_candidates)
    return {
        "total_queries": len(queries),
        "selection_observed_queries": len(observed),
        "resolved": sum(item.selection_status == "resolved" for item in observed),
        "unresolved": sum(item.selection_status == "unresolved" for item in observed),
        "failed": sum(item.selection_status == "failed" for item in observed),
        "raw_candidate_queries": sum(
            bool(item.raw_temporal or item.raw_static) for item in queries
        ),
        "temporal_selected": source_counts["temporal"],
        "static_selected": source_counts["static"],
        "direct_provenance_selected": sum(
            item.selection_strategy == "preferred_provenance" for item in observed
        ),
        "raw_temporal_candidate_count": len(raw_temporal),
        "raw_static_candidate_count": len(raw_static),
        "average_temporal_candidates": round(len(raw_temporal) / len(queries), 6),
        "average_static_candidates": round(len(raw_static) / len(queries), 6),
        "temporal_candidate_counts": _distribution(
            [float(len(item.raw_temporal)) for item in queries]
        ),
        "static_candidate_counts": _distribution(
            [float(len(item.raw_static)) for item in queries]
        ),
        "search_calls": sum(int(item.diagnostics.get("search_calls", 0)) for item in queries),
        "duplicate_selection_count": len(identities) - len(set(identities)),
        "duplicate_reuse_rate": (
            round((len(identities) - len(set(identities))) / len(identities), 6)
            if identities
            else 0.0
        ),
        "dominant_source_ratio": round(dominant / len(selected), 6) if selected else 0.0,
        "unique_sessions": len({item.session_id for item in selected if item.session_id}),
        "unique_assets": len({item.media_id for item in selected}),
        "raw_unique_sessions": len(
            {item.session_id for item in raw_temporal if item.session_id}
        ),
        "raw_unique_assets": len({item.media_id for item in raw_candidates}),
        "raw_dominant_asset_ratio": (
            round(max(raw_asset_counts.values(), default=0) / len(raw_candidates), 6)
            if raw_candidates
            else 0.0
        ),
        "raw_temporal_scores": _distribution(
            [item.semantic_score for item in raw_temporal if item.semantic_score is not None]
        ),
        "raw_static_scores": _distribution(
            [item.semantic_score for item in raw_static if item.semantic_score is not None]
        ),
        "selected_semantic_scores": _distribution(
            [item.semantic_score for item in selected if item.semantic_score is not None]
        ),
        "latency_seconds": _distribution([item.latency_seconds for item in queries]),
        "temporal_scope_leakage_count": sum(
            candidate.scope_status == "OUT_OF_SCOPE"
            for item in queries
            for candidate in [*item.raw_temporal, *item.alternatives]
            + ([item.selected] if item.selected else [])
            if candidate.source_type == "temporal"
        ),
        "static_scope_leakage_count": sum(
            candidate.source_type == "static" and candidate.scope_status == "OUT_OF_SCOPE"
            for item in queries
            for candidate in [*item.raw_static, *item.alternatives]
            + ([item.selected] if item.selected else [])
        ),
        "static_wrong_game_count": sum(
            candidate.source_type == "static" and candidate.game_status == "WRONG_GAME"
            for item in queries
            for candidate in [*item.raw_static, *item.alternatives]
            + ([item.selected] if item.selected else [])
        ),
    }


class VisualRetrievalEvaluator:
    def __init__(
        self,
        config: Settings = settings,
        *,
        media_provider: MediaIndexerSearchProvider | None = None,
        ideas_client: IdeasHubClient | None = None,
        orchestrator_url: str = "http://localhost:8011",
    ) -> None:
        self.config = config
        self.media = media_provider or MediaIndexerSearchProvider(config)
        self.ideas = ideas_client or IdeasHubClient(config)
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self._projects: tuple[MediaProjectMetadata, ...] | None = None
        self._sessions: dict[UUID, tuple[MediaSessionSummary, ...]] = {}

    async def load_roteiro(self, roteiro_id: UUID) -> tuple[EvaluationDataset, None]:
        script = await self.ideas.obter_roteiro(roteiro_id)
        return dataset_from_script(script), None

    async def load_workflow(
        self, workflow_id: UUID
    ) -> tuple[EvaluationDataset, dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self.orchestrator_url, timeout=30.0) as client:
            response = await client.get(f"/workflow/{workflow_id}/visual-plan")
        if response.status_code == 404:
            raise ValueError(
                "compatible VisualPlan not found; the read-only harness will not create one"
            )
        response.raise_for_status()
        plan = response.json()
        return dataset_from_visual_plan(plan), plan

    async def _project(self, scope: EvaluationScopeSpec) -> MediaProjectMetadata:
        if self._projects is None:
            self._projects = await self.media.list_projects()
        matches = [
            item
            for item in self._projects
            if (scope.media_project_id and item.id == scope.media_project_id)
            or (
                scope.media_project_id is None
                and scope.media_project_name
                and item.name == scope.media_project_name
            )
        ]
        if len(matches) != 1:
            selector = scope.media_project_id or scope.media_project_name
            raise ValueError(
                f"media project selector {selector!s} resolved to {len(matches)} projects"
            )
        return matches[0]

    async def _resolve_scope(self, scope: EvaluationScopeSpec) -> ResolvedScope:
        project = await self._project(scope)
        sessions = self._sessions.get(project.id)
        if sessions is None:
            sessions = await self.media.list_project_sessions(project.id)
            self._sessions[project.id] = sessions
        by_game: dict[str, set[UUID]] = defaultdict(set)
        for session in sessions:
            by_game[canonical_game_from_session_label(session.label)].add(session.id)
        if scope.games:
            allowed = frozenset().union(*(by_game[game] for game in scope.games))
            missing = [game for game in scope.games if not by_game[game]]
            if missing:
                raise ValueError(
                    f"no canonical sessions discovered for games: {', '.join(missing)}"
                )
            explicit = True
        else:
            allowed = frozenset(scope.media_session_ids or [])
            explicit = bool(allowed)
        known = {item.id for item in sessions}
        unknown = allowed - known
        if unknown:
            raise ValueError(
                "explicit sessions are outside the selected project: "
                + ", ".join(str(item) for item in sorted(unknown, key=str))
            )
        filters = tuple(
            item.client_filter() for item in scope.static_images.filters
        ) or (ImageSearchFilters(),)
        return ResolvedScope(
            project=project,
            sessions=sessions,
            allowed_session_ids=allowed,
            allowed_games=frozenset(scope.games),
            explicit_temporal=explicit,
            static_filters=filters,
            static_enabled=scope.static_images.enabled,
        )

    @staticmethod
    def _session_games(scope: ResolvedScope) -> dict[UUID, str]:
        return {
            item.id: canonical_game_from_session_label(item.label) for item in scope.sessions
        }

    def _temporal_candidate(
        self,
        item: MediaSearchCandidate,
        scope: ResolvedScope,
        expected_game: str | None,
        search_identity: dict[str, Any],
        *,
        selection_score: float | None = None,
    ) -> CandidateReport:
        game = self._session_games(scope).get(item.session_id, UNKNOWN_GAME)
        temporal_value: dict[str, Any] = {
            "source_type": "temporal",
            "session_id": str(item.session_id),
            "media_id": str(item.media_id),
            "start_seconds": item.start_seconds,
            "end_seconds": item.end_seconds,
        }
        return CandidateReport(
            candidate_identity=identity(temporal_value),
            source_type="temporal",
            project_id=scope.project.id,
            session_id=item.session_id,
            media_id=item.media_id,
            game=game,
            scope_status=temporal_scope_status(item.session_id, scope.allowed_session_ids),
            game_status=expected_game_status(game, expected_game),
            rank=item.rank,
            semantic_score=item.score,
            selection_score=selection_score,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
            filename=item.media_filename,
            provenance=item.provenance,
            search_identity=search_identity,
            details={
                "temporal_index_id": str(item.temporal_index_id),
                "retrieval_candidate_id": (
                    str(item.retrieval_candidate_id) if item.retrieval_candidate_id else None
                ),
                "transcript_preview": item.transcript_preview,
                "matched_note_ids": [str(value) for value in item.matched_note_ids],
            },
        )

    @staticmethod
    def _static_scope_status(
        provenance: dict[str, Any], filters: tuple[ImageSearchFilters, ...]
    ) -> str:
        active = [item.to_dict() for item in filters if item.to_dict()]
        if not active:
            return "PROJECT_WIDE_STATIC"
        if any(
            all(provenance.get(key) == value for key, value in image_filter.items())
            for image_filter in active
        ):
            return "IN_SCOPE"
        required_fields = {key for image_filter in active for key in image_filter}
        if any(provenance.get(key) is None for key in required_fields):
            return "UNKNOWN_METADATA"
        return "OUT_OF_SCOPE"

    def _static_candidate(
        self,
        item: ImageSearchCandidate,
        scope: ResolvedScope,
        expected_game: str | None,
        search_identity: dict[str, Any],
        *,
        selection_score: float | None = None,
    ) -> CandidateReport:
        game = canonical_game_from_provenance(item.provenance)
        value = {"source_type": "static", "media_id": str(item.media_id)}
        return CandidateReport(
            candidate_identity=identity(value),
            source_type="static",
            project_id=item.project_id,
            media_id=item.media_id,
            game=game,
            scope_status=self._static_scope_status(item.provenance, scope.static_filters),
            game_status=expected_game_status(game, expected_game),
            rank=item.rank,
            semantic_score=item.score,
            selection_score=selection_score,
            filename=item.original_filename,
            provenance=item.provenance,
            search_identity=search_identity,
            details={
                "image_semantic_index_id": str(item.image_semantic_index_id),
                "semantic_index": item.semantic_index,
                "semantic_preview": item.semantic_preview,
                "content_type": item.content_type,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
                "width": item.width,
                "height": item.height,
                "format_name": item.format_name,
            },
        )

    def _selection_candidate(
        self,
        raw: dict[str, Any],
        scope: ResolvedScope,
        expected_game: str | None,
    ) -> CandidateReport:
        source_type = raw.get("source_type")
        if source_type == "static_image":
            provenance = raw.get("provenance") or {}
            game = canonical_game_from_provenance(provenance)
            value = {"source_type": "static", "media_id": str(raw["media_id"])}
            return CandidateReport(
                candidate_identity=identity(value),
                source_type="static",
                project_id=raw["project_id"],
                media_id=raw["media_id"],
                game=game,
                scope_status=self._static_scope_status(provenance, scope.static_filters),
                game_status=expected_game_status(game, expected_game),
                rank=raw.get("rank"),
                semantic_score=raw.get("semantic_score"),
                selection_score=raw.get("selection_score"),
                filename=raw.get("media_filename"),
                provenance=provenance,
                search_identity={"semantic_index": raw.get("semantic_index") or {}},
                details={
                    key: raw.get(key)
                    for key in (
                        "image_semantic_index_id",
                        "semantic_preview",
                        "content_type",
                        "byte_size",
                        "sha256",
                        "width",
                        "height",
                        "format_name",
                    )
                },
            )
        session_id = UUID(str(raw["session_id"]))
        media_id = UUID(str(raw["media_id"]))
        game = self._session_games(scope).get(session_id, UNKNOWN_GAME)
        start = raw.get("source_start_seconds")
        end = raw.get("source_end_seconds")
        temporal_value: dict[str, Any] = {
            "source_type": "temporal",
            "session_id": str(session_id),
            "media_id": str(media_id),
            "start_seconds": start,
            "end_seconds": end,
        }
        return CandidateReport(
            candidate_identity=identity(temporal_value),
            source_type="temporal",
            project_id=raw["project_id"],
            session_id=session_id,
            media_id=media_id,
            game=game,
            scope_status=temporal_scope_status(session_id, scope.allowed_session_ids),
            game_status=expected_game_status(game, expected_game),
            rank=raw.get("rank"),
            semantic_score=raw.get("semantic_score"),
            selection_score=raw.get("selection_score"),
            start_seconds=start,
            end_seconds=end,
            filename=raw.get("media_filename"),
            provenance=raw.get("provenance") or {},
            search_identity={"strategy": raw.get("media_search_strategy")},
            details={
                key: raw.get(key)
                for key in (
                    "temporal_index_id",
                    "retrieval_candidate_id",
                    "transcript_preview",
                    "matched_note_ids",
                    "candidate_start_seconds",
                    "candidate_end_seconds",
                    "duration_fit_ratio",
                )
            },
        )

    async def _evaluate_query(
        self,
        query: EvaluationQuerySpec,
        default_scope: EvaluationScopeSpec,
        decision: dict[str, Any] | None,
    ) -> QueryEvaluationReport:
        started = time.perf_counter()
        scope = await self._resolve_scope(query.scope or default_scope)
        temporal: list[CandidateReport] = []
        static: list[CandidateReport] = []
        diagnostics: dict[str, Any] = {"search_calls": 0, "errors": []}
        try:
            temporal_result = await self.media.search_project(
                scope.project.id,
                query=query.query,
                max_results=self.config.visual_director_search_top_k,
                min_score=self.config.visual_director_min_semantic_score,
                session_ids=(
                    list(sorted(scope.allowed_session_ids, key=str))
                    if scope.explicit_temporal
                    else None
                ),
            )
            diagnostics["search_calls"] += 1
            search_identity = {
                "strategy": temporal_result.strategy,
                "embedding_provider": temporal_result.embedding_provider,
                "embedding_model": temporal_result.embedding_model,
                "embedding_dimensions": temporal_result.embedding_dimensions,
                "searched_sessions": temporal_result.searched_sessions,
                "skipped_sessions": temporal_result.skipped_sessions,
                "session_summaries": list(temporal_result.session_summaries),
            }
            temporal = [
                self._temporal_candidate(item, scope, query.expected_game, search_identity)
                for item in temporal_result.results
            ]
        except MediaIndexerError as exc:
            diagnostics["errors"].append({"branch": "temporal", "category": exc.category})
        if scope.static_enabled:
            seen: set[UUID] = set()
            for image_filter in scope.static_filters:
                try:
                    image_result = await self.media.search_images(
                        scope.project.id,
                        query=query.query,
                        max_results=self.config.visual_director_image_search_top_k,
                        min_score=self.config.visual_director_image_min_semantic_score,
                        filters=image_filter,
                    )
                    diagnostics["search_calls"] += 1
                    search_identity = {
                        "filters": image_filter.to_dict(),
                        "searched_indexes": image_result.searched_indexes,
                        "compatibility_groups": image_result.compatibility_groups,
                    }
                    for item in image_result.results:
                        if item.media_id not in seen:
                            seen.add(item.media_id)
                            static.append(
                                self._static_candidate(
                                    item, scope, query.expected_game, search_identity
                                )
                            )
                except MediaIndexerError as exc:
                    diagnostics["errors"].append(
                        {"branch": "static", "category": exc.category}
                    )
        selected = None
        alternatives: list[CandidateReport] = []
        selection_status = "not_observed"
        selection_strategy = None
        if decision is not None:
            selection_status = str(decision.get("source_resolution_status") or "unresolved")
            selection_strategy = decision.get("selection_strategy")
            if decision.get("selected_source"):
                selected = self._selection_candidate(
                    decision["selected_source"], scope, query.expected_game
                )
            alternatives = [
                self._selection_candidate(item, scope, query.expected_game)
                for item in decision.get("alternatives") or []
            ]
            diagnostics["visual_plan"] = {
                "reason": decision.get("reason"),
                "diagnostics": decision.get("diagnostics"),
                "score_components": decision.get("score_components"),
            }
        preferred = query.preferred_session_id
        if preferred is None and query.preferred_game:
            preferred = next(
                (
                    item.id
                    for item in sorted(scope.sessions, key=lambda value: value.label)
                    if canonical_game_from_session_label(item.label) == query.preferred_game
                ),
                None,
            )
        diagnostics["preferred_session_id"] = str(preferred) if preferred else None
        diagnostics["preferred_session_in_scope"] = (
            preferred in scope.allowed_session_ids
            if preferred is not None and scope.explicit_temporal
            else None
        )
        diagnostics["raw_temporal_scope_leakage"] = [
            item.candidate_identity for item in temporal if item.scope_status == "OUT_OF_SCOPE"
        ]
        diagnostics["selection_scope_leakage"] = [
            item.candidate_identity
            for item in ([selected] if selected else []) + alternatives
            if item.source_type == "temporal" and item.scope_status == "OUT_OF_SCOPE"
        ]
        return QueryEvaluationReport(
            id=query.id,
            query=query.query,
            intent=query.intent,
            importance=query.importance,
            expected_game=query.expected_game,
            expected_source_type=query.expected_source_type,
            notes=query.notes,
            scope=scope.report_value(),
            selection_status=selection_status,
            selection_strategy=selection_strategy,
            selected=selected,
            alternatives=alternatives,
            raw_temporal=temporal,
            raw_static=static,
            diagnostics=diagnostics,
            latency_seconds=round(time.perf_counter() - started, 6),
        )

    def strategy_identity(self, plan: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "visual_director": (
                plan.get("strategy_version")
                if plan
                else self.config.visual_director_strategy_version
            ),
            "media_search": self.config.visual_director_media_search_strategy_version,
            "hybrid": self.config.visual_director_hybrid_strategy_version,
            "fusion": self.config.hybrid_visual_fusion_version,
            "diversity": self.config.visual_diversity_hybrid_policy_version,
            "thresholds": {
                "temporal_min_semantic_score": self.config.visual_director_min_semantic_score,
                "static_min_semantic_score": self.config.visual_director_image_min_semantic_score,
                "temporal_top_k": self.config.visual_director_search_top_k,
                "static_top_k": self.config.visual_director_image_search_top_k,
                "rrf_k": self.config.hybrid_visual_fusion_rrf_k,
                "video_weight": self.config.hybrid_visual_fusion_video_weight,
                "image_weight": self.config.hybrid_visual_fusion_image_weight,
            },
            "visual_plan_identity": (
                {
                    "id": plan.get("id"),
                    "request_fingerprint": plan.get("request_fingerprint"),
                    "fingerprint": plan.get("fingerprint"),
                    "media_index_provenance": plan.get("media_index_provenance"),
                }
                if plan
                else None
            ),
        }

    async def evaluate(
        self,
        dataset: EvaluationDataset,
        *,
        mode: Literal["dataset", "roteiro", "workflow"],
        input_identity: str,
        plan: dict[str, Any] | None = None,
    ) -> VisualRetrievalReport:
        decisions = {
            str(item["visual_query_id"]): item for item in (plan or {}).get("decisions") or []
        }
        queries = [
            await self._evaluate_query(item, dataset.default_scope, decisions.get(item.id))
            for item in dataset.queries
        ]
        violations: list[dict[str, Any]] = []
        for query in queries:
            for layer, candidates in (
                ("raw_temporal", query.raw_temporal),
                ("raw_static", query.raw_static),
                ("selected", [query.selected] if query.selected else []),
                ("alternatives", query.alternatives),
            ):
                for candidate in candidates:
                    if (
                        candidate.source_type == "temporal"
                        and candidate.scope_status == "OUT_OF_SCOPE"
                    ):
                        violations.append(
                            {
                                "category": "MEDIA_SCOPE_LEAKAGE",
                                "query_id": query.id,
                                "layer": layer,
                                "candidate_identity": candidate.candidate_identity,
                                "session_id": str(candidate.session_id),
                            }
                        )
                    if (
                        candidate.source_type == "static"
                        and candidate.scope_status == "OUT_OF_SCOPE"
                    ):
                        violations.append(
                            {
                                "category": "STATIC_SCOPE_LEAKAGE",
                                "query_id": query.id,
                                "layer": layer,
                                "candidate_identity": candidate.candidate_identity,
                                "media_id": str(candidate.media_id),
                            }
                        )
        discovered = [
            {
                "id": str(item.id),
                "project_id": str(item.project_id),
                "media_id": str(item.media_id),
                "label": item.label,
                "status": item.status,
                "game": canonical_game_from_session_label(item.label),
            }
            for values in self._sessions.values()
            for item in values
        ]
        projects = [
            {"id": str(item.id), "name": item.name, "description": item.description}
            for item in self._projects or ()
            if item.id in self._sessions
        ]
        strategy = self.strategy_identity(plan)
        report_core = {
            "harness_version": HARNESS_VERSION,
            "mode": mode,
            "input_identity": input_identity,
            "dataset_identity": dataset.dataset_identity,
            "strategy_identity": strategy,
            "query_results": [
                item.model_dump(mode="json", exclude={"latency_seconds"}) for item in queries
            ],
        }
        findings = [
            {
                "category": "STATIC_SCOPE_CONTRACT",
                "severity": "info",
                "message": (
                    "Static assets are project-scoped. Dataset probes may use canonical game "
                    "provenance filters, but current ScriptMediaContext exposes no game filter."
                ),
            }
        ]
        return VisualRetrievalReport(
            report_identity=identity(report_core),
            generated_at=datetime.now(UTC),
            mode=mode,
            input_identity=input_identity,
            dataset_identity=dataset.dataset_identity,
            media_projects=projects,
            discovered_sessions=discovered,
            strategy_identity=strategy,
            queries=queries,
            metrics=objective_metrics(queries),
            violations=violations,
            findings=findings,
        )


REVIEW_COLUMNS = (
    "review_schema_version",
    "report_identity",
    "dataset_identity",
    "query_id",
    "query",
    "expected_game",
    "query_label",
    "selected_source_type",
    "selected_candidate_identity",
    "selected_label",
    "alternative_1_identity",
    "alternative_1_label",
    "alternative_2_identity",
    "alternative_2_label",
    "raw_temporal_1_identity",
    "raw_temporal_1_label",
    "raw_temporal_2_identity",
    "raw_temporal_2_label",
    "raw_temporal_3_identity",
    "raw_temporal_3_label",
    "raw_static_1_identity",
    "raw_static_1_label",
    "raw_static_2_identity",
    "raw_static_2_label",
    "raw_static_3_identity",
    "raw_static_3_label",
    "reviewer_notes",
)


def render_review_csv(report: VisualRetrievalReport) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS)
    writer.writeheader()
    for query in report.queries:
        alternatives = [*query.alternatives, None, None]
        temporal: list[CandidateReport | None] = [*query.raw_temporal[:3], None, None, None]
        static: list[CandidateReport | None] = [*query.raw_static[:3], None, None, None]
        row = {
            "review_schema_version": REVIEW_SCHEMA_VERSION,
            "report_identity": report.report_identity,
            "dataset_identity": report.dataset_identity,
            "query_id": query.id,
            "query": query.query,
            "expected_game": query.expected_game or "",
            "query_label": "",
            "selected_source_type": query.selected.source_type if query.selected else "",
            "selected_candidate_identity": (
                query.selected.candidate_identity if query.selected else ""
            ),
            "selected_label": "",
            "alternative_1_identity": (
                alternatives[0].candidate_identity if alternatives[0] else ""
            ),
            "alternative_1_label": "",
            "alternative_2_identity": (
                alternatives[1].candidate_identity if alternatives[1] else ""
            ),
            "alternative_2_label": "",
            "reviewer_notes": "",
        }
        for index in range(3):
            temporal_candidate = temporal[index]
            row[f"raw_temporal_{index + 1}_identity"] = (
                temporal_candidate.candidate_identity if temporal_candidate else ""
            )
            row[f"raw_temporal_{index + 1}_label"] = ""
            static_candidate = static[index]
            row[f"raw_static_{index + 1}_identity"] = (
                static_candidate.candidate_identity if static_candidate else ""
            )
            row[f"raw_static_{index + 1}_label"] = ""
        writer.writerow(row)
    return stream.getvalue()


def parse_review_labels(path: Path, report: VisualRetrievalReport) -> list[dict[str, str]]:
    if path.suffix.casefold() == ".jsonl":
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
    query_ids = {item.id for item in report.queries}
    seen: set[str] = set()
    parsed: list[dict[str, str]] = []
    for row in rows:
        query_id = str(row.get("query_id") or "")
        if query_id not in query_ids or query_id in seen:
            raise ValueError(f"invalid or duplicate query_id in review: {query_id!r}")
        if row.get("report_identity") != report.report_identity:
            raise ValueError("review report identity does not match report")
        if row.get("dataset_identity") != report.dataset_identity:
            raise ValueError("review dataset identity does not match report")
        if row.get("review_schema_version") != REVIEW_SCHEMA_VERSION:
            raise ValueError("unsupported review schema version")
        label_fields = [key for key in REVIEW_COLUMNS if key.endswith("_label")]
        for field in label_fields:
            value = str(row.get(field) or "").strip().upper()
            if value and value not in HUMAN_LABELS:
                raise ValueError(f"unsupported human label {value!r}")
            if field == "query_label" and value and value not in {
                "QUERY_BAD",
                "QUERY_AMBIGUOUS",
            }:
                raise ValueError("query_label only accepts query-quality labels")
            if field != "query_label" and value in {"QUERY_BAD", "QUERY_AMBIGUOUS"}:
                raise ValueError("query-quality labels belong in query_label")
            identity_field = (
                "selected_candidate_identity"
                if field == "selected_label"
                else field.removesuffix("_label") + "_identity"
            )
            if (
                value
                and field != "query_label"
                and not row.get(identity_field)
                and not (field == "selected_label" and value == "UNRESOLVED")
            ):
                raise ValueError(f"{field} labels a missing candidate")
            row[field] = value
        seen.add(query_id)
        parsed.append({key: str(value or "") for key, value in row.items()})
    return parsed


def labeled_metrics(
    report: VisualRetrievalReport, rows: list[dict[str, str]]
) -> dict[str, Any]:
    by_query = {item.id: item for item in report.queries}
    selected_rows = [row for row in rows if row.get("selected_label")]
    counts = Counter(row["selected_label"] for row in selected_rows)
    query_label_counts = Counter(row["query_label"] for row in rows if row.get("query_label"))
    raw_label_counts = {
        source_type: Counter(
            row[f"raw_{source_type}_{index}_label"]
            for row in rows
            for index in range(1, 4)
            if row.get(f"raw_{source_type}_{index}_label")
        )
        for source_type in ("temporal", "static")
    }

    def rates(values: list[dict[str, str]]) -> dict[str, Any]:
        total = len(values)
        local = Counter(item["selected_label"] for item in values)
        return {
            "labeled": total,
            "good_rate": round(local["GOOD"] / total, 6) if total else None,
            "good_acceptable_rate": (
                round((local["GOOD"] + local["ACCEPTABLE"]) / total, 6)
                if total
                else None
            ),
            "bad_rate": round(local["BAD"] / total, 6) if total else None,
            "wrong_game_rate": round(local["WRONG_GAME"] / total, 6) if total else None,
            "wrong_context_rate": (
                round(local["WRONG_CONTEXT"] / total, 6) if total else None
            ),
            "unresolved_rate": round(local["UNRESOLVED"] / total, 6) if total else None,
        }

    dimensions: dict[str, dict[str, list[dict[str, str]]]] = {
        key: defaultdict(list) for key in ("intent", "source_type", "game")
    }
    for row in selected_rows:
        query = by_query[row["query_id"]]
        dimensions["intent"][query.intent].append(row)
        dimensions["source_type"][query.selected.source_type if query.selected else "none"].append(
            row
        )
        dimensions["game"][query.selected.game if query.selected else UNKNOWN_GAME].append(row)
    label_rank = {"GOOD": 2, "ACCEPTABLE": 1, "BAD": 0, "WRONG_GAME": 0, "WRONG_CONTEXT": 0}
    comparisons: Counter[str] = Counter()
    for row in selected_rows:
        selected_rank = label_rank.get(row["selected_label"])
        for field in ("alternative_1_label", "alternative_2_label"):
            alternative_rank = label_rank.get(row.get(field, ""))
            if selected_rank is None or alternative_rank is None:
                continue
            if alternative_rank > selected_rank:
                comparisons["alternative_better"] += 1
            elif alternative_rank < selected_rank:
                comparisons["selected_better"] += 1
            else:
                comparisons["equal"] += 1
    return {
        "labeled_queries": len(selected_rows),
        "label_counts": dict(sorted(counts.items())),
        "query_label_counts": dict(sorted(query_label_counts.items())),
        "raw_candidate_label_counts": {
            key: dict(sorted(values.items())) for key, values in raw_label_counts.items()
        },
        **rates(selected_rows),
        "by_intent": {
            key: rates(values) for key, values in sorted(dimensions["intent"].items())
        },
        "by_source_type": {
            key: rates(values) for key, values in sorted(dimensions["source_type"].items())
        },
        "by_game": {
            key: rates(values) for key, values in sorted(dimensions["game"].items())
        },
        "selected_vs_alternatives": dict(comparisons),
    }


def render_markdown(report: VisualRetrievalReport) -> str:
    metrics = report.metrics
    lines = [
        "# Visual retrieval evaluation",
        "",
        f"- Report: `{report.report_identity}`",
        f"- Dataset: `{report.dataset_identity}`",
        f"- Mode: `{report.mode}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Queries: {metrics['total_queries']}",
        f"- Raw candidate queries: {metrics['raw_candidate_queries']}",
        f"- Temporal scope leakages: {metrics['temporal_scope_leakage_count']}",
        "",
        "Project-wide temporal scope intentionally permits every session in the project. "
        "Only `EXPLICIT_ALLOWED_SESSIONS` is a game-isolation guarantee.",
        "",
        "## Query results",
        "",
    ]
    for item in report.queries:
        lines.extend(
            [
                f"### {item.id}",
                "",
                f"- Query: {item.query}",
                f"- Intent / importance: `{item.intent}` / `{item.importance}`",
                f"- Expected game: `{item.expected_game or 'not specified'}`",
                f"- Scope: `{item.scope['temporal_semantics']}`; "
                f"games `{', '.join(item.scope['allowed_games']) or 'project-wide'}`",
                f"- Selection: `{item.selection_status}` / "
                f"`{item.selection_strategy or 'not observed'}`",
                f"- Raw candidates: temporal={len(item.raw_temporal)}, "
                f"static={len(item.raw_static)}",
            ]
        )
        if item.selected:
            lines.append(
                f"- Selected: `{item.selected.source_type}` `{item.selected.game}` "
                f"score={item.selected.semantic_score} scope={item.selected.scope_status}"
            )
        for label, candidates in (
            ("Temporal top", item.raw_temporal[:3]),
            ("Static top", item.raw_static[:3]),
        ):
            if candidates:
                rendered = "; ".join(
                    f"#{candidate.rank} {candidate.game} score={candidate.semantic_score} "
                    f"scope={candidate.scope_status}"
                    for candidate in candidates
                )
                lines.append(f"- {label}: {rendered}")
        lines.append("")
    if report.violations:
        lines.extend(["## BLOCKER violations", ""])
        lines.extend(f"- `{canonical_json(item)}`" for item in report.violations)
        lines.append("")
    return "\n".join(lines)


def load_report(path: Path) -> VisualRetrievalReport:
    try:
        return VisualRetrievalReport.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ValueError("invalid visual retrieval report") from exc


def comparison(baseline: VisualRetrievalReport, candidate: VisualRetrievalReport) -> dict[str, Any]:
    if baseline.dataset_identity != candidate.dataset_identity:
        raise ValueError("reports use different datasets")
    keys = (
        "resolved",
        "unresolved",
        "failed",
        "raw_candidate_queries",
        "raw_unique_sessions",
        "raw_unique_assets",
        "raw_dominant_asset_ratio",
        "duplicate_reuse_rate",
        "dominant_source_ratio",
        "temporal_scope_leakage_count",
        "static_scope_leakage_count",
        "static_wrong_game_count",
    )
    return {
        "schema_version": "visual_retrieval_comparison_v1",
        "dataset_identity": baseline.dataset_identity,
        "baseline_report_identity": baseline.report_identity,
        "candidate_report_identity": candidate.report_identity,
        "metrics": {
            key: {
                "baseline": baseline.metrics.get(key),
                "candidate": candidate.metrics.get(key),
                "delta": (
                    candidate.metrics[key] - baseline.metrics[key]
                    if isinstance(candidate.metrics.get(key), int | float)
                    and isinstance(baseline.metrics.get(key), int | float)
                    and not isinstance(candidate.metrics.get(key), bool)
                    and not isinstance(baseline.metrics.get(key), bool)
                    else None
                ),
            }
            for key in keys
        },
    }
