"""Persistence boundary for Stage 14 Visual Plans."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import VisualDecision, VisualPlan


class VisualPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _eager(self):
        return selectinload(VisualPlan.decisions)

    async def get_by_request_fingerprint(self, fingerprint: str) -> VisualPlan | None:
        statement = (
            select(VisualPlan)
            .where(VisualPlan.request_fingerprint == fingerprint)
            .options(self._eager())
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_id(self, plan_id: UUID) -> VisualPlan | None:
        statement = select(VisualPlan).where(VisualPlan.id == plan_id).options(self._eager())
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: UUID) -> Sequence[VisualPlan]:
        statement = (
            select(VisualPlan)
            .where(VisualPlan.workflow_execucao_id == workflow_id)
            .options(self._eager())
            .order_by(VisualPlan.criado_em.desc())
        )
        return (await self.session.execute(statement)).scalars().all()

    async def create_or_get(self, **values) -> tuple[VisualPlan, bool]:
        existing = await self.get_by_request_fingerprint(values["request_fingerprint"])
        if existing is not None:
            return existing, False
        plan = VisualPlan(**values)
        self.session.add(plan)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_request_fingerprint(values["request_fingerprint"])
            if existing is None:
                raise
            return existing, False
        return await self.reload(plan), True

    async def mark_processing(self, plan: VisualPlan) -> None:
        plan.status = "processing"
        plan.error_message = None
        plan.started_at = dt.datetime.now(dt.UTC)
        plan.completed_at = None
        await self.session.commit()

    async def clear_outputs(self, plan: VisualPlan) -> None:
        await self.session.execute(
            delete(VisualDecision).where(VisualDecision.visual_plan_id == plan.id)
        )
        plan.total_queries = 0
        plan.resolved_queries = 0
        plan.unresolved_queries = 0
        plan.failed_queries = 0
        plan.required_gaps = 0
        plan.optional_gaps = 0
        plan.direct_provenance_count = 0
        plan.media_search_count = 0
        plan.reused_source_count = 0
        plan.metrics = None
        plan.diagnostics = None
        plan.media_index_provenance = None
        await self.session.commit()
        self.session.expire(plan, ["decisions"])

    async def mark_blocked(
        self, plan: VisualPlan, reason: str, *, diagnostics: dict | None = None
    ) -> None:
        plan.status = "blocked"
        plan.error_message = reason
        if diagnostics is not None:
            plan.diagnostics = diagnostics
        plan.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def mark_failed(self, plan: VisualPlan, reason: str) -> None:
        plan.status = "failed"
        plan.error_message = reason
        plan.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def replace_outputs(
        self,
        plan: VisualPlan,
        *,
        decisions: Sequence[VisualDecision],
        status: str,
        fingerprint: str,
        media_index_provenance: dict,
        metrics: dict,
        diagnostics: dict,
    ) -> None:
        await self.session.execute(
            delete(VisualDecision).where(VisualDecision.visual_plan_id == plan.id)
        )
        for decision in decisions:
            self.session.add(decision)
        plan.fingerprint = fingerprint
        plan.media_index_provenance = media_index_provenance
        plan.status = status
        plan.total_queries = len(decisions)
        plan.resolved_queries = sum(
            item.source_resolution_status == "resolved" for item in decisions
        )
        plan.unresolved_queries = sum(
            item.source_resolution_status == "unresolved" for item in decisions
        )
        plan.failed_queries = sum(item.source_resolution_status == "failed" for item in decisions)
        plan.required_gaps = sum(
            item.importance == "required" and item.source_resolution_status != "resolved"
            for item in decisions
        )
        plan.optional_gaps = sum(
            item.importance != "required" and item.source_resolution_status != "resolved"
            for item in decisions
        )
        plan.direct_provenance_count = sum(
            item.selection_strategy == "preferred_provenance" for item in decisions
        )
        plan.media_search_count = int(metrics.get("media_search_calls", 0))
        plan.reused_source_count = int(metrics.get("reused_source_count", 0))
        plan.metrics = metrics
        plan.diagnostics = diagnostics
        plan.error_message = None
        plan.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()
        self.session.expire(plan, ["decisions"])

    async def reload(self, plan: VisualPlan) -> VisualPlan:
        loaded = await self.get_by_id(plan.id)
        if loaded is None:
            raise LookupError("visual plan disappeared during persistence")
        return loaded
