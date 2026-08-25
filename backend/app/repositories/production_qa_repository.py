"""Persistence boundary for versioned Stage 18 QA reports."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import ProductionQAReport
from app.services.production_qa_checks import ProductionQACheck, QAAggregate


class ProductionQARepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, report_id: UUID) -> ProductionQAReport | None:
        return await self.session.get(ProductionQAReport, report_id)

    async def get_by_fingerprint(self, fingerprint: str) -> ProductionQAReport | None:
        statement = select(ProductionQAReport).where(
            ProductionQAReport.request_fingerprint == fingerprint
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_current_for_workflow(
        self, workflow_id: UUID, qa_mode: str
    ) -> ProductionQAReport | None:
        statement = (
            select(ProductionQAReport)
            .where(
                ProductionQAReport.workflow_execucao_id == workflow_id,
                ProductionQAReport.qa_mode == qa_mode,
            )
            .order_by(ProductionQAReport.created_at.desc(), ProductionQAReport.id.desc())
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: UUID) -> Sequence[ProductionQAReport]:
        statement = (
            select(ProductionQAReport)
            .where(ProductionQAReport.workflow_execucao_id == workflow_id)
            .order_by(ProductionQAReport.created_at.desc())
        )
        return (await self.session.execute(statement)).scalars().all()

    async def create_or_get(self, **values: object) -> tuple[ProductionQAReport, bool]:
        fingerprint = str(values["request_fingerprint"])
        existing = await self.get_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False
        report = ProductionQAReport(**values)
        self.session.add(report)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_fingerprint(fingerprint)
            if existing is None:
                raise
            return existing, False
        await self.session.refresh(report)
        return report, True

    async def mark_running(self, report: ProductionQAReport) -> None:
        report.status = "running"
        report.publication_ready = False
        report.check_count = 0
        report.passed_count = 0
        report.warning_count = 0
        report.failed_count = 0
        report.blocked_count = 0
        report.checks = []
        report.metrics = None
        report.error_category = None
        report.error_message = None
        report.started_at = dt.datetime.now(dt.UTC)
        report.completed_at = None
        await self.session.commit()

    async def complete(
        self,
        report: ProductionQAReport,
        *,
        checks: list[ProductionQACheck],
        aggregate: QAAggregate,
        metrics: dict[str, object] | None,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        report.status = aggregate.status
        report.publication_ready = aggregate.publication_ready
        report.check_count = aggregate.check_count
        report.passed_count = aggregate.passed_count
        report.warning_count = aggregate.warning_count
        report.failed_count = aggregate.failed_count
        report.blocked_count = aggregate.blocked_count
        report.checks = [item.to_dict() for item in checks]
        report.metrics = metrics
        report.error_category = error_category
        report.error_message = error_message
        report.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def block(self, report: ProductionQAReport, category: str, message: str) -> None:
        report.status = "blocked"
        report.publication_ready = False
        report.error_category = category
        report.error_message = message[:1000]
        report.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def fail(self, report: ProductionQAReport, category: str, message: str) -> None:
        report.status = "failed"
        report.publication_ready = False
        report.error_category = category
        report.error_message = message[:1000]
        report.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def reload(self, report: ProductionQAReport) -> ProductionQAReport:
        await self.session.refresh(report)
        return report
