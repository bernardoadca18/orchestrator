"""
Client HTTP para o voice-workshop (síntese + conversão de voz).

Ver API_REFERENCE.md do voice-workshop: POST /narrar retorna job_id
imediatamente; status é consultado via polling em GET /jobs/{job_id};
áudio é baixado uma única vez via GET /jobs/{job_id}/audio (o arquivo é
removido do lado do voice-workshop após o download).
"""
import asyncio

import httpx

from app.core.config import settings


class VoiceClientError(RuntimeError):
    """Erro permanente - não deve ser retentado."""
    pass


class VoiceClientTransientError(RuntimeError):
    """Erro transitório - pode ser retentado com backoff."""
    pass


class VoiceClientTimeoutError(VoiceClientTransientError):
    pass


class VoiceClient:
    def __init__(self):
        self._base_url = settings.voice_api_url
        self._max_retries = 2
        self._backoff_base_seconds = 2

    async def _retry_com_backoff(self, coro_func, *args, **kwargs):
        """Executa coro_func com retry exponencial para erros transitórios."""
        ultima_excecao = None
        for tentativa in range(self._max_retries + 1):
            try:
                return await coro_func(*args, **kwargs)
            except VoiceClientTransientError as exc:
                ultima_excecao = exc
                if tentativa < self._max_retries:
                    backoff = self._backoff_base_seconds * (2 ** tentativa)
                    await asyncio.sleep(backoff)
                    continue
                raise
            except VoiceClientError:
                # Erro permanente - não retenta
                raise
        raise ultima_excecao

    async def _iniciar_narracao_interno(self, texto: str, run_name: str | None, qualidade: str | None) -> str:
        payload = {
            "texto": texto,
            "run_name": run_name or settings.voice_run_name,
            "qualidade": qualidade or settings.voice_qualidade_padrao,
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
                resp = await client.post("/narrar", json=payload)
                if resp.status_code == 400:
                    raise VoiceClientError(f"Requisição rejeitada pelo voice-workshop: {resp.json().get('detail')}")
                if resp.status_code >= 500:
                    raise VoiceClientTransientError(f"Erro interno no voice-workshop: {resp.status_code}")
                resp.raise_for_status()
                return resp.json()["job_id"]
        except httpx.TimeoutException as exc:
            raise VoiceClientTransientError(f"Timeout ao conectar com voice-workshop: {exc}") from exc
        except httpx.NetworkError as exc:
            raise VoiceClientTransientError(f"Erro de rede ao conectar com voice-workshop: {exc}") from exc

    async def iniciar_narracao(self, texto: str, run_name: str | None = None, qualidade: str | None = None) -> str:
        return await self._retry_com_backoff(self._iniciar_narracao_interno, texto, run_name, qualidade)

    async def _aguardar_conclusao_interno(self, job_id: str) -> dict:
        """Faz polling até status=concluido ou status=erro, respeitando o timeout configurado."""
        elapsed = 0
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
                while elapsed < settings.voice_job_timeout_seconds:
                    resp = await client.get(f"/jobs/{job_id}")
                    if resp.status_code >= 500:
                        raise VoiceClientTransientError(f"Erro interno no voice-workshop ao consultar job: {resp.status_code}")
                    if resp.status_code == 404:
                        raise VoiceClientError(f"Job {job_id} não encontrado no voice-workshop")
                    resp.raise_for_status()
                    dados = resp.json()
                    if dados["status"] == "concluido":
                        return dados
                    if dados["status"] == "erro":
                        raise VoiceClientError(dados.get("erro") or "Job de narração falhou sem detalhe")
                    await asyncio.sleep(settings.voice_poll_interval_seconds)
                    elapsed += settings.voice_poll_interval_seconds
        except httpx.TimeoutException as exc:
            raise VoiceClientTransientError(f"Timeout ao consultar job no voice-workshop: {exc}") from exc
        except httpx.NetworkError as exc:
            raise VoiceClientTransientError(f"Erro de rede ao consultar job no voice-workshop: {exc}") from exc
        raise VoiceClientTimeoutError(f"Job {job_id} excedeu timeout de {settings.voice_job_timeout_seconds}s")

    async def aguardar_conclusao(self, job_id: str) -> dict:
        return await self._retry_com_backoff(self._aguardar_conclusao_interno, job_id)

    async def _baixar_audio_interno(self, job_id: str) -> bytes:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=60) as client:
                resp = await client.get(f"/jobs/{job_id}/audio")
                if resp.status_code >= 500:
                    raise VoiceClientTransientError(f"Erro interno no voice-workshop ao baixar áudio: {resp.status_code}")
                if resp.status_code == 404:
                    raise VoiceClientError(f"Áudio do job {job_id} não encontrado")
                resp.raise_for_status()
                return resp.content
        except httpx.TimeoutException as exc:
            raise VoiceClientTransientError(f"Timeout ao baixar áudio do voice-workshop: {exc}") from exc
        except httpx.NetworkError as exc:
            raise VoiceClientTransientError(f"Erro de rede ao baixar áudio do voice-workshop: {exc}") from exc

    async def baixar_audio(self, job_id: str) -> bytes:
        return await self._retry_com_backoff(self._baixar_audio_interno, job_id)

    async def consultar_job(self, job_id: str) -> dict:
        """Consulta o status de um job no voice-workshop sem baixar o áudio."""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=10) as client:
                resp = await client.get(f"/jobs/{job_id}")
                if resp.status_code == 404:
                    return {"status": "nao_encontrado"}
                if resp.status_code >= 500:
                    raise VoiceClientTransientError(f"Erro interno no voice-workshop ao consultar job: {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException as exc:
            raise VoiceClientTransientError(f"Timeout ao consultar job no voice-workshop: {exc}") from exc
        except httpx.NetworkError as exc:
            raise VoiceClientTransientError(f"Erro de rede ao consultar job no voice-workshop: {exc}") from exc


voice_client = VoiceClient()
