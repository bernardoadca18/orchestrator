"""Endpoints de dashboard: métricas de fila, performance e saúde do sistema."""

from datetime import UTC, datetime, timedelta

import redis.asyncio as redis
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.workflow import AudioBloco, WorkflowExecucao

router = APIRouter()


class DashboardStats(BaseModel):
    """Métricas de saúde do sistema de produção de áudio."""

    fila_tamanho: int
    workflows_ativos: int
    blocos_concluidos_24h: int
    blocos_erro_24h: int
    tempo_medio_bloco_segundos: float | None
    taxa_erro_24h_percent: float


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats():
    """Retorna métricas de fila, performance e taxa de erro."""
    # Tamanho da fila Celery
    r = redis.from_url(settings.redis_url)
    try:
        fila_tamanho = await r.llen("celery:queue:audio_producao")
    except Exception:
        fila_tamanho = 0
    finally:
        await r.close()

    async with AsyncSessionLocal() as session:
        agora = datetime.now(UTC)
        ultimas_24h = agora - timedelta(hours=24)

        # Workflows ativos (agendada ou gerando_audio)
        stmt_ativos = (
            select(func.count())
            .select_from(WorkflowExecucao)
            .where(WorkflowExecucao.status.in_(["agendada", "gerando_audio"]))
        )
        workflows_ativos = (await session.execute(stmt_ativos)).scalar() or 0

        # Blocos concluídos nas últimas 24h
        stmt_concluidos = (
            select(func.count())
            .select_from(AudioBloco)
            .where(
                AudioBloco.status == "concluido",
                AudioBloco.atualizado_em >= ultimas_24h,
            )
        )
        blocos_concluidos_24h = (await session.execute(stmt_concluidos)).scalar() or 0

        # Blocos com erro nas últimas 24h
        stmt_erro = (
            select(func.count())
            .select_from(AudioBloco)
            .where(
                AudioBloco.status == "erro",
                AudioBloco.atualizado_em >= ultimas_24h,
            )
        )
        blocos_erro_24h = (await session.execute(stmt_erro)).scalar() or 0

        # Tempo médio de geração por bloco (últimas 24h, apenas concluídos com duração)
        stmt_tempo_medio = (
            select(func.avg(AudioBloco.duracao_segundos))
            .select_from(AudioBloco)
            .where(
                AudioBloco.status == "concluido",
                AudioBloco.atualizado_em >= ultimas_24h,
                AudioBloco.duracao_segundos.isnot(None),
            )
        )
        tempo_medio = (await session.execute(stmt_tempo_medio)).scalar()

        # Taxa de erro (percentual de blocos com erro vs total processado)
        total_processados = blocos_concluidos_24h + blocos_erro_24h
        taxa_erro = (blocos_erro_24h / total_processados * 100) if total_processados > 0 else 0.0

    return DashboardStats(
        fila_tamanho=fila_tamanho,
        workflows_ativos=workflows_ativos,
        blocos_concluidos_24h=blocos_concluidos_24h,
        blocos_erro_24h=blocos_erro_24h,
        tempo_medio_bloco_segundos=round(tempo_medio, 2) if tempo_medio else None,
        taxa_erro_24h_percent=round(taxa_erro, 2),
    )
