"""Persistence boundary for Stage 15 Production Timelines."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import ProductionTimeline, TimelineGap, TimelinePlacement


class ProductionTimelineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _eager(self):
        return (
            selectinload(ProductionTimeline.placements),
            selectinload(ProductionTimeline.gaps),
        )

    async def get_by_request_fingerprint(self, fingerprint: str) -> ProductionTimeline | None:
        statement = (
            select(ProductionTimeline)
            .where(ProductionTimeline.request_fingerprint == fingerprint)
            .options(*self._eager())
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_id(self, timeline_id: UUID) -> ProductionTimeline | None:
        statement = (
            select(ProductionTimeline)
            .where(ProductionTimeline.id == timeline_id)
            .options(*self._eager())
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: UUID) -> Sequence[ProductionTimeline]:
        statement = (
            select(ProductionTimeline)
            .where(ProductionTimeline.workflow_execucao_id == workflow_id)
            .options(*self._eager())
            .order_by(ProductionTimeline.criado_em.desc())
        )
        return (await self.session.execute(statement)).scalars().all()

    async def create_or_get(self, **values) -> tuple[ProductionTimeline, bool]:
        existing = await self.get_by_request_fingerprint(values["request_fingerprint"])
        if existing is not None:
            return existing, False
        timeline = ProductionTimeline(**values)
        self.session.add(timeline)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_request_fingerprint(values["request_fingerprint"])
            if existing is None:
                raise
            return existing, False
        return await self.reload(timeline), True

    async def clear_outputs(self, timeline: ProductionTimeline) -> None:
        await self.session.execute(
            delete(TimelinePlacement).where(TimelinePlacement.production_timeline_id == timeline.id)
        )
        await self.session.execute(
            delete(TimelineGap).where(TimelineGap.production_timeline_id == timeline.id)
        )
        timeline.visual_coverage_seconds = 0.0
        timeline.visual_gap_seconds = 0.0
        timeline.visual_coverage_ratio = 0.0
        timeline.placement_count = 0
        timeline.gap_count = 0
        timeline.cut_count = 0
        timeline.unplaced_query_count = 0
        timeline.required_gap_seconds = 0.0
        timeline.preferred_gap_seconds = 0.0
        timeline.optional_gap_seconds = 0.0
        timeline.background_gap_seconds = 0.0
        timeline.average_shot_duration_seconds = None
        timeline.metrics = None
        timeline.diagnostics = None
        timeline.error_message = None
        await self.session.commit()
        self.session.expire(timeline, ["placements", "gaps"])

    async def mark_processing(self, timeline: ProductionTimeline) -> None:
        timeline.status = "processing"
        timeline.error_message = None
        timeline.started_at = dt.datetime.now(dt.UTC)
        timeline.completed_at = None
        await self.session.commit()

    async def mark_blocked(self, timeline: ProductionTimeline, reason: str) -> None:
        timeline.status = "blocked"
        timeline.error_message = reason
        timeline.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def mark_failed(self, timeline: ProductionTimeline, reason: str) -> None:
        timeline.status = "failed"
        timeline.error_message = reason
        timeline.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def replace_outputs(
        self,
        timeline: ProductionTimeline,
        *,
        placements: Sequence[TimelinePlacement],
        gaps: Sequence[TimelineGap],
        status: str,
        fingerprint: str,
        coverage_seconds: float,
        gap_seconds: float,
        coverage_ratio: float,
        required_gap_seconds: float,
        preferred_gap_seconds: float,
        optional_gap_seconds: float,
        background_gap_seconds: float,
        cut_count: int,
        unplaced_query_count: int,
        average_shot_duration_seconds: float | None,
        metrics: dict,
        diagnostics: dict,
    ) -> None:
        await self.session.execute(
            delete(TimelinePlacement).where(TimelinePlacement.production_timeline_id == timeline.id)
        )
        await self.session.execute(
            delete(TimelineGap).where(TimelineGap.production_timeline_id == timeline.id)
        )
        self.session.add_all([*placements, *gaps])
        timeline.status = status
        timeline.fingerprint = fingerprint
        timeline.visual_coverage_seconds = coverage_seconds
        timeline.visual_gap_seconds = gap_seconds
        timeline.visual_coverage_ratio = coverage_ratio
        timeline.placement_count = len(placements)
        timeline.gap_count = len(gaps)
        timeline.cut_count = cut_count
        timeline.unplaced_query_count = unplaced_query_count
        timeline.required_gap_seconds = required_gap_seconds
        timeline.preferred_gap_seconds = preferred_gap_seconds
        timeline.optional_gap_seconds = optional_gap_seconds
        timeline.background_gap_seconds = background_gap_seconds
        timeline.average_shot_duration_seconds = average_shot_duration_seconds
        timeline.metrics = metrics
        timeline.diagnostics = diagnostics
        timeline.error_message = None
        timeline.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()
        self.session.expire(timeline, ["placements", "gaps"])

    async def reload(self, timeline: ProductionTimeline) -> ProductionTimeline:
        loaded = await self.get_by_id(timeline.id)
        if loaded is None:
            raise LookupError("production timeline disappeared during persistence")
        return loaded
