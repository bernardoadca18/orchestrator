import time

import httpx
import redis.asyncio as redis
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import engine

router = APIRouter()


class DependenciaStatus(BaseModel):
    status: str
    latency_ms: float | None = None
    error: str | None = None


class DependenciasHealth(BaseModel):
    status: str
    dependencies: dict[str, DependenciaStatus]


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/health/dependencias", response_model=DependenciasHealth)
async def health_dependencias():
    """Healthcheck profundo checando ideas-hub, voice-workshop, Redis e Postgres."""
    deps: dict[str, DependenciaStatus] = {}
    all_healthy = True

    # Postgres
    try:
        from sqlalchemy import text

        start = time.monotonic()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency = (time.monotonic() - start) * 1000
        deps["postgres"] = DependenciaStatus(status="ok", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["postgres"] = DependenciaStatus(status="error", error=str(exc))

    # Redis
    try:
        start = time.monotonic()
        r = redis.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        latency = (time.monotonic() - start) * 1000
        deps["redis"] = DependenciaStatus(status="ok", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["redis"] = DependenciaStatus(status="error", error=str(exc))

    # Ideas Hub
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(base_url=settings.ideas_hub_api_url, timeout=5.0) as client:
            resp = await client.get("/health")
            resp.raise_for_status()
        latency = (time.monotonic() - start) * 1000
        deps["ideas_hub"] = DependenciaStatus(status="ok", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["ideas_hub"] = DependenciaStatus(status="error", error=str(exc))

    # Voice Workshop
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(base_url=settings.voice_api_url, timeout=5.0) as client:
            resp = await client.get("/health")
            resp.raise_for_status()
        latency = (time.monotonic() - start) * 1000
        deps["voice_workshop"] = DependenciaStatus(status="ok", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["voice_workshop"] = DependenciaStatus(status="error", error=str(exc))

    return DependenciasHealth(
        status="ok" if all_healthy else "degraded",
        dependencies=deps,
    )
