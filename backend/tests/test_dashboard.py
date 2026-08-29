from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routers.dashboard import CELERY_PRODUCTION_QUEUE, _queue_depth, dashboard_stats


@pytest.mark.asyncio
async def test_queue_depth_uses_real_kombu_queue_name() -> None:
    redis_client = MagicMock()
    redis_client.llen = AsyncMock(return_value=7)
    redis_client.aclose = AsyncMock()
    with patch("app.api.routers.dashboard.redis.from_url", return_value=redis_client):
        size, error = await _queue_depth()
    assert size == 7
    assert error is None
    redis_client.llen.assert_awaited_once_with(CELERY_PRODUCTION_QUEUE)


@pytest.mark.asyncio
async def test_unavailable_redis_is_not_reported_as_empty_queue() -> None:
    redis_client = MagicMock()
    redis_client.llen = AsyncMock(side_effect=ConnectionError("offline"))
    redis_client.aclose = AsyncMock()
    with patch("app.api.routers.dashboard.redis.from_url", return_value=redis_client):
        size, error = await _queue_depth()
    assert size is None
    assert error == "ConnectionError"


@pytest.mark.asyncio
async def test_invalid_redis_configuration_is_also_unavailable() -> None:
    with patch(
        "app.api.routers.dashboard.redis.from_url",
        side_effect=ValueError("invalid URL"),
    ):
        size, error = await _queue_depth()
    assert size is None
    assert error == "ValueError"


@pytest.mark.asyncio
async def test_dashboard_labels_audio_duration_and_real_latency_separately() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(
        side_effect=[2, 10, 2, 12.5, 4.2, 9.8, 3, 4, 1.4, 120.0, 0.8, 5, 4]
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    with (
        patch(
            "app.api.routers.dashboard._queue_depth",
            AsyncMock(return_value=(None, "TimeoutError")),
        ),
        patch("app.api.routers.dashboard.AsyncSessionLocal", return_value=context),
    ):
        result = await dashboard_stats()

    assert result.fila_status == "unavailable"
    assert result.fila_tamanho is None
    assert result.duracao_media_audio_bloco_segundos == 12.5
    assert result.generation_latency_p50_seconds == 4.2
    assert result.generation_latency_p95_seconds == 9.8
    assert result.taxa_erro_24h_percent == pytest.approx(16.67)
    assert result.qa_success_rate_24h_percent == 80.0
