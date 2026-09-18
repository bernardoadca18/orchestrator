import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.thumbnail_director import ThumbnailClientError, ThumbnailDirectorClient
from app.core.database import get_db
from app.models.workflow import WorkflowThumbnailPackage
from app.services.thumbnails import ThumbnailService, candidate_for, projection
from app.tasks.thumbnail_tasks import generate_thumbnails

router = APIRouter(prefix="/operator/workflows/{workflow_id}/thumbnails", tags=["operator"])
DB = Annotated[AsyncSession, Depends(get_db)]


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: Literal["A", "B", "C"]
    package_id: uuid.UUID | None = None


async def enqueue(workflow_id, session, regenerate):
    package = await ThumbnailService(session).ensure(workflow_id, regenerate)
    if package.status == "pending":
        try:
            generate_thumbnails.delay(str(package.id))
        except Exception:
            # The committed pending row is recoverable; a repeat generate dispatches it.
            raise HTTPException(
                503, "Package salvo; fila indisponível. Tente gerar novamente."
            ) from None
    return projection(package)


@router.post("/generate", status_code=202)
async def generate(workflow_id: uuid.UUID, session: DB):
    return await enqueue(workflow_id, session, False)


@router.post("/regenerate", status_code=202)
async def regenerate(workflow_id: uuid.UUID, session: DB):
    return await enqueue(workflow_id, session, True)


@router.get("")
async def get(workflow_id: uuid.UUID, session: DB):
    service = ThumbnailService(session)
    await service.workflow(workflow_id)
    current = await service.current(workflow_id)
    history = (
        await session.scalars(
            select(WorkflowThumbnailPackage)
            .where(WorkflowThumbnailPackage.workflow_execucao_id == workflow_id)
            .order_by(WorkflowThumbnailPackage.creative_revision.desc())
            .limit(50)
        )
    ).all()
    selected = (
        candidate_for(current, current.selected_candidate_id)
        if current and current.selected_candidate_id
        else None
    )
    return {
        "current_package": projection(current) if current else None,
        "history": [
            {
                k: getattr(p, k)
                for k in (
                    "id",
                    "creative_revision",
                    "status",
                    "created_at",
                    "selected_candidate_id",
                )
            }
            for p in history
        ],
        "selected_candidate": selected,
    }


@router.post("/select")
async def select_candidate(workflow_id: uuid.UUID, body: Selection, session: DB):
    return projection(
        await ThumbnailService(session).select(workflow_id, body.candidate_id, body.package_id)
    )


@router.get("/{candidate_id}/content")
async def content(
    workflow_id: uuid.UUID,
    candidate_id: Literal["A", "B", "C"],
    session: DB,
    package_id: uuid.UUID | None = None,
):
    service = ThumbnailService(session)
    await service.workflow(workflow_id)
    package = await service.current(workflow_id)
    if package_id and (package is None or package.id != package_id):
        raise HTTPException(409, "Package atual mudou")
    candidate = candidate_for(package, candidate_id)
    try:
        data = await ThumbnailDirectorClient().artifact(candidate["artifact"])
    except ThumbnailClientError:
        raise HTTPException(502, "Artifact indisponível ou inválido") from None
    return Response(
        data,
        media_type="image/png",
        headers={
            "ETag": '"' + candidate["artifact"]["sha256"] + '"',
            "Cache-Control": "private, no-cache",
        },
    )
