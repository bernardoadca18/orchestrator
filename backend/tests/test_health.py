from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

from app.api.routers.health import (
    DependenciasHealth,
    DependenciaStatus,
    _check_dependencies,
    health_ready,
)


@pytest.mark.asyncio
async def test_degraded_readiness_returns_503() -> None:
    result = DependenciasHealth(
        status="degraded",
        dependencies={
            "redis": DependenciaStatus(status="unavailable", error="ConnectionError")
        },
    )
    response = Response()
    with patch("app.api.routers.health._check_dependencies", AsyncMock(return_value=result)):
        returned = await health_ready(response)

    assert returned.status == "degraded"
    assert response.status_code == 503
    assert returned.dependencies["redis"].latency_ms is None


@pytest.mark.asyncio
@pytest.mark.parametrize("focus", ["full", "pov", "gamedev"])
async def test_failed_redis_probe_is_unavailable_and_client_is_closed(focus) -> None:
    postgres_connection = MagicMock()
    postgres_connection.execute = AsyncMock()
    postgres_context = MagicMock()
    postgres_context.__aenter__ = AsyncMock(return_value=postgres_connection)
    postgres_context.__aexit__ = AsyncMock(return_value=None)
    postgres_engine = MagicMock()
    postgres_engine.connect.return_value = postgres_context
    redis_client = MagicMock()
    redis_client.ping = AsyncMock(side_effect=ConnectionError("offline"))
    redis_client.aclose = AsyncMock()

    healthy_response = MagicMock()
    healthy_response.raise_for_status = MagicMock()
    http_client = MagicMock()
    http_client.__aenter__ = AsyncMock(return_value=http_client)
    http_client.__aexit__ = AsyncMock(return_value=None)
    http_client.get = AsyncMock(return_value=healthy_response)

    composer = MagicMock()
    composer.__aenter__ = AsyncMock(return_value=composer)
    composer.__aexit__ = AsyncMock(return_value=None)
    composer.health_ready = AsyncMock(return_value=True)

    with (
        patch("app.api.routers.health.engine", postgres_engine),
        patch("app.api.routers.health.redis.from_url", return_value=redis_client),
        patch("app.api.routers.health.httpx.AsyncClient", return_value=http_client),
        patch("app.api.routers.health.VideoComposerClient", return_value=composer),
    ):
        result = await _check_dependencies(focus)

    assert result.status == "degraded"
    assert result.dependencies["redis"].status == "unavailable"
    assert result.dependencies["redis"].error == "ConnectionError"
    redis_client.aclose.assert_awaited_once()
    assert result.dependencies["postgres"].status == "healthy"
    assert result.dependencies["ideas_hub"].status == "healthy"
    if focus != "gamedev":
        assert result.dependencies["media_indexer"].status == "healthy"
    else:
        assert "media_indexer" not in result.dependencies
    assert result.dependencies["voice_service"].status == "healthy"
    if focus != "gamedev":
        assert result.dependencies["video_composer"].status == "healthy"
    else:
        assert "video_composer" not in result.dependencies
        composer.health_ready.assert_not_awaited()
