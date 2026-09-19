"""Operator Console projections and direct voice/music previews."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.workflow import _production_timeline_response
from app.clients.voice_client import VoiceClient, VoiceClientError, VoiceClientTransientError
from app.core.config import settings
from app.core.database import get_db
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.operator import (
    VoiceProfileOut,
    VoiceProfilesOut,
    VoicePreviewRequest,
    MusicAssetCatalogOut,
    MusicCatalogOut,
    OperatorComposerInspectionOut,
    OperatorWorkflowHistoryOut,
    OperatorWorkflowSnapshotOut,
    OperatorWorkflowSummaryOut,
    ReviewQueueItemOut,
)
from app.services.operator_console import OperatorConsoleService
from app.services.presentation_spec import PresentationInputError, build_presentation_spec
from app.services.visual_director_service import resolve_production_profile_config
from app.services.production_timeline_service import ProductionTimelineService
from app.services.program_audio import (
    ProgramAudioError,
    build_audio_mix_plan,
    load_music_registry,
    verify_music_asset,
)

router = APIRouter()


def _safe_audio_mix_payload(audio_mix):
    payload = audio_mix.model_dump(mode="json") if audio_mix else None
    if payload and payload.get("music", {}).get("enabled"):
        payload["music"]["asset"].pop("source_reference", None)
    return payload


@router.get("/workflows", response_model=list[OperatorWorkflowSummaryOut])
async def list_operator_workflows(
    session: Annotated[AsyncSession, Depends(get_db)],
    state: Literal["active", "needs_attention", "needs_review", "completed", "failed", "cancelled"]
    | None = None,
    search: str | None = Query(default=None, max_length=200),
    production_status: str | None = Query(default=None, max_length=32),
    review_status: str | None = Query(default=None, max_length=32),
    ordering: Literal["updated_desc", "created_desc", "title"] = "updated_desc",
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await OperatorConsoleService(session).list_summaries(
        state=state,
        search=search,
        production_status=production_status,
        review_status=review_status,
        ordering=ordering,
        limit=limit,
        offset=offset,
    )


@router.get("/workflows/{workflow_id}", response_model=OperatorWorkflowSnapshotOut)
async def get_operator_workflow(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    snapshot = await OperatorConsoleService(session).snapshot(workflow_id)
    if snapshot is None:
        raise HTTPException(404, "Workflow não encontrado")
    return snapshot


@router.get("/workflows/{workflow_id}/history", response_model=OperatorWorkflowHistoryOut)
async def get_operator_workflow_history(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    history = await OperatorConsoleService(session).history(workflow_id)
    if history is None:
        raise HTTPException(404, "Workflow não encontrado")
    return history


@router.get(
    "/workflows/{workflow_id}/composer-inspection",
    response_model=OperatorComposerInspectionOut,
)
async def get_composer_inspection(
    workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]
):
    """Read-only Presentation/Program Audio projection with sensitive source fields removed."""
    workflow = await WorkflowRepository(session).obter_por_id(
        workflow_id, com_blocos=True, com_transcricao=True
    )
    if workflow is None:
        raise HTTPException(404, "Workflow não encontrado")
    timeline = await ProductionTimelineService(session).current(workflow_id)
    if timeline is None:
        raise HTTPException(404, "Production Timeline compatível ainda não disponível")
    try:
        profile_config = resolve_production_profile_config(workflow.perfil_producao)
        burned_in_captions = profile_config.burned_in_captions if profile_config else True
        presentation = build_presentation_spec(
            workflow, duration_seconds=timeline.duration_seconds, burned_in_captions=burned_in_captions
        )
        audio_mix = build_audio_mix_plan(workflow, timeline, settings)
    except PresentationInputError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProgramAudioError as exc:
        raise HTTPException(409, detail={"category": exc.category, "message": str(exc)}) from exc
    return OperatorComposerInspectionOut(
        production_timeline=_production_timeline_response(timeline),
        presentation=presentation,
        audio_mix_plan=_safe_audio_mix_payload(audio_mix),
    )


@router.get("/review-queue", response_model=list[ReviewQueueItemOut])
async def get_review_queue(session: Annotated[AsyncSession, Depends(get_db)]):
    return await OperatorConsoleService(session).review_queue()


@router.get("/music-assets", response_model=MusicCatalogOut)
async def list_music_assets():
    try:
        registry = load_music_registry(settings)
    except ProgramAudioError as exc:
        raise HTTPException(503, detail={"category": exc.category, "message": str(exc)}) from exc
    return MusicCatalogOut(
        assets=[
            MusicAssetCatalogOut(
                asset_id=asset.asset_id,
                sha256=asset.sha256,
                duration_seconds=asset.duration_seconds,
                sample_rate=asset.sample_rate,
                channels=asset.channels,
                format=asset.format,
                integrated_lufs=asset.integrated_lufs,
                true_peak_dbtp=asset.true_peak_dbtp,
                moods=asset.moods,
                styles=asset.styles,
                energy=asset.energy,
                loopable=asset.loopable,
                source_owner=asset.source_owner,
                license_type=asset.license_type,
                monetization_allowed=asset.monetization_allowed,
                attribution_required=asset.attribution_required,
                attribution_text=asset.attribution_text,
            )
            for asset in registry.assets
        ]
    )


@router.get("/music-assets/{asset_id}/audio")
async def preview_music_asset(asset_id: str, sha256: str):
    try:
        registry = load_music_registry(settings)
        asset = next((item for item in registry.assets if item.asset_id == asset_id), None)
        if asset is None:
            raise HTTPException(404, "Music asset não encontrado")
        if asset.sha256 != sha256:
            raise HTTPException(409, "Music asset SHA não corresponde ao catálogo corrente")
        path = verify_music_asset(settings, asset)
    except ProgramAudioError as exc:
        raise HTTPException(409, detail={"category": exc.category, "message": str(exc)}) from exc
    return FileResponse(
        path,
        media_type=asset.content_type,
        filename=f"{asset.asset_id}.{asset.format}",
        content_disposition_type="inline",
        headers={
            "ETag": f'"{asset.sha256}"',
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Audio-SHA256": asset.sha256,
        },
    )


@router.get("/voice-profiles", response_model=VoiceProfilesOut)
async def list_voice_profiles():
    try:
        async with VoiceClient(settings) as voice:
            profiles = await voice.list_voices()
    except VoiceClientTransientError as exc:
        raise HTTPException(503, "Voice Service temporariamente indisponível") from exc
    except VoiceClientError as exc:
        raise HTTPException(502, "Não foi possível listar os voice profiles") from exc
    return VoiceProfilesOut(
        profiles=[VoiceProfileOut.model_validate(profile.model_dump()) for profile in profiles],
        defaults={"pt-BR": settings.voice_profile, "en-US": settings.voice_profile_en_us},
    )


@router.post("/voice-preview", response_class=Response)
async def create_voice_preview(payload: VoicePreviewRequest):
    try:
        async with VoiceClient(settings) as voice:
            profiles = await voice.list_voices()
            if not any(profile.name == payload.voice_profile for profile in profiles):
                raise HTTPException(409, "Profile não carregado: referência ainda não disponível")
            job = await voice.create_narration(
                text=payload.text,
                voice_profile=payload.voice_profile,
                generation_profile="narration_v2",
                language=payload.language,
                seed=303,
                idempotency_key=f"operator-preview:{uuid.uuid4()}",
            )
            completed = await voice.wait_for_narration(job.id)
            audio = await voice.download_audio(completed.id)
    except VoiceClientTransientError as exc:
        raise HTTPException(503, "Voice Service temporariamente indisponível") from exc
    except VoiceClientError as exc:
        raise HTTPException(502, "Não foi possível gerar o preview no Voice Service") from exc
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
