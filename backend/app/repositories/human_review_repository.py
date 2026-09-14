"""Append-only persistence for render-bound operator review decisions."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import WorkflowHumanReview


class HumanReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_idempotency_key(self, key: str) -> WorkflowHumanReview | None:
        statement = select(WorkflowHumanReview).where(
            WorkflowHumanReview.idempotency_key == key
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: UUID) -> Sequence[WorkflowHumanReview]:
        statement = (
            select(WorkflowHumanReview)
            .where(WorkflowHumanReview.workflow_execucao_id == workflow_id)
            .order_by(WorkflowHumanReview.created_at.desc(), WorkflowHumanReview.id.desc())
        )
        return (await self.session.execute(statement)).scalars().all()

    async def current_for_render(
        self, workflow_id: UUID, render_id: UUID, fingerprint: str, output_sha256: str
    ) -> WorkflowHumanReview | None:
        statement = (
            select(WorkflowHumanReview)
            .where(
                WorkflowHumanReview.workflow_execucao_id == workflow_id,
                WorkflowHumanReview.workflow_render_id == render_id,
                WorkflowHumanReview.render_fingerprint == fingerprint,
                WorkflowHumanReview.output_sha256 == output_sha256,
            )
            .order_by(WorkflowHumanReview.created_at.desc(), WorkflowHumanReview.id.desc())
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def append(self, **values: object) -> tuple[WorkflowHumanReview, bool]:
        key = str(values["idempotency_key"])
        existing = await self.get_by_idempotency_key(key)
        if existing is not None:
            return existing, False
        review = WorkflowHumanReview(**values)
        self.session.add(review)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_idempotency_key(key)
            if existing is None:
                raise
            return existing, False
        await self.session.refresh(review)
        return review, True
