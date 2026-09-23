import time
from typing import Literal

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.clients.video_composer_client import VideoComposerClient
from app.core.config import settings
from app.core.database import engine

router = APIRouter()


class DependenciaStatus(BaseModel):
    status: Literal["healthy", "unavailable"]
    latency_ms: float | None = None
    error: str | None = None


class DependenciasHealth(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: dict[str, DependenciaStatus]


@router.get("/health")
@router.get("/health/live")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _check_dependencies() -> DependenciasHealth:
    """Check the durable stores and runtime services required by production."""
    deps: dict[str, DependenciaStatus] = {}
    all_healthy = True

    # Postgres
    try:
        from sqlalchemy import text

        start = time.monotonic()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency = (time.monotonic() - start) * 1000
        deps["postgres"] = DependenciaStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["postgres"] = DependenciaStatus(status="unavailable", error=type(exc).__name__)

    # Redis
    redis_client: redis.Redis | None = None
    try:
        start = time.monotonic()
        redis_client = redis.from_url(settings.redis_url)
        assert redis_client is not None
        await redis_client.ping()
        latency = (time.monotonic() - start) * 1000
        deps["redis"] = DependenciaStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["redis"] = DependenciaStatus(status="unavailable", error=type(exc).__name__)
    finally:
        if redis_client is not None:
            await redis_client.aclose()

    # Ideas Hub
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(base_url=settings.ideas_hub_api_url, timeout=5.0) as client:
            resp = await client.get("/health")
            resp.raise_for_status()
        latency = (time.monotonic() - start) * 1000
        deps["ideas_hub"] = DependenciaStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["ideas_hub"] = DependenciaStatus(status="unavailable", error=type(exc).__name__)

    # Media Indexer
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(
            base_url=settings.media_indexer_api_url,
            timeout=5.0,
        ) as client:
            resp = await client.get("/health/ready")
            resp.raise_for_status()
        latency = (time.monotonic() - start) * 1000
        deps["media_indexer"] = DependenciaStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["media_indexer"] = DependenciaStatus(status="unavailable", error=type(exc).__name__)

    # Canonical Qwen Voice Service readiness
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(base_url=settings.voice_api_url, timeout=5.0) as client:
            resp = await client.get("/health/ready")
            resp.raise_for_status()
        latency = (time.monotonic() - start) * 1000
        deps["voice_service"] = DependenciaStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["voice_service"] = DependenciaStatus(status="unavailable", error=type(exc).__name__)

    # Frozen Stage 16 Video Composer readiness
    try:
        start = time.monotonic()
        async with VideoComposerClient() as composer:
            if not await composer.health_ready():
                raise RuntimeError("not_ready")
        latency = (time.monotonic() - start) * 1000
        deps["video_composer"] = DependenciaStatus(status="healthy", latency_ms=round(latency, 2))
    except Exception as exc:
        all_healthy = False
        deps["video_composer"] = DependenciaStatus(status="unavailable", error=type(exc).__name__)

    return DependenciasHealth(
        status="ready" if all_healthy else "degraded",
        dependencies=deps,
    )


@router.get("/health/dependencias", response_model=DependenciasHealth)
async def health_dependencias() -> DependenciasHealth:
    return await _check_dependencies()


@router.get("/health/ready", response_model=DependenciasHealth)
async def health_ready(response: Response) -> DependenciasHealth:
    result = await _check_dependencies()
    if result.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
