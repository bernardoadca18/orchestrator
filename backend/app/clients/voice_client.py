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
    pass


class VoiceClientTimeoutError(VoiceClientError):
    pass


class VoiceClient:
    def __init__(self):
        self._base_url = settings.voice_api_url

    async def iniciar_narracao(self, texto: str, run_name: str | None = None, qualidade: str | None = None) -> str:
        payload = {
            "texto": texto,
            "run_name": run_name or settings.voice_run_name,
            "qualidade": qualidade or settings.voice_qualidade_padrao,
        }
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
            resp = await client.post("/narrar", json=payload)
            if resp.status_code == 400:
                raise VoiceClientError(f"Requisição rejeitada pelo voice-workshop: {resp.json().get('detail')}")
            resp.raise_for_status()
            return resp.json()["job_id"]

    async def aguardar_conclusao(self, job_id: str) -> dict:
        """Faz polling até status=concluido ou status=erro, respeitando o timeout configurado."""
        elapsed = 0
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
            while elapsed < settings.voice_job_timeout_seconds:
                resp = await client.get(f"/jobs/{job_id}")
                resp.raise_for_status()
                dados = resp.json()
                if dados["status"] == "concluido":
                    return dados
                if dados["status"] == "erro":
                    raise VoiceClientError(dados.get("erro") or "Job de narração falhou sem detalhe")
                await asyncio.sleep(settings.voice_poll_interval_seconds)
                elapsed += settings.voice_poll_interval_seconds
        raise VoiceClientTimeoutError(f"Job {job_id} excedeu timeout de {settings.voice_job_timeout_seconds}s")

    async def baixar_audio(self, job_id: str) -> bytes:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=60) as client:
            resp = await client.get(f"/jobs/{job_id}/audio")
            resp.raise_for_status()
            return resp.content


voice_client = VoiceClient()
