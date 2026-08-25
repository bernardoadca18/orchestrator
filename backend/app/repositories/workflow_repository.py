"""Camada de acesso a dados para WorkflowExecucao e AudioBloco."""

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import AudioBloco, TranscricaoAudio, WorkflowExecucao


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
            stmt = stmt.options(selectinload(WorkflowExecucao.blocos))
        if com_transcricao:
            stmt = stmt.options(selectinload(WorkflowExecucao.transcricao))
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
        wf.production_status = status
        wf.production_phase = phase
        wf.production_error_category = error_category
        wf.production_error_message = error_message
        if start and wf.production_started_at is None:
            wf.production_started_at = dt.datetime.now(dt.UTC)
        if complete:
            wf.production_completed_at = dt.datetime.now(dt.UTC)
        elif status in {"queued", "running"}:
            wf.production_completed_at = None
        await self.session.commit()
        await self.session.refresh(wf)
        return wf

    async def recoverable_productions(self) -> Sequence[WorkflowExecucao]:
        statement = (
            select(WorkflowExecucao)
            .where(WorkflowExecucao.production_status.in_(["queued", "running"]))
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
        idempotency_key: str,
        voice_profile: str,
        voice_profile_identity: str,
        generation_profile: str,
        generation_profile_identity: str,
    ) -> tuple[AudioBloco, bool]:
        """Create/update a block and report whether its synthesis identity is unchanged."""
        stmt = select(AudioBloco).where(
            AudioBloco.workflow_execucao_id == workflow_id, AudioBloco.ordem == ordem
        )
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        unchanged = bloco is not None and bloco.synthesis_identity == synthesis_identity
        if bloco is None:
            bloco = AudioBloco(workflow_execucao_id=workflow_id, ordem=ordem)
            self.session.add(bloco)
        elif not unchanged:
            bloco.status = "pendente"
            bloco.erro_mensagem = None
            bloco.job_id_voz = None
            bloco.resolved_seed = None
            bloco.generation_attempt = 0
            bloco.audio_path = None
            bloco.duracao_segundos = None
            bloco.audio_sha256 = None
            bloco.audio_sample_rate = None
            bloco.audio_channels = None
            bloco.audio_byte_size = None
            bloco.narration_start_seconds = None
            bloco.narration_end_seconds = None
        bloco.tipo = tipo
        bloco.texto = texto
        bloco.synthesis_text = synthesis_text
        bloco.source_text_sha256 = source_text_sha256
        bloco.synthesis_text_sha256 = synthesis_text_sha256
        bloco.synthesis_identity = synthesis_identity
        bloco.pronunciation_glossary_identity = pronunciation_glossary_identity
        if bloco.idempotency_key is None or not unchanged:
            bloco.idempotency_key = idempotency_key
        bloco.voice_profile = voice_profile
        bloco.voice_profile_identity = voice_profile_identity
        bloco.generation_profile = generation_profile
        bloco.generation_profile_identity = generation_profile_identity
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
