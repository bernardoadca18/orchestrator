"""
Endpoints de workflow: listar roteiros aprovados disponíveis no ideas-hub,
enfileirar geração de áudio, acompanhar progresso e baixar o resultado.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ideas_hub_client import ideas_hub_client
from app.core.config import settings
from app.core.database import get_db
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.workflow import RoteiroDisponivelOut, WorkflowCreateIn, WorkflowDetailOut, WorkflowOut
from app.tasks.audio_tasks import gerar_audio_workflow

router = APIRouter()


@router.get("/roteiros-disponiveis", response_model=list[RoteiroDisponivelOut])
async def listar_roteiros_disponiveis():
    """Proxy read-only para os roteiros aprovados no ideas-hub."""
    roteiros = await ideas_hub_client.listar_roteiros_aprovados()
    return [
        RoteiroDisponivelOut(
            id=r["id"],
            ideia_id=r["ideia_id"],
            versao=r["versao"],
            titulo=r["estrutura"]["titulo"],
            duracao_total_segundos=r["estrutura"]["duracao_total_segundos"],
            num_blocos=len(r["estrutura"]["blocos"]),
        )
        for r in roteiros
    ]


@router.post("", response_model=WorkflowOut, status_code=201)
async def criar_workflow(
    payload: WorkflowCreateIn, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)

    existente = await repo.obter_ativo_por_roteiro(payload.roteiro_id)
    if existente is not None:
        raise HTTPException(409, f"Já existe um workflow ativo para este roteiro (id={existente.id})")

    roteiro = await ideas_hub_client.obter_roteiro(payload.roteiro_id)
    if roteiro["status"] != "aprovado":
        raise HTTPException(400, "Roteiro precisa estar aprovado no ideas-hub para entrar na fila de produção")

    wf = await repo.criar(
        ideia_id=uuid.UUID(roteiro["ideia_id"]),
        roteiro_id=payload.roteiro_id,
        roteiro_snapshot=roteiro["estrutura"],
    )
    gerar_audio_workflow.delay(str(wf.id))
    return wf


@router.get("", response_model=list[WorkflowOut])
async def listar_workflows(session: Annotated[AsyncSession, Depends(get_db)], status: str | None = None):
    repo = WorkflowRepository(session)
    return await repo.listar(status=status)


@router.get("/{workflow_id}", response_model=WorkflowDetailOut)
async def obter_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    return wf


@router.patch("/{workflow_id}/retry", response_model=WorkflowOut)
async def repetir_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.status != "erro":
        raise HTTPException(400, "Só é possível repetir workflows com status 'erro'")
    if wf.tentativas >= wf.max_tentativas:
        raise HTTPException(400, f"Limite de tentativas atingido ({wf.max_tentativas})")

    await repo.incrementar_tentativa(wf.id)
    gerar_audio_workflow.delay(str(wf.id))
    return await repo.obter_por_id(wf.id)


@router.patch("/{workflow_id}/cancelar", response_model=WorkflowOut)
async def cancelar_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    await repo.atualizar_status(wf.id, "cancelada")
    return await repo.obter_por_id(wf.id)


@router.get("/{workflow_id}/audio")
async def baixar_audio_final(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None or not wf.audio_final_path:
        raise HTTPException(404, "Áudio final ainda não disponível para este workflow")
    return FileResponse(wf.audio_final_path, media_type="audio/wav", filename=f"{workflow_id}.wav")
