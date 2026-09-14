"""Identity-safe Human Review V1 operations."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import ProductionQAReport, WorkflowHumanReview
from app.repositories.human_review_repository import HumanReviewRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.operator import HumanReviewCreateIn
from app.services.production_orchestration_service import ProductionOrchestrationService
from app.services.program_audio import ProgramAudioError


class HumanReviewConflictError(RuntimeError):
    pass


class HumanReviewNotFoundError(RuntimeError):
    pass


class HumanReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workflows = WorkflowRepository(session)
        self.reviews = HumanReviewRepository(session)
        self.production = ProductionOrchestrationService(session)

    async def submit(
        self, workflow_id: uuid.UUID, payload: HumanReviewCreateIn, idempotency_key: str
    ) -> tuple[WorkflowHumanReview, bool]:
        existing = await self.reviews.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            same = (
                existing.workflow_execucao_id == workflow_id
                and existing.workflow_render_id == payload.workflow_render_id
                and existing.render_fingerprint == payload.render_fingerprint
                and existing.output_sha256 == payload.output_sha256
                and existing.status == payload.status
                and existing.note == payload.note
            )
            if not same:
                raise HumanReviewConflictError("Idempotency-Key já foi usada por outra decisão")
            return existing, False

        workflow = await self.workflows.obter_por_id_para_atualizacao(workflow_id)
        if workflow is None:
            raise HumanReviewNotFoundError("Workflow não encontrado")
        try:
            render = await self.production.current_render(workflow_id)
        except ProgramAudioError as exc:
            raise HumanReviewConflictError(
                f"Render final corrente não pôde ser resolvido ({exc.category})"
            ) from exc
        if (
            render is None
            or render.status != "completed"
            or render.render_fingerprint is None
            or render.output_sha256 is None
        ):
            raise HumanReviewConflictError("Render final corrente não está disponível")
        if (
            render.id != payload.workflow_render_id
            or render.render_fingerprint != payload.render_fingerprint
            or render.output_sha256 != payload.output_sha256
        ):
            raise HumanReviewConflictError(
                "O render mudou desde que a tela foi carregada; revise o output corrente"
            )

        qa_report = None
        if payload.qa_report_id is not None:
            qa_report = await self.session.get(ProductionQAReport, payload.qa_report_id)
            if (
                qa_report is None
                or qa_report.workflow_execucao_id != workflow_id
                or qa_report.workflow_render_id != render.id
                or qa_report.render_output_sha256 != render.output_sha256
                or (
                    payload.qa_request_fingerprint is not None
                    and qa_report.request_fingerprint != payload.qa_request_fingerprint
                )
            ):
                raise HumanReviewConflictError("O QA informado não pertence ao render corrente")
        elif payload.qa_request_fingerprint is not None:
            statement = select(ProductionQAReport).where(
                ProductionQAReport.workflow_execucao_id == workflow_id,
                ProductionQAReport.workflow_render_id == render.id,
                ProductionQAReport.request_fingerprint == payload.qa_request_fingerprint,
            )
            qa_report = (await self.session.execute(statement)).scalar_one_or_none()
            if qa_report is None:
                raise HumanReviewConflictError("O QA informado não pertence ao render corrente")

        return await self.reviews.append(
            workflow_execucao_id=workflow_id,
            workflow_render_id=render.id,
            render_fingerprint=render.render_fingerprint,
            output_sha256=render.output_sha256,
            qa_report_id=qa_report.id if qa_report else None,
            qa_request_fingerprint=qa_report.request_fingerprint if qa_report else None,
            status=payload.status,
            note=payload.note,
            operator_label=payload.operator_label,
            idempotency_key=idempotency_key,
        )
