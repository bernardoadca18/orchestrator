"""Typed read-only HTTP boundary for Ideas Hub production-script snapshots."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

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


class IdeasHubIdea(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: uuid.UUID
    perfil_producao: str | None = None


class IdeasHubClient:
    """GETs may retry transiently; no mutating request is exposed by this client."""

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

    async def listar_roteiros_aprovados(self) -> list[dict[str, Any]]:
        response = await self._get("/roteiros/", params={"status": "aprovado"})
        try:
            items = TypeAdapter(list[IdeasHubScript]).validate_python(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise IdeasHubContractError("Ideas Hub script list response is malformed") from exc
        return [item.model_dump(mode="json") for item in items]

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


ideas_hub_client = IdeasHubClient()
