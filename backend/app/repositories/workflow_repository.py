"""Camada de acesso a dados para WorkflowExecucao e AudioBloco."""
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import AudioBloco, WorkflowExecucao


class WorkflowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def criar(self, ideia_id: uuid.UUID, roteiro_id: uuid.UUID, roteiro_snapshot: dict) -> WorkflowExecucao:
        wf = WorkflowExecucao(ideia_id=ideia_id, roteiro_id=roteiro_id, roteiro_snapshot=roteiro_snapshot)
        self.session.add(wf)
        await self.session.commit()
        await self.session.refresh(wf)
        return wf

    async def obter_ativo_por_roteiro(self, roteiro_id: uuid.UUID) -> WorkflowExecucao | None:
        stmt = select(WorkflowExecucao).where(
            WorkflowExecucao.roteiro_id == roteiro_id,
            WorkflowExecucao.status.in_(["agendada", "gerando_audio", "audio_concluido"]),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def obter_por_id(self, workflow_id: uuid.UUID, com_blocos: bool = False) -> WorkflowExecucao | None:
        stmt = select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id)
        if com_blocos:
            stmt = stmt.options(selectinload(WorkflowExecucao.blocos))
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

    # --- AudioBloco ----------------------------------------------------------

    async def upsert_bloco(
        self, workflow_id: uuid.UUID, ordem: int, tipo: str, texto: str
    ) -> AudioBloco:
        stmt = select(AudioBloco).where(
            AudioBloco.workflow_execucao_id == workflow_id, AudioBloco.ordem == ordem
        )
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        if bloco is None:
            bloco = AudioBloco(workflow_execucao_id=workflow_id, ordem=ordem, tipo=tipo, texto=texto)
            self.session.add(bloco)
        else:
            bloco.status = "pendente"
            bloco.erro_mensagem = None
        await self.session.commit()
        await self.session.refresh(bloco)
        return bloco

    async def atualizar_bloco(self, bloco_id: uuid.UUID, **campos) -> None:
        stmt = select(AudioBloco).where(AudioBloco.id == bloco_id)
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        if bloco is None:
            return
        for chave, valor in campos.items():
            setattr(bloco, chave, valor)
        await self.session.commit()
