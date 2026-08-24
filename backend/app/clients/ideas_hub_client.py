"""Client HTTP para o serviço ideas-hub (somente leitura, sem acoplamento de DB)."""

import uuid

import httpx

from app.core.config import settings


class IdeasHubClient:
    def __init__(self):
        self._base_url = settings.ideas_hub_api_url
        self._timeout = settings.ideas_hub_api_timeout

    async def listar_roteiros_aprovados(self) -> list[dict]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get("/roteiros/", params={"status": "aprovado"})
            resp.raise_for_status()
            return resp.json()

    async def obter_roteiro(self, roteiro_id: uuid.UUID) -> dict:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get(f"/roteiros/{roteiro_id}")
            resp.raise_for_status()
            return resp.json()

    async def obter_ideia(self, ideia_id: uuid.UUID) -> dict:
        """Busca detalhes da ideia no ideas-hub, incluindo perfil_producao."""
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get(f"/ideias/{ideia_id}")
            resp.raise_for_status()
            return resp.json()


ideas_hub_client = IdeasHubClient()
