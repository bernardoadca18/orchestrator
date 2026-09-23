"""Operator image actions; binaries remain in the Media Indexer registry."""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.storyboard import StoryboardError
from app.services.storyboard_service import StoryboardService

router = APIRouter(prefix="/workflow", tags=["storyboard"])
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{workflow_id}/storyboard")
async def get_storyboard(workflow_id: uuid.UUID, session: DB):
    try:
        return await StoryboardService(session).view(workflow_id)
    except StoryboardError as exc:
        raise HTTPException(404, {"category": exc.category, "message": str(exc)}) from exc


@router.post("/{workflow_id}/storyboard/scenes/{scene_id}/image")
async def upload_image(
    workflow_id: uuid.UUID, scene_id: str, session: DB, image: Annotated[UploadFile, File()]
):
    try:
        return await StoryboardService(session).upload(workflow_id, scene_id, image)
    except StoryboardError as exc:
        raise HTTPException(409, {"category": exc.category, "message": str(exc)}) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Media Indexer image ingest failed") from exc


@router.delete("/{workflow_id}/storyboard/scenes/{scene_id}/image")
async def remove_image(workflow_id: uuid.UUID, scene_id: str, session: DB):
    try:
        return await StoryboardService(session).remove(workflow_id, scene_id)
    except StoryboardError as exc:
        raise HTTPException(409, {"category": exc.category, "message": str(exc)}) from exc


@router.get("/{workflow_id}/storyboard/scenes/{scene_id}/image")
async def preview_image(workflow_id: uuid.UUID, scene_id: str, session: DB):
    service = StoryboardService(session)
    try:
        wf = await service.workflow(workflow_id)
    except StoryboardError as exc:
        raise HTTPException(404, str(exc)) from exc
    asset = (wf.storyboard_bindings or {}).get(scene_id)
    if not asset:
        raise HTTPException(404, "Scene has no bound image")
    async with httpx.AsyncClient(
        base_url=service.config.media_indexer_api_url, timeout=60
    ) as client:
        response = await client.get(f"/media-assets/{asset['media_id']}/content")
        response.raise_for_status()
    return Response(
        response.content, media_type=asset["content_type"], headers={"Cache-Control": "no-cache"}
    )
