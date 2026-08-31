"""Operational dashboard metrics backed only by durable, trustworthy data."""

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import redis.asyncio as redis
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import and_, extract, func, or_, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.workflow import (
    AudioBloco,
    AudioBlocoTake,
    ProductionQAReport,
    WorkflowExecucao,
    WorkflowRender,
)

router = APIRouter()
CELERY_PRODUCTION_QUEUE = "audio_producao"


class DashboardStats(BaseModel):
    fila_nome: str
    fila_status: Literal["available", "unavailable"]
    fila_tamanho: int | None
    fila_error: str | None = None
    workflows_ativos: int
    blocos_concluidos_24h: int
    blocos_erro_24h: int
    duracao_media_audio_bloco_segundos: float | None
    taxa_erro_24h_percent: float
    generation_latency_p50_seconds: float | None
    generation_latency_p95_seconds: float | None
    failed_takes_24h: int
    quality_retries_24h: int
    average_attempts_per_completed_block_24h: float | None
    workflow_completion_time_mean_seconds_24h: float | None
    render_factor_mean_24h: float | None
    qa_success_rate_24h_percent: float | None


async def _queue_depth() -> tuple[int | None, str | None]:
    client: redis.Redis | None = None
    try:
        client = redis.from_url(settings.redis_url)
        # Kombu's Redis transport materializes the Celery queue as this list.
        depth = await cast(Awaitable[int], client.llen(CELERY_PRODUCTION_QUEUE))
        return int(depth), None
    except Exception as exc:
        return None, type(exc).__name__
    finally:
        if client is not None:
            await client.aclose()


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats() -> DashboardStats:
    queue_size, queue_error = await _queue_depth()
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        workflows_ativos = (
            await session.scalar(
                select(func.count())
                .select_from(WorkflowExecucao)
                .where(
                    or_(
                        WorkflowExecucao.production_status.in_(["queued", "running"]),
                        and_(
                            WorkflowExecucao.production_status.is_(None),
                            WorkflowExecucao.status.in_(["agendada", "gerando_audio"]),
                        ),
                    )
                )
            )
            or 0
        )
        completed_blocks = (
            await session.scalar(
                select(func.count())
                .select_from(AudioBloco)
                .where(AudioBloco.status == "concluido", AudioBloco.atualizado_em >= since)
            )
            or 0
        )
        failed_blocks = (
            await session.scalar(
                select(func.count())
                .select_from(AudioBloco)
                .where(AudioBloco.status == "erro", AudioBloco.atualizado_em >= since)
            )
            or 0
        )
        average_audio_duration = await session.scalar(
            select(func.avg(AudioBloco.duracao_segundos)).where(
                AudioBloco.status == "concluido",
                AudioBloco.atualizado_em >= since,
                AudioBloco.duracao_segundos.isnot(None),
            )
        )

        latency = extract("epoch", AudioBlocoTake.completed_at - AudioBlocoTake.created_at)
        latency_filter = and_(
            AudioBlocoTake.status == "completed",
            AudioBlocoTake.completed_at.isnot(None),
            AudioBlocoTake.completed_at >= since,
        )
        latency_p50 = await session.scalar(
            select(func.percentile_cont(0.5).within_group(latency)).where(latency_filter)
        )
        latency_p95 = await session.scalar(
            select(func.percentile_cont(0.95).within_group(latency)).where(latency_filter)
        )
        failed_takes = (
            await session.scalar(
                select(func.count())
                .select_from(AudioBlocoTake)
                .where(AudioBlocoTake.status == "failed", AudioBlocoTake.completed_at >= since)
            )
            or 0
        )
        quality_retries = (
            await session.scalar(
                select(func.count())
                .select_from(AudioBlocoTake)
                .where(AudioBlocoTake.kind == "quality_retry", AudioBlocoTake.created_at >= since)
            )
            or 0
        )
        average_attempts = await session.scalar(
            select(func.avg(AudioBloco.generation_attempt + 1)).where(
                AudioBloco.status == "concluido", AudioBloco.atualizado_em >= since
            )
        )
        workflow_completion = await session.scalar(
            select(
                func.avg(
                    extract(
                        "epoch",
                        WorkflowExecucao.production_completed_at
                        - WorkflowExecucao.production_started_at,
                    )
                )
            ).where(
                WorkflowExecucao.production_status == "completed",
                WorkflowExecucao.production_completed_at >= since,
                WorkflowExecucao.production_started_at.isnot(None),
            )
        )
        render_factor = await session.scalar(
            select(
                func.avg(
                    extract("epoch", WorkflowRender.completed_at - WorkflowRender.started_at)
                    / WorkflowRender.output_duration_seconds
                )
            ).where(
                WorkflowRender.status == "completed",
                WorkflowRender.completed_at >= since,
                WorkflowRender.started_at.isnot(None),
                WorkflowRender.output_duration_seconds > 0,
            )
        )
        qa_total = (
            await session.scalar(
                select(func.count())
                .select_from(ProductionQAReport)
                .where(
                    ProductionQAReport.completed_at >= since,
                    ProductionQAReport.status.in_(
                        ["passed", "passed_with_warnings", "failed", "blocked"]
                    ),
                )
            )
            or 0
        )
        qa_success = (
            await session.scalar(
                select(func.count())
                .select_from(ProductionQAReport)
                .where(
                    ProductionQAReport.completed_at >= since,
                    ProductionQAReport.status.in_(["passed", "passed_with_warnings"]),
                )
            )
            or 0
        )

    total_blocks = completed_blocks + failed_blocks
    return DashboardStats(
        fila_nome=CELERY_PRODUCTION_QUEUE,
        fila_status="available" if queue_size is not None else "unavailable",
        fila_tamanho=queue_size,
        fila_error=queue_error,
        workflows_ativos=workflows_ativos,
        blocos_concluidos_24h=completed_blocks,
        blocos_erro_24h=failed_blocks,
        duracao_media_audio_bloco_segundos=(
            round(float(average_audio_duration), 2) if average_audio_duration is not None else None
        ),
        taxa_erro_24h_percent=(
            round(failed_blocks / total_blocks * 100, 2) if total_blocks else 0.0
        ),
        generation_latency_p50_seconds=(
            round(float(latency_p50), 2) if latency_p50 is not None else None
        ),
        generation_latency_p95_seconds=(
            round(float(latency_p95), 2) if latency_p95 is not None else None
        ),
        failed_takes_24h=failed_takes,
        quality_retries_24h=quality_retries,
        average_attempts_per_completed_block_24h=(
            round(float(average_attempts), 2) if average_attempts is not None else None
        ),
        workflow_completion_time_mean_seconds_24h=(
            round(float(workflow_completion), 2) if workflow_completion is not None else None
        ),
        render_factor_mean_24h=(
            round(float(render_factor), 3) if render_factor is not None else None
        ),
        qa_success_rate_24h_percent=(round(qa_success / qa_total * 100, 2) if qa_total else None),
    )
