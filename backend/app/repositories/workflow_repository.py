"""Camada de acesso a dados para WorkflowExecucao e AudioBloco."""

import copy
import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import AudioBloco, AudioBlocoTake, TranscricaoAudio, WorkflowExecucao


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
        """Drop only current pointers; immutable historical rows/files remain available."""
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
