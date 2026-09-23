"""Typed read-only HTTP boundary for Ideas Hub production-script snapshots."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, ValidationError

from app.core.config import Settings, settings


class IdeasHubClientError(RuntimeError):
    pass


class IdeasHubTransientError(IdeasHubClientError):
    pass


class IdeasHubNotFoundError(IdeasHubClientError):
    pass


class IdeasHubContractError(IdeasHubClientError):
    pass


class IdeasHubBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    tipo: str = Field(min_length=1)
    duracao_segundos: int = Field(gt=0)
    conteudo: str = Field(min_length=1)
    display_title: str | None = Field(default=None, min_length=1, max_length=120)
    visual_queries: list[dict[str, Any]] = Field(default_factory=list)


class IdeasHubScriptStructure(BaseModel):
    model_config = ConfigDict(extra="allow")
    titulo: str = Field(min_length=1)
    duracao_total_segundos: int = Field(gt=0)
    blocos: list[IdeasHubBlock] = Field(min_length=1)


class IdeasHubScript(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: uuid.UUID
    ideia_id: uuid.UUID
    versao: int = Field(ge=1)
    estrutura: IdeasHubScriptStructure
    status: str
    parent_roteiro_id: uuid.UUID | None = None
    revision_kind: str = "legacy"
    criado_em: dt.datetime | None = None


class IdeasHubIdea(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: uuid.UUID
    perfil_producao: str | None = None


class IdeasHubProductionCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery_mode: Literal["rendered_video", "manual_edit_package"]
    visual_mode: Literal["semantic_match", "background_fill", "storyboard"]
    requires_media_scope: StrictBool
    caption_policy: Literal["burned_in", "none"]
    render_profile: str | None
    version: str = Field(min_length=1)


class IdeasHubVisualReadiness(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    required: bool = True
    mode: Literal["semantic_match", "background_fill", "storyboard"] = "semantic_match"
    policy_version: str | None = None
    policy_identity: str | None = None
    speakable_content_identity: str | None = None
    block_count: int = Field(default=0, ge=0)
    visual_beat_count: int = Field(default=0, ge=0)
    textual_coverage_ratio: float = Field(default=0, ge=0, le=1)
    missing_ranges: list[dict[str, int]] = Field(default_factory=list)
    overlap_ranges: list[dict[str, int]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)


class IdeasHubMediaScopeReadiness(BaseModel):
    model_config = ConfigDict(extra="allow")
    required: bool
    status: str
    media_project_id: uuid.UUID | None = None
    media_session_ids: list[uuid.UUID] = Field(default_factory=list)


class IdeasHubProductionReadiness(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    profile: str
    capabilities: IdeasHubProductionCapabilities
    editorial_status: str
    visual: IdeasHubVisualReadiness
    media_scope: IdeasHubMediaScopeReadiness
    issues: list[dict[str, Any]] = Field(default_factory=list)


class IdeasHubClient:
    """Typed Ideas Hub boundary; only safe GETs are retried."""

    def __init__(
        self,
        config: Settings = settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = config.ideas_hub_api_url
        self._timeout = config.ideas_hub_api_timeout
        self._max_retries = config.ideas_hub_http_max_retries
        self._backoff = config.ideas_hub_http_backoff_seconds
        self._transport = transport

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        last_error: IdeasHubTransientError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.get(path, **kwargs)
                if 200 <= response.status_code < 300:
                    return response
                if response.status_code == 404:
                    raise IdeasHubNotFoundError("Ideas Hub resource was not found")
                if response.status_code == 429 or response.status_code >= 500:
                    raise IdeasHubTransientError(
                        f"Ideas Hub temporarily returned HTTP {response.status_code}"
                    )
                raise IdeasHubClientError(
                    f"Ideas Hub rejected the read request with HTTP {response.status_code}"
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = IdeasHubTransientError("Ideas Hub network request failed")
                last_error.__cause__ = exc
            except IdeasHubTransientError as exc:
                last_error = exc
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff * (2**attempt))
        assert last_error is not None
        raise last_error

    async def _mutate(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Execute one mutation attempt; immutable revision creation is never retried."""

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, json=json)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise IdeasHubTransientError("Ideas Hub network request failed") from exc
        if response.status_code == 404:
            raise IdeasHubNotFoundError("Ideas Hub resource was not found")
        if response.status_code == 429 or response.status_code >= 500:
            raise IdeasHubTransientError(
                f"Ideas Hub temporarily returned HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            suffix = f": {detail}" if isinstance(detail, str) else ""
            raise IdeasHubClientError(
                f"Ideas Hub rejected the mutation with HTTP {response.status_code}{suffix}"
            )
        return response

    async def listar_roteiros(self) -> list[dict[str, Any]]:
        response = await self._get("/roteiros/")
        try:
            items = TypeAdapter(list[IdeasHubScript]).validate_python(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub script list response is malformed") from exc
        return [item.model_dump(mode="json") for item in items]

    async def listar_roteiros_aprovados(self) -> list[dict[str, Any]]:
        response = await self._get("/roteiros/", params={"status": "aprovado"})
        try:
            items = TypeAdapter(list[IdeasHubScript]).validate_python(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub script list response is malformed") from exc
        return [item.model_dump(mode="json") for item in items]

    async def aprovar_roteiro(self, roteiro_id: uuid.UUID) -> dict[str, Any]:
        response = await self._mutate("PATCH", f"/roteiros/{roteiro_id}/aprovar")
        try:
            return IdeasHubScript.model_validate(response.json()).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub approval response is malformed") from exc

    async def vincular_midia(
        self, roteiro_id: uuid.UUID, media_context: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._mutate(
            "POST",
            f"/roteiros/{roteiro_id}/revisoes/vincular-midia",
            json={"media_context": media_context},
        )
        try:
            return IdeasHubScript.model_validate(response.json()).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub media binding response is malformed") from exc

    async def preparar_producao(self, roteiro_id: uuid.UUID) -> dict[str, Any]:
        """Create one visual-enrichment child through the canonical Ideas Hub action."""

        response = await self._mutate("POST", f"/roteiros/{roteiro_id}/preparar-producao")
        try:
            script = IdeasHubScript.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError(
                "Ideas Hub visual enrichment response is malformed"
            ) from exc
        if (
            script.id == roteiro_id
            or script.parent_roteiro_id != roteiro_id
            or script.revision_kind != "visual_enrichment"
        ):
            raise IdeasHubContractError(
                "Ideas Hub visual enrichment response has invalid revision lineage"
            )
        return script.model_dump(mode="json")

    async def obter_roteiro(self, roteiro_id: uuid.UUID) -> dict[str, Any]:
        response = await self._get(f"/roteiros/{roteiro_id}")
        try:
            return IdeasHubScript.model_validate(response.json()).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub script response is malformed") from exc

    async def obter_ideia(self, ideia_id: uuid.UUID) -> dict[str, Any]:
        response = await self._get(f"/ideias/{ideia_id}")
        try:
            return IdeasHubIdea.model_validate(response.json()).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub idea response is malformed") from exc

    async def obter_visual_readiness(self, roteiro_id: uuid.UUID) -> dict[str, Any]:
        response = await self._get(f"/roteiros/{roteiro_id}/visual-readiness")
        try:
            readiness = IdeasHubVisualReadiness.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub visual readiness response is malformed") from exc
        return readiness.model_dump(mode="json")

    async def obter_production_readiness(self, roteiro_id: uuid.UUID) -> dict[str, Any]:
        response = await self._get(f"/roteiros/{roteiro_id}/production-readiness")
        try:
            readiness = IdeasHubProductionReadiness.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError(
                "Ideas Hub production readiness response is malformed"
            ) from exc
        return readiness.model_dump(mode="json")


ideas_hub_client = IdeasHubClient()
