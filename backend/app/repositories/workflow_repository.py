"""Camada de acesso a dados para WorkflowExecucao e AudioBloco."""

import copy
import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import (
    AudioBloco,
    AudioBlocoTake,
    AudioChunk,
    AudioChunkTake,
    AudioEditUpdate,
    TranscricaoAudio,
    WorkflowExecucao,
)


def evolve_production_error_history(
    history_value: list[dict] | None,
    *,
    previous_category: str | None,
    previous_message: str | None,
    next_category: str | None,
    next_message: str | None,
    status: str,
    phase: str,
    now: dt.datetime,
) -> list[dict] | None:
    """Keep recovered failures auditable without presenting them as active."""

    history = [dict(item) for item in (history_value or [])]
    if next_category and next_message:
        last = history[-1] if history else None
        if not (
            last
            and last.get("category") == next_category
            and last.get("message") == next_message
            and last.get("recovered_at") is None
        ):
            history.append(
                {
                    "category": next_category,
                    "message": next_message,
                    "status": status,
                    "phase": phase,
                    "occurred_at": now.isoformat(),
                    "recovered_at": None,
                }
            )
    elif previous_category and previous_message:
        for item in reversed(history):
            if (
                item.get("category") == previous_category
                and item.get("message") == previous_message
                and item.get("recovered_at") is None
            ):
                item["recovered_at"] = now.isoformat()
                item["recovery_status"] = status
                item["recovery_phase"] = phase
                break
    return history or None


def _take_text_provenance(block: AudioBloco) -> dict[str, object]:
    """Snapshot current derivation inputs; old takes must not follow mutable block pointers."""
    return {
        "canonical_text": block.texto,
        "normalized_text": block.normalized_text,
        "synthesis_text": block.synthesis_text,
        "pronunciation_glossary_identity": block.pronunciation_glossary_identity,
        "pronunciation_replacements": copy.deepcopy(block.pronunciation_replacements),
        "text_normalizer_version": block.text_normalizer_version,
        "text_normalizer_identity": block.text_normalizer_identity,
        "voice_profile": getattr(block, "voice_profile", None),
        "voice_profile_identity": getattr(block, "voice_profile_identity", None),
        "generation_profile": getattr(block, "generation_profile", None),
        "generation_profile_identity": getattr(block, "generation_profile_identity", None),
        "voice_chunking_identity": getattr(block, "voice_chunking_identity", None),
    }


class WorkflowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def criar(
        self,
        ideia_id: uuid.UUID,
        roteiro_id: uuid.UUID,
        roteiro_snapshot: dict,
        perfil_producao: str | None = None,
    ) -> WorkflowExecucao:
        wf = WorkflowExecucao(
            ideia_id=ideia_id,
            roteiro_id=roteiro_id,
            roteiro_snapshot=roteiro_snapshot,
            perfil_producao=perfil_producao,
            production_status="queued",
            production_phase="audio",
        )
        self.session.add(wf)
        await self.session.commit()
        await self.session.refresh(wf)
        return wf

    async def obter_ativo_por_roteiro(self, roteiro_id: uuid.UUID) -> WorkflowExecucao | None:
        stmt = select(WorkflowExecucao).where(
            WorkflowExecucao.roteiro_id == roteiro_id,
            or_(
                WorkflowExecucao.production_status.in_(["queued", "running", "blocked"]),
                and_(
                    WorkflowExecucao.production_status.is_(None),
                    WorkflowExecucao.status.in_(["agendada", "gerando_audio", "audio_concluido"]),
                ),
            ),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def obter_por_id(
        self,
        workflow_id: uuid.UUID,
        com_blocos: bool = False,
        com_transcricao: bool = False,
    ) -> WorkflowExecucao | None:
        stmt = select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id)
        if com_blocos:
            stmt = stmt.options(
                selectinload(WorkflowExecucao.blocos).selectinload(AudioBloco.takes)
            )
        if com_transcricao:
            stmt = stmt.options(selectinload(WorkflowExecucao.transcricao))
        if com_blocos or com_transcricao:
            # The audio workflow creates blocks in the same AsyncSession after an
            # earlier load observed an empty collection. Re-populate eager
            # relationships so the identity map cannot hide newly committed rows
            # from alignment and consolidated-ASR stages.
            stmt = stmt.execution_options(populate_existing=True)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def listar(self, status: str | None = None) -> Sequence[WorkflowExecucao]:
        stmt = select(WorkflowExecucao).order_by(WorkflowExecucao.criado_em.desc())
        if status:
            stmt = stmt.where(WorkflowExecucao.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def atualizar_status(self, workflow_id: uuid.UUID, status: str, **campos) -> None:
        wf = await self.obter_por_id(workflow_id)
        if wf is None:
            return
        wf.status = status
        for chave, valor in campos.items():
            setattr(wf, chave, valor)
        await self.session.commit()

    async def incrementar_tentativa(self, workflow_id: uuid.UUID) -> None:
        wf = await self.obter_por_id(workflow_id)
        if wf is None:
            return
        wf.tentativas += 1
        wf.status = "agendada"
        wf.erro_mensagem = None
        await self.session.commit()

    async def atualizar_producao(
        self,
        workflow_id: uuid.UUID,
        *,
        status: str,
        phase: str,
        error_category: str | None = None,
        error_message: str | None = None,
        start: bool = False,
        complete: bool = False,
    ) -> WorkflowExecucao | None:
        wf = await self.obter_por_id(workflow_id)
        if wf is None:
            return None
        now = dt.datetime.now(dt.UTC)
        previous_category = wf.production_error_category
        previous_message = wf.production_error_message
        history = evolve_production_error_history(
            wf.production_error_history,
            previous_category=previous_category,
            previous_message=previous_message,
            next_category=error_category,
            next_message=error_message,
            status=status,
            phase=phase,
            now=now,
        )
        wf.production_status = status
        wf.production_phase = phase
        wf.production_error_category = error_category
        wf.production_error_message = error_message
        wf.production_error_history = history
        if start and wf.production_started_at is None:
            wf.production_started_at = dt.datetime.now(dt.UTC)
        if complete:
            wf.production_completed_at = dt.datetime.now(dt.UTC)
        elif status in {"queued", "running"}:
            wf.production_completed_at = None
        await self.session.commit()
        await self.session.refresh(wf)
        return wf

    async def invalidar_derivados_de_audio(
        self, workflow: WorkflowExecucao, blocks: Sequence[AudioBloco] | None = None
    ) -> None:
        """Drop only current pointers; immutable historical rows/files remain available.

        Also invalidates downstream derived artifacts (transcription, narration alignment,
        visual plan, production timeline) to ensure they are recomputed when audio changes.
        """
        workflow.audio_final_path = None
        workflow.audio_final_sha256 = None
        workflow.audio_final_sample_rate = None
        workflow.audio_final_channels = None
        workflow.audio_final_byte_size = None
        workflow.audio_final_frame_count = None
        workflow.audio_final_subtype = None
        workflow.audio_integrity = None
        workflow.narration_diagnostics = None
        workflow.duracao_audio_segundos = None
        workflow.mastering_config = None
        workflow.mastering_metrics = None
        for block in blocks or ():
            block.narration_start_seconds = None
            block.narration_end_seconds = None

        # The transcription is NOT invalidated here. The schema requires texto_completo
        # and palavras_timestamps to be NOT NULL. Instead, the transcription is
        # re-computed when audio_sha256 changes. The _ensure_transcription() path
        # checks if existing transcription.audio_sha256 matches the new audio SHA
        # and triggers re-transcription when they differ.

        # Reset production phase to audio to force re-derivation of all downstream stages
        # (narration_alignment -> visual_plan -> production_timeline -> render)
        workflow.production_phase = "audio"

        await self.session.flush()

    async def recoverable_productions(self) -> Sequence[WorkflowExecucao]:
        statement = (
            select(WorkflowExecucao)
            .where(WorkflowExecucao.production_status.in_(["queued", "running"]))
            .order_by(WorkflowExecucao.criado_em)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def completed_productions(self) -> Sequence[WorkflowExecucao]:
        statement = (
            select(WorkflowExecucao)
            .where(WorkflowExecucao.production_status == "completed")
            .order_by(WorkflowExecucao.criado_em)
        )
        return (await self.session.execute(statement)).scalars().all()

    # --- AudioBloco ----------------------------------------------------------

    async def ensure_bloco(
        self,
        *,
        workflow_id: uuid.UUID,
        ordem: int,
        tipo: str,
        texto: str,
        synthesis_text: str,
        source_text_sha256: str,
        synthesis_text_sha256: str,
        synthesis_identity: str,
        pronunciation_glossary_identity: str,
        text_normalizer_version: str,
        text_normalizer_identity: str,
        idempotency_key: str,
        voice_profile: str,
        voice_profile_identity: str,
        generation_profile: str,
        generation_profile_identity: str,
        normalized_text: str | None = None,
        pronunciation_replacements: list[dict] | None = None,
        voice_chunking_identity: str | None = None,
    ) -> tuple[AudioBloco, bool]:
        """Create/update a block and report whether its synthesis identity is unchanged."""
        workflow = (
            await self.session.execute(
                select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id).with_for_update()
            )
        ).scalar_one_or_none()
        stmt = (
            select(AudioBloco)
            .where(AudioBloco.workflow_execucao_id == workflow_id, AudioBloco.ordem == ordem)
            .with_for_update()
        )
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        unchanged = bloco is not None and bloco.synthesis_identity == synthesis_identity
        if bloco is None:
            bloco = AudioBloco(workflow_execucao_id=workflow_id, ordem=ordem)
            self.session.add(bloco)
        elif not unchanged:
            if bloco.requested_take_id is not None:
                pending = await self.obter_take(bloco.requested_take_id)
                if pending is not None and pending.status in {"queued", "running"}:
                    pending.status = "superseded"
                    pending.is_current = False
                bloco.requested_take_id = None
            bloco.status = "pendente"
            bloco.erro_mensagem = None
            bloco.job_id_voz = None
            bloco.resolved_seed = None
            bloco.initial_resolved_seed = None
            bloco.generation_attempt = 0
            bloco.retry_seed_strategy = None
            bloco.generation_attempt_history = None
            bloco.audio_path = None
            bloco.duracao_segundos = None
            bloco.audio_sha256 = None
            bloco.audio_sample_rate = None
            bloco.audio_channels = None
            bloco.audio_byte_size = None
            bloco.audio_frame_count = None
            bloco.audio_subtype = None
            bloco.artifact_integrity = None
            bloco.pause_diagnostics = None
            bloco.narration_start_seconds = None
            bloco.narration_end_seconds = None
            if workflow is not None:
                workflow.production_status = "queued"
                workflow.production_phase = "audio"
                workflow.production_error_category = None
                workflow.production_error_message = None
                workflow.production_completed_at = None
                await self.invalidar_derivados_de_audio(workflow, [bloco])
        bloco.tipo = tipo
        bloco.texto = texto
        bloco.normalized_text = normalized_text
        bloco.synthesis_text = synthesis_text
        bloco.source_text_sha256 = source_text_sha256
        bloco.synthesis_text_sha256 = synthesis_text_sha256
        bloco.synthesis_identity = synthesis_identity
        bloco.pronunciation_glossary_identity = pronunciation_glossary_identity
        bloco.pronunciation_replacements = pronunciation_replacements
        bloco.text_normalizer_version = text_normalizer_version
        bloco.text_normalizer_identity = text_normalizer_identity
        if bloco.idempotency_key is None or not unchanged:
            bloco.idempotency_key = idempotency_key
        bloco.voice_profile = voice_profile
        bloco.voice_profile_identity = voice_profile_identity
        bloco.generation_profile = generation_profile
        bloco.generation_profile_identity = generation_profile_identity
        bloco.voice_chunking_identity = voice_chunking_identity
        await self.session.commit()
        await self.session.refresh(bloco)
        return bloco, unchanged

    async def atualizar_bloco(self, bloco_id: uuid.UUID, **campos) -> None:
        stmt = select(AudioBloco).where(AudioBloco.id == bloco_id)
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        if bloco is None:
            return
        for chave, valor in campos.items():
            setattr(bloco, chave, valor)
        await self.session.commit()

    async def criar_take(
        self,
        *,
        bloco: AudioBloco,
        kind: str,
        request_key: str | None,
        idempotency_key: str,
        requested_seed: int | None,
        synthesis_identity: str | None,
        chunking_identity: str | None,
    ) -> tuple[AudioBlocoTake, bool]:
        locked_block = (
            await self.session.execute(
                select(AudioBloco).where(AudioBloco.id == bloco.id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_block is not None:
            bloco = locked_block
        if request_key:
            existing = (
                await self.session.execute(
                    select(AudioBlocoTake).where(AudioBlocoTake.request_key == request_key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
        max_index = (
            await self.session.execute(
                select(func.max(AudioBlocoTake.attempt_index)).where(
                    AudioBlocoTake.audio_bloco_id == bloco.id
                )
            )
        ).scalar_one()
        take = AudioBlocoTake(
            audio_bloco_id=bloco.id,
            attempt_index=(max_index if max_index is not None else -1) + 1,
            kind=kind,
            status="queued",
            request_key=request_key,
            idempotency_key=idempotency_key,
            requested_seed=requested_seed,
            synthesis_identity=synthesis_identity,
            chunking_identity=chunking_identity,
            **_take_text_provenance(bloco),
        )
        self.session.add(take)
        await self.session.flush()
        await self.session.commit()
        await self.session.refresh(take)
        return take, True

    async def solicitar_take(
        self,
        *,
        workflow_id: uuid.UUID,
        ordem: int,
        kind: str,
        request_key: str,
        idempotency_key: str,
        requested_seed: int | None,
    ) -> tuple[AudioBloco, AudioBlocoTake, bool]:
        existing = (
            await self.session.execute(
                select(AudioBlocoTake).where(AudioBlocoTake.request_key == request_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            bloco = await self.obter_bloco_por_id(existing.audio_bloco_id)
            if bloco is None:
                raise LookupError("audio block for take request disappeared")
            return bloco, existing, False
        workflow = (
            await self.session.execute(
                select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id).with_for_update()
            )
        ).scalar_one_or_none()
        if workflow is None:
            raise LookupError("workflow not found")
        # Re-check after acquiring the workflow lock so concurrent identical
        # HTTP requests cannot create two takes for one Idempotency-Key.
        existing = (
            await self.session.execute(
                select(AudioBlocoTake).where(AudioBlocoTake.request_key == request_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            bloco = await self.obter_bloco_por_id(existing.audio_bloco_id)
            if bloco is None:
                raise LookupError("audio block for take request disappeared")
            return bloco, existing, False
        bloco = (
            await self.session.execute(
                select(AudioBloco)
                .where(
                    AudioBloco.workflow_execucao_id == workflow_id,
                    AudioBloco.ordem == ordem,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if bloco is None:
            raise LookupError("audio block not found")
        if bloco.requested_take_id is not None:
            pending = await self.obter_take(bloco.requested_take_id)
            if pending is not None and pending.status in {"queued", "running"}:
                pending.status = "superseded"
                pending.is_current = False
            bloco.requested_take_id = None
        max_index = (
            await self.session.execute(
                select(func.max(AudioBlocoTake.attempt_index)).where(
                    AudioBlocoTake.audio_bloco_id == bloco.id
                )
            )
        ).scalar_one()
        take = AudioBlocoTake(
            audio_bloco_id=bloco.id,
            attempt_index=(max_index if max_index is not None else -1) + 1,
            kind=kind,
            status="queued",
            request_key=request_key,
            idempotency_key=idempotency_key,
            requested_seed=requested_seed,
            synthesis_identity=bloco.synthesis_identity,
            **_take_text_provenance(bloco),
        )
        self.session.add(take)
        await self.session.flush()
        bloco.requested_take_id = take.id
        bloco.status = "pendente"
        bloco.job_id_voz = None
        bloco.idempotency_key = idempotency_key
        bloco.resolved_seed = requested_seed
        bloco.initial_resolved_seed = requested_seed
        bloco.generation_attempt = 0
        bloco.retry_seed_strategy = None
        bloco.generation_attempt_history = []
        bloco.erro_mensagem = None
        workflow.status = "agendada"
        workflow.audio_phase = "waiting"
        workflow.production_status = "queued"
        workflow.production_phase = "audio"
        workflow.production_error_category = None
        workflow.production_error_message = None
        workflow.production_completed_at = None
        await self.invalidar_derivados_de_audio(workflow, [bloco])
        await self.session.commit()
        await self.session.refresh(bloco)
        await self.session.refresh(take)
        return bloco, take, True

    async def solicitar_takes(
        self,
        *,
        workflow_id: uuid.UUID,
        kind: str,
        request_key: str,
        idempotency_key_prefix: str,
        requested_seed_by_order: dict[int, int | None],
    ) -> list[tuple[AudioBloco, AudioBlocoTake]]:
        existing = (
            await self.session.execute(
                select(AudioBlocoTake).where(AudioBlocoTake.request_key == request_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            bloco = await self.obter_bloco_por_id(existing.audio_bloco_id)
            if bloco is None:
                raise LookupError("audio block for take request disappeared")
            return []
        workflow = (
            await self.session.execute(
                select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id).with_for_update()
            )
        ).scalar_one_or_none()
        if workflow is None:
            raise LookupError("workflow not found")
        existing = (
            await self.session.execute(
                select(AudioBlocoTake).where(AudioBlocoTake.request_key == request_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return []
        blocks = list(
            (
                await self.session.execute(
                    select(AudioBloco)
                    .where(AudioBloco.workflow_execucao_id == workflow_id)
                    .order_by(AudioBloco.ordem)
                    .with_for_update()
                )
            ).scalars()
        )
        result: list[tuple[AudioBloco, AudioBlocoTake]] = []
        for block in blocks:
            if block.requested_take_id is not None:
                pending = await self.obter_take(block.requested_take_id)
                if pending is not None and pending.status in {"queued", "running"}:
                    pending.status = "superseded"
                    pending.is_current = False
                block.requested_take_id = None
            max_index = (
                await self.session.execute(
                    select(func.max(AudioBlocoTake.attempt_index)).where(
                        AudioBlocoTake.audio_bloco_id == block.id
                    )
                )
            ).scalar_one()
            block_key = request_key if not result else f"{request_key}:block:{block.ordem}"
            take = AudioBlocoTake(
                audio_bloco_id=block.id,
                attempt_index=(max_index if max_index is not None else -1) + 1,
                kind=kind,
                status="queued",
                request_key=block_key,
                idempotency_key=f"{idempotency_key_prefix}:block:{block.ordem}",
                requested_seed=requested_seed_by_order.get(block.ordem),
                synthesis_identity=block.synthesis_identity,
                **_take_text_provenance(block),
            )
            self.session.add(take)
            await self.session.flush()
            block.requested_take_id = take.id
            block.status = "pendente"
            block.job_id_voz = None
            block.idempotency_key = take.idempotency_key
            block.resolved_seed = take.requested_seed
            block.initial_resolved_seed = take.requested_seed
            block.generation_attempt = 0
            block.retry_seed_strategy = None
            block.generation_attempt_history = []
            block.erro_mensagem = None
            result.append((block, take))
        workflow.status = "agendada"
        workflow.audio_phase = "waiting"
        workflow.production_status = "queued"
        workflow.production_phase = "audio"
        workflow.production_error_category = None
        workflow.production_error_message = None
        workflow.production_completed_at = None
        await self.invalidar_derivados_de_audio(workflow, blocks)
        await self.session.commit()
        for block, take in result:
            await self.session.refresh(block)
            await self.session.refresh(take)
        return result

    async def obter_take(self, take_id: uuid.UUID) -> AudioBlocoTake | None:
        return await self.session.get(AudioBlocoTake, take_id)

    async def listar_takes(self, bloco_id: uuid.UUID) -> Sequence[AudioBlocoTake]:
        statement = (
            select(AudioBlocoTake)
            .where(AudioBlocoTake.audio_bloco_id == bloco_id)
            .order_by(AudioBlocoTake.attempt_index)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def atualizar_take(self, take_id: uuid.UUID, **campos) -> None:
        take = await self.obter_take(take_id)
        if take is None:
            return
        for chave, valor in campos.items():
            setattr(take, chave, valor)
        await self.session.commit()

    async def promover_take(self, bloco_id: uuid.UUID, take_id: uuid.UUID, **campos) -> bool:
        block_fields = campos.pop("block_fields", None)
        bloco = (
            await self.session.execute(
                select(AudioBloco).where(AudioBloco.id == bloco_id).with_for_update()
            )
        ).scalar_one_or_none()
        take = await self.obter_take(take_id)
        if bloco is None or take is None:
            raise LookupError("take promotion target not found")
        if bloco.requested_take_id != take_id:
            take.status = "superseded"
            take.is_current = False
            await self.session.commit()
            return False
        if bloco.current_take_id is not None and bloco.current_take_id != take_id:
            previous = await self.obter_take(bloco.current_take_id)
            if previous is not None:
                previous.is_current = False
        for chave, valor in campos.items():
            setattr(take, chave, valor)
        take.status = "completed"
        take.is_current = True
        if isinstance(block_fields, dict):
            for chave, valor in block_fields.items():
                setattr(bloco, chave, valor)
        bloco.current_take_id = take.id
        bloco.requested_take_id = None
        await self.session.commit()
        await self.session.refresh(bloco)
        await self.session.refresh(take)
        return True

    async def obter_blocos_orfaos(self) -> Sequence[AudioBloco]:
        """Retorna blocos com status 'gerando' cujo workflow não está concluído nem cancelado."""
        stmt = (
            select(AudioBloco)
            .join(WorkflowExecucao)
            .where(
                AudioBloco.status == "gerando",
                WorkflowExecucao.status.in_(["agendada", "gerando_audio"]),
                AudioBloco.job_id_voz.isnot(None),
            )
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def obter_bloco_por_id(self, bloco_id: uuid.UUID) -> AudioBloco | None:
        stmt = select(AudioBloco).where(AudioBloco.id == bloco_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def obter_bloco_por_ordem(self, workflow_id: uuid.UUID, ordem: int) -> AudioBloco | None:
        stmt = select(AudioBloco).where(
            AudioBloco.workflow_execucao_id == workflow_id,
            AudioBloco.ordem == ordem,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # --- TranscricaoAudio ----------------------------------------------------

    async def upsert_transcricao(
        self,
        workflow_id: uuid.UUID,
        *,
        texto_completo: str,
        palavras_timestamps: list[dict],
        audio_sha256: str,
        provider: str,
        model: str,
        language: str | None,
    ) -> TranscricaoAudio:
        transcricao = await self.obter_transcricao(workflow_id)
        if transcricao is None:
            transcricao = TranscricaoAudio(workflow_execucao_id=workflow_id)
            self.session.add(transcricao)
        transcricao.texto_completo = texto_completo
        transcricao.palavras_timestamps = palavras_timestamps
        transcricao.audio_sha256 = audio_sha256
        transcricao.provider = provider
        transcricao.model = model
        transcricao.language = language
        await self.session.commit()
        await self.session.refresh(transcricao)
        return transcricao

    async def obter_transcricao(self, workflow_id: uuid.UUID) -> TranscricaoAudio | None:
        stmt = select(TranscricaoAudio).where(TranscricaoAudio.workflow_execucao_id == workflow_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # --- AudioChunk / AudioChunkTake ----------------------------------------

    async def persistir_chunks(
        self,
        bloco_id: uuid.UUID,
        chunks_data: list[dict],
    ) -> list[AudioChunk]:
        """Persist physical chunks with their initial active takes.

        Args:
            bloco_id: The audio block these chunks belong to
            chunks_data: List of chunk metadata from Voice Service, each containing:
                - chunk_index: Physical index within the block
                - chunk_id: Stable identifier (job_id_chunk_index format)
                - text: Canonical text from the script
                - synthesis_text: Text sent to TTS provider
                - start_char/end_char/boundary/pause_after_frames: Frozen physical chunk plan
                - chunking_identity/chunking_strategy_version: Chunker provenance
                - voice_profile_identity/generation_profile_identity: Frozen profile identities
                - source_job_id: Voice Service narration job that produced the chunk
                - seed: Deterministic seed used for synthesis
                - duration_seconds: Audio duration
                - sample_rate: Audio sample rate
                - audio_path: Local path to the chunk WAV file
                - audio_sha256: SHA256 hash of the audio file
                - audio_byte_size: Size in bytes

        Returns:
            List of persisted AudioChunk objects with their initial takes
        """
        bloco = await self.obter_bloco_por_id(bloco_id)
        if bloco is None:
            raise LookupError("audio block not found")

        # Check if chunks already exist for this block
        existing = await self.listar_chunks(bloco_id)
        if existing:
            return list(existing)

        persisted: list[AudioChunk] = []
        for chunk_meta in chunks_data:
            chunk_index = chunk_meta["chunk_index"]
            chunk_id = chunk_meta["chunk_id"]
            canonical_text = chunk_meta.get("text", "")
            synthesis_text = chunk_meta.get("synthesis_text", canonical_text)
            seed = chunk_meta["seed"]
            duration_seconds = chunk_meta.get("duration_seconds")
            sample_rate = chunk_meta.get("sample_rate")

            chunk = AudioChunk(
                audio_bloco_id=bloco_id,
                chunk_index=chunk_index,
                chunk_id=chunk_id,
                canonical_text=canonical_text,
                synthesis_text_snapshot=synthesis_text,
                start_char=chunk_meta["start_char"],
                end_char=chunk_meta["end_char"],
                boundary=chunk_meta["boundary"],
                pause_after_frames=chunk_meta["pause_after_frames"],
                chunking_identity=chunk_meta["chunking_identity"],
                chunking_strategy_version=chunk_meta["chunking_strategy_version"],
                voice_profile_identity=chunk_meta["voice_profile_identity"],
                generation_profile_identity=chunk_meta["generation_profile_identity"],
                source_job_id=chunk_meta["source_job_id"],
                seed=seed,
                duration_seconds=duration_seconds,
                sample_rate=sample_rate,
                status="pending",
            )
            self.session.add(chunk)
            await self.session.flush()

            # Create initial take
            take = AudioChunkTake(
                audio_chunk_id=chunk.id,
                take_number=1,
                kind="initial",
                status="completed",
                seed=seed,
                audio_path=chunk_meta.get("audio_path"),
                audio_sha256=chunk_meta.get("audio_sha256"),
                audio_byte_size=chunk_meta.get("audio_byte_size"),
                audio_sample_rate=sample_rate,
                audio_duration_seconds=duration_seconds,
                audio_frame_count=chunk_meta.get("audio_frame_count"),
                completed_at=dt.datetime.now(dt.UTC),
            )
            self.session.add(take)
            await self.session.flush()

            # Set current take and activate chunk
            chunk.current_take_id = take.id
            chunk.status = "active"

            persisted.append(chunk)

        await self.session.commit()
        for chunk in persisted:
            await self.session.refresh(chunk)
        return persisted

    async def listar_chunks(self, bloco_id: uuid.UUID) -> Sequence[AudioChunk]:
        """List all chunks for a block, ordered by chunk_index."""
        stmt = (
            select(AudioChunk)
            .where(AudioChunk.audio_bloco_id == bloco_id)
            .order_by(AudioChunk.chunk_index)
            .options(selectinload(AudioChunk.takes))
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def obter_chunk(self, chunk_id: uuid.UUID) -> AudioChunk | None:
        """Get a single chunk by ID with its takes loaded."""
        stmt = (
            select(AudioChunk)
            .where(AudioChunk.id == chunk_id)
            .options(selectinload(AudioChunk.takes))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def obter_chunk_por_indice(
        self, bloco_id: uuid.UUID, chunk_index: int
    ) -> AudioChunk | None:
        """Get a chunk by its block and index."""
        stmt = select(AudioChunk).where(
            AudioChunk.audio_bloco_id == bloco_id,
            AudioChunk.chunk_index == chunk_index,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def criar_chunk_take(
        self,
        chunk_id: uuid.UUID,
        seed: int,
        kind: str = "regeneration",
        *,
        idempotency_key: str | None = None,
    ) -> AudioChunkTake:
        """Create a new candidate take for a chunk.

        The take starts in 'queued' status and must be updated after
        regeneration completes.
        """
        chunk = (
            await self.session.execute(
                select(AudioChunk).where(AudioChunk.id == chunk_id).with_for_update()
            )
        ).scalar_one_or_none()
        if chunk is None:
            raise LookupError("chunk not found")

        if idempotency_key is not None:
            existing = await self.obter_chunk_take_por_idempotency(idempotency_key)
            if existing is not None:
                if existing.audio_chunk_id != chunk_id or existing.seed != seed:
                    raise ValueError(
                        "Idempotency-Key was already used with different chunk inputs"
                    )
                return existing

        # Determine next take number
        max_take = (
            await self.session.execute(
                select(func.max(AudioChunkTake.take_number)).where(
                    AudioChunkTake.audio_chunk_id == chunk_id
                )
            )
        ).scalar_one()
        take_number = (max_take or 0) + 1

        take = AudioChunkTake(
            audio_chunk_id=chunk_id,
            take_number=take_number,
            kind=kind,
            status="queued",
            seed=seed,
            idempotency_key=idempotency_key,
        )
        self.session.add(take)
        await self.session.commit()
        await self.session.refresh(take)
        return take

    async def obter_chunk_take_por_idempotency(
        self, idempotency_key: str
    ) -> AudioChunkTake | None:
        stmt = select(AudioChunkTake).where(
            AudioChunkTake.idempotency_key == idempotency_key
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def atualizar_chunk_take(self, take_id: uuid.UUID, **campos) -> None:
        """Update a chunk take's fields."""
        take = await self.session.get(AudioChunkTake, take_id)
        if take is None:
            return
        for chave, valor in campos.items():
            setattr(take, chave, valor)
        await self.session.commit()

    async def promover_chunk_take(
        self, take_id: uuid.UUID, *, workflow_id: uuid.UUID | None = None
    ) -> bool:
        """Promote a candidate take to active.

        Atomically:
        - Mark the current active take as superseded
        - Set the candidate take as completed
        - Update the chunk's current_take_id
        - Increment the durable workflow/block edit revision

        Returns True if promotion succeeded, False if take was already superseded.
        """
        take = await self.session.get(AudioChunkTake, take_id)
        if take is None:
            raise LookupError("take not found")
        chunk = await self.session.get(AudioChunk, take.audio_chunk_id)
        if chunk is None:
            raise LookupError("chunk not found")
        block = await self.session.get(AudioBloco, chunk.audio_bloco_id)
        if block is None:
            raise LookupError("audio block not found")
        if workflow_id is not None and block.workflow_execucao_id != workflow_id:
            raise LookupError("take does not belong to workflow")

        workflow = (
            await self.session.execute(
                select(WorkflowExecucao)
                .where(WorkflowExecucao.id == block.workflow_execucao_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if workflow is None:
            raise LookupError("workflow not found")
        chunk = (
            await self.session.execute(
                select(AudioChunk).where(AudioChunk.id == chunk.id).with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        take = (
            await self.session.execute(
                select(AudioChunkTake)
                .where(AudioChunkTake.id == take_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

        if chunk.current_take_id == take.id:
            await self.session.commit()
            return True
        if take.status in {"superseded", "rejected", "failed"}:
            return False
        if take.status != "completed":
            return False

        # Supersede current active take if exists
        if chunk.current_take_id is not None and chunk.current_take_id != take_id:
            current_take = await self.session.get(AudioChunkTake, chunk.current_take_id)
            if current_take is not None:
                current_take.status = "superseded"

        # Promote the candidate
        take.status = "completed"
        take.completed_at = dt.datetime.now(dt.UTC)
        chunk.current_take_id = take.id
        chunk.status = "active"
        chunk.updated_at = dt.datetime.now(dt.UTC)
        workflow.audio_edit_revision += 1
        block.audio_edit_revision = workflow.audio_edit_revision

        await self.session.commit()
        await self.session.refresh(chunk)
        await self.session.refresh(take)
        return True

    async def request_audio_edit_update(
        self, workflow_id: uuid.UUID, idempotency_key: str
    ) -> tuple[AudioEditUpdate | None, bool]:
        """Create/replay one batch update without launching duplicate work."""
        workflow = (
            await self.session.execute(
                select(WorkflowExecucao)
                .where(WorkflowExecucao.id == workflow_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if workflow is None:
            raise LookupError("workflow not found")
        existing = (
            await self.session.execute(
                select(AudioEditUpdate).where(
                    AudioEditUpdate.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.workflow_execucao_id != workflow_id:
                raise ValueError("Idempotency-Key was already used for another workflow")
            if existing.status == "failed":
                if existing.error_stage == "edit_snapshot_stale":
                    existing.edit_snapshot = None
                    existing.target_revision = workflow.audio_edit_revision
                existing.status = "pending"
                existing.error_stage = None
                existing.error_message = None
                existing.completed_at = None
                await self.session.commit()
                await self.session.refresh(existing)
                return existing, True
            return existing, False

        active = (
            await self.session.execute(
                select(AudioEditUpdate)
                .where(
                    AudioEditUpdate.workflow_execucao_id == workflow_id,
                    AudioEditUpdate.status.in_(["pending", "running"]),
                )
                .order_by(AudioEditUpdate.created_at.desc())
            )
        ).scalars().first()
        if active is not None:
            return active, False
        if workflow.audio_edit_revision <= workflow.audio_materialized_revision:
            return None, False

        update = AudioEditUpdate(
            workflow_execucao_id=workflow_id,
            idempotency_key=idempotency_key,
            target_revision=workflow.audio_edit_revision,
            status="pending",
        )
        self.session.add(update)
        await self.session.commit()
        await self.session.refresh(update)
        return update, True

    async def claim_audio_edit_update(
        self, update_id: uuid.UUID, *, recovery: bool = False
    ) -> AudioEditUpdate | None:
        """Claim one update and freeze the exact selected-take snapshot."""
        unresolved = await self.session.get(AudioEditUpdate, update_id)
        if unresolved is None:
            return None
        workflow = (
            await self.session.execute(
                select(WorkflowExecucao)
                .where(WorkflowExecucao.id == unresolved.workflow_execucao_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        update = (
            await self.session.execute(
                select(AudioEditUpdate)
                .where(AudioEditUpdate.id == update_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if update.status == "completed":
            return None
        if update.status == "running" and not recovery:
            return None
        if update.status == "failed":
            return None
        if update.edit_snapshot is None:
            update.target_revision = max(
                update.target_revision, workflow.audio_edit_revision
            )
            blocks = list(
                (
                    await self.session.execute(
                        select(AudioBloco)
                        .where(
                            AudioBloco.workflow_execucao_id == workflow.id,
                            AudioBloco.audio_edit_revision
                            > AudioBloco.audio_materialized_revision,
                            AudioBloco.audio_edit_revision <= update.target_revision,
                        )
                        .order_by(AudioBloco.ordem)
                        .options(
                            selectinload(AudioBloco.chunks).selectinload(
                                AudioChunk.takes
                            )
                        )
                    )
                ).scalars()
            )
            update.edit_snapshot = {
                "version": "audio_edit_snapshot_v1",
                "target_revision": update.target_revision,
                "blocks": [
                    {
                        "block_id": str(block.id),
                        "order": block.ordem,
                        "edit_revision": block.audio_edit_revision,
                        "chunks": [
                            {
                                "chunk_id": str(chunk.id),
                                "chunk_index": chunk.chunk_index,
                                "take_id": (
                                    str(chunk.current_take_id)
                                    if chunk.current_take_id is not None
                                    else None
                                ),
                            }
                            for chunk in sorted(
                                block.chunks, key=lambda item: item.chunk_index
                            )
                        ],
                    }
                    for block in blocks
                ],
            }
        update.status = "running"
        update.started_at = update.started_at or dt.datetime.now(dt.UTC)
        update.error_stage = None
        update.error_message = None
        await self.session.commit()
        await self.session.refresh(update)
        return update

    async def mark_audio_edit_update_failed(
        self, update_id: uuid.UUID, *, stage: str, message: str
    ) -> None:
        update = await self.session.get(AudioEditUpdate, update_id)
        if update is None:
            return
        update.status = "failed"
        update.error_stage = stage
        update.error_message = message
        update.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def mark_audio_edit_update_completed(self, update_id: uuid.UUID) -> None:
        update = await self.session.get(AudioEditUpdate, update_id)
        if update is None:
            return
        update.status = "completed"
        update.error_stage = None
        update.error_message = None
        update.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def get_audio_edit_update(
        self, update_id: uuid.UUID
    ) -> AudioEditUpdate | None:
        return await self.session.get(AudioEditUpdate, update_id)

    async def recoverable_audio_edit_updates(self) -> Sequence[AudioEditUpdate]:
        statement = (
            select(AudioEditUpdate)
            .where(AudioEditUpdate.status.in_(["pending", "running"]))
            .with_for_update()
        )
        updates = list((await self.session.execute(statement)).scalars())
        for update in updates:
            if update.status == "running":
                update.status = "pending"
        await self.session.commit()
        return updates

    async def descartar_chunk_take(self, take_id: uuid.UUID) -> bool:
        """Mark a candidate take as rejected."""
        take = await self.session.get(AudioChunkTake, take_id)
        if take is None:
            raise LookupError("take not found")

        if take.status in {"rejected", "superseded", "failed"}:
            return False

        chunk = await self.obter_chunk(take.audio_chunk_id)
        if chunk is None:
            raise LookupError("chunk not found")
        if chunk.current_take_id == take.id:
            return False

        take.status = "rejected"
        await self.session.commit()
        return True
