"""Safe, durable workflow music override operations."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.models.workflow import WorkflowExecucao
from app.repositories.workflow_repository import WorkflowRepository, evolve_production_error_history
from app.schemas.operator import MusicOverrideOut
from app.services.production_timeline_service import ProductionTimelineService
from app.services.program_audio import (
    ProgramAudioError,
    build_audio_mix_plan,
    load_music_registry,
    verify_music_asset,
)


class MusicOverrideConflictError(RuntimeError):
    pass


class MusicOverrideNotFoundError(RuntimeError):
    pass


class OperatorMusicService:
    def __init__(self, session: AsyncSession, config: Settings = settings) -> None:
        self.session = session
        self.config = config
        self.workflows = WorkflowRepository(session)
        self.timelines = ProductionTimelineService(session, config=config)

    @staticmethod
    def _response(workflow: WorkflowExecucao, changed: bool) -> MusicOverrideOut:
        return MusicOverrideOut(
            workflow_id=workflow.id,
            asset_id=workflow.music_override_asset_id,
            revision=workflow.music_override_revision,
            changed=changed,
            production_status=workflow.production_status,
            production_phase=workflow.production_phase,
        )

    async def set_override(
        self,
        workflow_id: uuid.UUID,
        asset_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> MusicOverrideOut:
        workflow = await self._locked(workflow_id)
        if workflow.music_override_last_idempotency_key == idempotency_key:
            return self._response(workflow, changed=False)
        self._validate_mutation(workflow, expected_revision)
        registry = load_music_registry(self.config)
        asset = next((item for item in registry.assets if item.asset_id == asset_id), None)
        if asset is None:
            raise MusicOverrideConflictError("Music asset não existe no catálogo corrente")
        try:
            verify_music_asset(self.config, asset)
            timeline = await self.timelines.current(workflow_id)
            if timeline is None:
                raise MusicOverrideConflictError("Production Timeline corrente não está disponível")
            detailed = await self.workflows.obter_por_id(
                workflow_id, com_blocos=True, com_transcricao=True
            )
            assert detailed is not None
            plan = build_audio_mix_plan(
                detailed,
                timeline,
                self.config,
                registry=registry,
                override_asset_id=asset_id,
            )
            if plan is None or not plan.music.enabled:
                raise MusicOverrideConflictError("Workflow não possui Program Audio com música")
        except ProgramAudioError as exc:
            raise MusicOverrideConflictError(f"{exc.category}: {exc}") from exc
        if workflow.music_override_revision > 0 and workflow.music_override_asset_id == asset_id:
            workflow.music_override_last_idempotency_key = idempotency_key
            await self.session.commit()
            return self._response(workflow, changed=False)
        return await self._apply(workflow, asset_id, idempotency_key)

    async def clear_override(
        self,
        workflow_id: uuid.UUID,
        expected_revision: int,
        idempotency_key: str,
    ) -> MusicOverrideOut:
        workflow = await self._locked(workflow_id)
        if workflow.music_override_last_idempotency_key == idempotency_key:
            return self._response(workflow, changed=False)
        self._validate_mutation(workflow, expected_revision)
        if workflow.music_override_revision > 0 and workflow.music_override_asset_id is None:
            workflow.music_override_last_idempotency_key = idempotency_key
            await self.session.commit()
            return self._response(workflow, changed=False)
        # Revision zero + NULL is still a meaningful mutation: it explicitly
        # suppresses any historical registry JSON override.
        return await self._apply(workflow, None, idempotency_key)

    async def _locked(self, workflow_id: uuid.UUID) -> WorkflowExecucao:
        workflow = await self.workflows.obter_por_id_para_atualizacao(workflow_id)
        if workflow is None:
            raise MusicOverrideNotFoundError("Workflow não encontrado")
        return workflow

    @staticmethod
    def _validate_mutation(workflow: WorkflowExecucao, expected_revision: int) -> None:
        if workflow.music_override_revision != expected_revision:
            raise MusicOverrideConflictError(
                "O override mudou desde que a tela foi carregada; atualize os dados"
            )
        if workflow.production_status in {"queued", "running"}:
            raise MusicOverrideConflictError("Aguarde a operação de produção atual terminar")
        if workflow.production_status == "cancelled" or workflow.status == "cancelada":
            raise MusicOverrideConflictError("Retome a produção antes de alterar a música")

    async def _apply(
        self, workflow: WorkflowExecucao, asset_id: str | None, idempotency_key: str
    ) -> MusicOverrideOut:
        now = dt.datetime.now(dt.UTC)
        workflow.production_error_history = evolve_production_error_history(
            workflow.production_error_history,
            previous_category=workflow.production_error_category,
            previous_message=workflow.production_error_message,
            next_category=None,
            next_message=None,
            status="queued",
            phase="render",
            now=now,
        )
        workflow.music_override_asset_id = asset_id
        workflow.music_override_revision += 1
        workflow.music_override_last_idempotency_key = idempotency_key
        workflow.production_status = "queued"
        workflow.production_phase = "render"
        workflow.production_error_category = None
        workflow.production_error_message = None
        workflow.production_completed_at = None
        await self.session.commit()
        await self.session.refresh(workflow)
        return self._response(workflow, changed=True)
