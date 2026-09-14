"""Typed HTTP boundary for Media Indexer's public Media Search APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.core.config import Settings, settings


class MediaIndexerError(RuntimeError):
    """Safe provider error with a stable category."""

    category = "media_indexer_error"
    retryable = False


class MediaIndexerNotFoundError(MediaIndexerError):
    category = "not_found"


class MediaIndexerConflictError(MediaIndexerError):
    category = "incompatible_index"


class MediaIndexerContractError(MediaIndexerError):
    category = "invalid_contract"


class MediaIndexerUnavailableError(MediaIndexerError):
    category = "unavailable"
    retryable = True


@dataclass(frozen=True, slots=True)
class MediaSessionMetadata:
    id: UUID
    project_id: UUID
    media_id: UUID
    label: str
    status: str
    media_filename: str
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class MediaAssetMetadata:
    id: UUID
    project_id: UUID
    media_kind: str
    content_type: str | None
    byte_size: int
    sha256: str
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class MediaProjectMetadata:
    id: UUID
    name: str
    description: str | None


@dataclass(frozen=True, slots=True)
class MediaSessionSummary:
    id: UUID
    project_id: UUID
    media_id: UUID
    label: str
    status: str


@dataclass(frozen=True, slots=True)
class ImageSearchFilters:
    source_owner: str | None = None
    source_class: str | None = None
    asset_class: str | None = None
    collection: str | None = None
    game: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "source_owner": self.source_owner,
                "source_class": self.source_class,
                "asset_class": self.asset_class,
                "collection": self.collection,
                "game": self.game,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class ImageSearchCandidate:
    project_id: UUID
    media_id: UUID
    score: float
    rank: int
    original_filename: str
    content_type: str
    byte_size: int
    sha256: str
    width: int
    height: int
    format_name: str
    provenance: dict[str, Any]
    semantic_preview: str
    image_semantic_index_id: UUID
    semantic_index: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ImageSearchResultSet:
    query: str
    project_id: UUID
    results: tuple[ImageSearchCandidate, ...]
    searched_indexes: int
    compatibility_groups: int
    filters: ImageSearchFilters


@dataclass(frozen=True, slots=True)
class MediaSearchCandidate:
    session_id: UUID
    media_id: UUID
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    score: float
    rank: int
    session_label: str
    media_filename: str
    temporal_index_id: UUID
    retrieval_candidate_id: UUID | None
    transcript_preview: str | None
    matched_note_ids: tuple[UUID, ...]
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MediaSearchResultSet:
    query: str
    scope: str
    project_id: UUID
    session_id: UUID | None
    results: tuple[MediaSearchCandidate, ...]
    searched_sessions: int
    skipped_sessions: int
    session_summaries: tuple[dict[str, Any], ...]
    strategy: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int


class MediaSearchProvider(Protocol):
    async def get_asset(self, media_id: UUID) -> MediaAssetMetadata: ...

    async def get_session(self, session_id: UUID) -> MediaSessionMetadata: ...

    async def search_session(
        self,
        session_id: UUID,
        *,
        query: str,
        max_results: int,
        min_score: float,
        max_duration_seconds: float | None = None,
    ) -> MediaSearchResultSet: ...

    async def search_project(
        self,
        project_id: UUID,
        *,
        query: str,
        max_results: int,
        min_score: float,
        session_ids: list[UUID] | None = None,
        max_duration_seconds: float | None = None,
    ) -> MediaSearchResultSet: ...

    async def search_images(
        self,
        project_id: UUID,
        *,
        query: str,
        max_results: int,
        min_score: float,
        filters: ImageSearchFilters,
    ) -> ImageSearchResultSet: ...


class _MediaOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    original_filename: str
    duration_seconds: float | None = Field(default=None, ge=0)


class _SessionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    project_id: UUID
    media_id: UUID
    label: str
    status: str
    media: _MediaOut


class _MediaAssetOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    project_id: UUID
    media_kind: str
    content_type: str | None = None
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class _ProjectOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    name: str
    description: str | None = None


class _SessionSummaryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    project_id: UUID
    media_id: UUID
    label: str
    status: str


class _SearchCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: UUID
    media_id: UUID
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    score: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    session_label: str
    media_filename: str
    temporal_index_id: UUID
    retrieval_candidate_id: UUID | None = None
    transcript_preview: str | None = None
    matched_note_ids: list[UUID] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class _SessionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    label: str
    searched: bool
    result_count: int = Field(default=0, ge=0)
    skip_reason: str | None = None


class _SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    scope: str
    project_id: UUID
    session_id: UUID | None = None
    results: list[_SearchCandidate]
    searched_sessions: int = Field(ge=0)
    skipped_sessions: int = Field(ge=0)
    session_summaries: list[_SessionSummary] = Field(default_factory=list)
    strategy: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int = Field(gt=0)


class _ImageSemanticIndex(BaseModel):
    model_config = ConfigDict(extra="ignore")
    index_id: UUID
    strategy: str
    prompt_version: str
    result_schema_version: str
    canonical_text_version: str
    vision_provider: str
    vision_model: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int = Field(gt=0)
    configuration_fingerprint: str


class _ImageSearchCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_type: str
    project_id: UUID
    media_id: UUID
    score: float = Field(ge=-1, le=1)
    rank: int = Field(ge=1)
    original_filename: str
    content_type: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format_name: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    semantic_preview: str
    semantic_index: _ImageSemanticIndex


class _ImageSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    project_id: UUID
    results: list[_ImageSearchCandidate]
    searched_indexes: int = Field(ge=0)
    compatibility_groups: int = Field(ge=0)


def _safe_provenance(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "compatibility_signature",
        "selection_reason",
        "score_components",
        "cluster_start_seconds",
        "cluster_end_seconds",
        "semantic_text_strategy",
        "semantic_source_types",
        "semantic_support",
        "overlapping_timeline_note_ids",
    }
    return {key: value[key] for key in sorted(allowed & value.keys())}


class MediaIndexerSearchProvider:
    def __init__(
        self,
        config: Settings = settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._base_url = config.media_indexer_api_url
        self._timeout = config.media_indexer_api_timeout_seconds
        self._max_retries = config.media_indexer_http_max_retries
        self._backoff = config.media_indexer_http_backoff_seconds
        self._transport = transport
        self._sleep = sleep

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> Any:
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.request(method, path, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self._max_retries:
                    await self._sleep(self._backoff * (2**attempt))
                    continue
                raise MediaIndexerUnavailableError("Media Indexer is unavailable") from exc

            if response.status_code >= 500:
                if attempt < self._max_retries:
                    await self._sleep(self._backoff * (2**attempt))
                    continue
                raise MediaIndexerUnavailableError("Media Indexer returned a server error")
            if response.status_code == 404:
                raise MediaIndexerNotFoundError("Media Indexer resource was not found")
            if response.status_code == 409:
                raise MediaIndexerConflictError("Media Indexer has no compatible temporal index")
            if response.status_code >= 400:
                raise MediaIndexerContractError(
                    f"Media Indexer rejected the request with status {response.status_code}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise MediaIndexerContractError("Media Indexer returned invalid JSON") from exc
        raise AssertionError("unreachable")

    async def get_session(self, session_id: UUID) -> MediaSessionMetadata:
        raw = await self._request("GET", f"/sessions/{session_id}")
        try:
            parsed = _SessionOut.model_validate(raw)
        except ValidationError as exc:
            raise MediaIndexerContractError("Invalid Media Indexer session response") from exc
        if parsed.media.id != parsed.media_id:
            raise MediaIndexerContractError("Media Indexer session media identity is inconsistent")
        return MediaSessionMetadata(
            id=parsed.id,
            project_id=parsed.project_id,
            media_id=parsed.media_id,
            label=parsed.label,
            status=parsed.status,
            media_filename=parsed.media.original_filename,
            duration_seconds=parsed.media.duration_seconds,
        )

    async def list_projects(self) -> tuple[MediaProjectMetadata, ...]:
        raw = await self._request("GET", "/projects")
        try:
            parsed = TypeAdapter(list[_ProjectOut]).validate_python(raw)
        except ValidationError as exc:
            raise MediaIndexerContractError("Invalid Media Indexer projects response") from exc
        return tuple(
            MediaProjectMetadata(id=item.id, name=item.name, description=item.description)
            for item in parsed
        )

    async def list_project_sessions(
        self, project_id: UUID
    ) -> tuple[MediaSessionSummary, ...]:
        raw = await self._request("GET", f"/projects/{project_id}/sessions")
        try:
            parsed = TypeAdapter(list[_SessionSummaryOut]).validate_python(raw)
        except ValidationError as exc:
            raise MediaIndexerContractError("Invalid Media Indexer sessions response") from exc
        if any(item.project_id != project_id for item in parsed):
            raise MediaIndexerContractError(
                "Media Indexer session project identity is inconsistent"
            )
        return tuple(
            MediaSessionSummary(
                id=item.id,
                project_id=item.project_id,
                media_id=item.media_id,
                label=item.label,
                status=item.status,
            )
            for item in parsed
        )

    async def get_asset(self, media_id: UUID) -> MediaAssetMetadata:
        raw = await self._request("GET", f"/media-assets/{media_id}")
        try:
            parsed = _MediaAssetOut.model_validate(raw)
        except ValidationError as exc:
            raise MediaIndexerContractError("Invalid Media Indexer asset response") from exc
        if parsed.id != media_id:
            raise MediaIndexerContractError("Media Indexer asset identity is inconsistent")
        return MediaAssetMetadata(
            id=parsed.id,
            project_id=parsed.project_id,
            media_kind=parsed.media_kind,
            content_type=parsed.content_type,
            byte_size=parsed.byte_size,
            sha256=parsed.sha256,
            width=parsed.width,
            height=parsed.height,
        )

    async def search_session(
        self,
        session_id: UUID,
        *,
        query: str,
        max_results: int,
        min_score: float,
        max_duration_seconds: float | None = None,
    ) -> MediaSearchResultSet:
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "min_score": min_score,
        }
        if max_duration_seconds is not None:
            payload["max_duration_seconds"] = max_duration_seconds
        raw = await self._request("POST", f"/media-search/sessions/{session_id}", json=payload)
        return self._parse_search(raw, expected_scope="session", expected_id=session_id)

    async def search_project(
        self,
        project_id: UUID,
        *,
        query: str,
        max_results: int,
        min_score: float,
        session_ids: list[UUID] | None = None,
        max_duration_seconds: float | None = None,
    ) -> MediaSearchResultSet:
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "min_score": min_score,
        }
        if session_ids:
            payload["session_ids"] = [str(item) for item in session_ids]
        if max_duration_seconds is not None:
            payload["max_duration_seconds"] = max_duration_seconds
        raw = await self._request("POST", f"/media-search/projects/{project_id}", json=payload)
        return self._parse_search(raw, expected_scope="project", expected_id=project_id)

    async def search_images(
        self,
        project_id: UUID,
        *,
        query: str,
        max_results: int,
        min_score: float,
        filters: ImageSearchFilters,
    ) -> ImageSearchResultSet:
        raw = await self._request(
            "POST",
            f"/image-search/projects/{project_id}",
            json={
                "query": query,
                "limit": max_results,
                "min_score": min_score,
                "filters": filters.to_dict(),
            },
        )
        try:
            parsed = _ImageSearchResponse.model_validate(raw)
        except ValidationError as exc:
            raise MediaIndexerContractError("Invalid Media Indexer Image Search response") from exc
        if parsed.project_id != project_id:
            raise MediaIndexerContractError("Media Indexer image project identity is inconsistent")
        candidates: list[ImageSearchCandidate] = []
        for item in parsed.results:
            if item.source_type != "image" or item.project_id != project_id:
                raise MediaIndexerContractError("Media Indexer returned an invalid image source")
            semantic = item.semantic_index.model_dump(mode="json")
            candidates.append(
                ImageSearchCandidate(
                    project_id=item.project_id,
                    media_id=item.media_id,
                    score=item.score,
                    rank=item.rank,
                    original_filename=item.original_filename,
                    content_type=item.content_type,
                    byte_size=item.byte_size,
                    sha256=item.sha256,
                    width=item.width,
                    height=item.height,
                    format_name=item.format_name,
                    provenance=dict(item.provenance),
                    semantic_preview=item.semantic_preview,
                    image_semantic_index_id=item.semantic_index.index_id,
                    semantic_index=semantic,
                )
            )
        return ImageSearchResultSet(
            query=parsed.query,
            project_id=parsed.project_id,
            results=tuple(candidates),
            searched_indexes=parsed.searched_indexes,
            compatibility_groups=parsed.compatibility_groups,
            filters=filters,
        )

    def _parse_search(
        self, raw: Any, *, expected_scope: str, expected_id: UUID
    ) -> MediaSearchResultSet:
        try:
            parsed = _SearchResponse.model_validate(raw)
        except ValidationError as exc:
            raise MediaIndexerContractError("Invalid Media Indexer search response") from exc
        if parsed.scope != expected_scope:
            raise MediaIndexerContractError("Media Indexer search scope is inconsistent")
        if expected_scope == "project" and parsed.project_id != expected_id:
            raise MediaIndexerContractError("Media Indexer project identity is inconsistent")
        if expected_scope == "session" and parsed.session_id != expected_id:
            raise MediaIndexerContractError("Media Indexer session identity is inconsistent")
        candidates: list[MediaSearchCandidate] = []
        for item in parsed.results:
            if item.end_seconds <= item.start_seconds:
                raise MediaIndexerContractError("Media Indexer returned an invalid source range")
            if abs(item.duration_seconds - (item.end_seconds - item.start_seconds)) > 0.01:
                raise MediaIndexerContractError("Media Indexer returned an inconsistent duration")
            candidates.append(
                MediaSearchCandidate(
                    session_id=item.session_id,
                    media_id=item.media_id,
                    start_seconds=item.start_seconds,
                    end_seconds=item.end_seconds,
                    duration_seconds=item.duration_seconds,
                    score=item.score,
                    rank=item.rank,
                    session_label=item.session_label,
                    media_filename=item.media_filename,
                    temporal_index_id=item.temporal_index_id,
                    retrieval_candidate_id=item.retrieval_candidate_id,
                    transcript_preview=item.transcript_preview,
                    matched_note_ids=tuple(item.matched_note_ids),
                    provenance=_safe_provenance(item.provenance),
                )
            )
        summaries = tuple(
            {
                "id": str(item.id),
                "label": item.label,
                "searched": item.searched,
                "result_count": item.result_count,
                "skip_reason": item.skip_reason,
            }
            for item in parsed.session_summaries
        )
        return MediaSearchResultSet(
            query=parsed.query,
            scope=parsed.scope,
            project_id=parsed.project_id,
            session_id=parsed.session_id,
            results=tuple(candidates),
            searched_sessions=parsed.searched_sessions,
            skipped_sessions=parsed.skipped_sessions,
            session_summaries=summaries,
            strategy=parsed.strategy,
            embedding_provider=parsed.embedding_provider,
            embedding_model=parsed.embedding_model,
            embedding_dimensions=parsed.embedding_dimensions,
        )
