from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routers.operator import (
    _safe_audio_mix_payload,
    get_operator_workflow,
    list_music_assets,
)
from app.schemas.operator import HumanReviewStateOut
from app.schemas.workflow import ProgramMusicAssetOut
from app.services.operator_console import (
    OperatorConsoleService,
    derive_attention,
    derive_capabilities,
)
from app.services.program_audio import MusicRegistryDocument, ProgramAudioError


def workflow(**overrides):
    now = dt.datetime.now(dt.UTC)
    values = {
        "id": uuid.uuid4(),
        "status": "audio_concluido",
        "production_status": "completed",
        "production_phase": "completed",
        "production_error_category": None,
        "production_error_message": None,
        "erro_mensagem": None,
        "audio_edit_revision": 0,
        "audio_materialized_revision": 0,
        "audio_final_sha256": "a" * 64,
        "roteiro_snapshot": {"titulo": "Vídeo real", "duracao_total_segundos": 60, "blocos": []},
        "perfil_producao": "game_analysis",
        "duracao_audio_segundos": 60.0,
        "criado_em": now,
        "atualizado_em": now,
        "music_override_asset_id": None,
        "music_override_revision": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def review_state(state: str = "pending") -> HumanReviewStateOut:
    return HumanReviewStateOut(state=state)


def test_capabilities_are_server_owned_and_include_impact() -> None:
    render = SimpleNamespace(
        status="completed", render_fingerprint="b" * 64, output_sha256="c" * 64
    )
    capabilities = derive_capabilities(workflow(), render=render, qa=None, music_available=True)
    assert capabilities["can_review"].allowed is True
    assert capabilities["can_cancel"].allowed is False
    assert capabilities["can_cancel"].reason
    assert "human_review" in capabilities["can_change_music"].invalidates


def test_attention_is_deterministic_and_completed_is_not_automatically_attention() -> None:
    assert derive_attention(workflow(), qa=None, review=review_state("approved")) == []
    failed = workflow(
        production_status="failed",
        production_phase="render",
        production_error_category="music_resolution_failed",
        production_error_message="No compatible asset",
    )
    reasons = derive_attention(failed, qa=None, review=review_state("unavailable"))
    assert [item.code for item in reasons] == ["music_unresolved"]


def test_stale_review_is_explicit_attention() -> None:
    reasons = derive_attention(workflow(), qa=None, review=review_state("stale"))
    assert [item.code for item in reasons] == ["stale_review"]


@pytest.mark.asyncio
async def test_operator_snapshot_projects_persisted_identity_without_online_ideas_hub() -> None:
    current = workflow()
    service = OperatorConsoleService(MagicMock())
    service.workflows.obter_por_id = AsyncMock(return_value=current)
    service.production.current_render = AsyncMock(return_value=None)
    service.reviews.list_for_workflow = AsyncMock(return_value=[])
    with (
        patch("app.services.operator_console.NarrationAlignmentService") as alignment,
        patch("app.services.operator_console.VisualDirectorService") as visual,
        patch("app.services.operator_console.ProductionTimelineService") as timeline,
    ):
        alignment.return_value.current = AsyncMock(return_value=None)
        visual.return_value.current = AsyncMock(return_value=None)
        timeline.return_value.current = AsyncMock(return_value=None)
        snapshot = await service.snapshot(current.id)
    assert snapshot is not None
    assert snapshot.workflow.title == "Vídeo real"
    assert snapshot.workflow.human_review.state == "unavailable"
    assert snapshot.artifacts.visual_plan is None


@pytest.mark.asyncio
async def test_operator_snapshot_endpoint_returns_404_for_unknown_workflow() -> None:
    with (
        patch(
            "app.api.routers.operator.OperatorConsoleService.snapshot",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(HTTPException) as error,
    ):
        await get_operator_workflow(uuid.uuid4(), MagicMock())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_review_projection_marks_old_approval_stale_for_new_render() -> None:
    service = OperatorConsoleService(MagicMock())
    old = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_execucao_id=uuid.uuid4(),
        workflow_render_id=uuid.uuid4(),
        render_fingerprint="a" * 64,
        output_sha256="b" * 64,
        qa_report_id=None,
        qa_request_fingerprint=None,
        status="approved",
        note=None,
        operator_label=None,
        created_at=dt.datetime.now(dt.UTC),
    )
    service.reviews.list_for_workflow = AsyncMock(return_value=[old])
    service.reviews.current_for_render = AsyncMock(return_value=None)
    current = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        phase="completed",
        progress=1.0,
        frames_rendered=1800,
        total_frames=1800,
        render_fingerprint="c" * 64,
        output_sha256="d" * 64,
        updated_at=dt.datetime.now(dt.UTC),
    )
    state = await service._review_state(old.workflow_execucao_id, current)
    assert state.state == "stale"
    assert state.latest_historical and state.latest_historical.status == "approved"


def music_asset() -> ProgramMusicAssetOut:
    return ProgramMusicAssetOut(
        asset_id="track-01",
        sha256="a" * 64,
        byte_size=10,
        content_type="audio/wav",
        format="wav",
        duration_seconds=60,
        sample_rate=48000,
        channels=2,
        integrated_lufs=-22,
        true_peak_dbtp=-2,
        moods=["analytical"],
        styles=["documentary"],
        energy=0.4,
        vocals=False,
        loopable=True,
        source_owner="NeoVids",
        source_reference="/private/path/never-exposed.wav",
        license_type="owned",
        monetization_allowed=True,
        attribution_required=False,
        attribution_text=None,
    )


@pytest.mark.asyncio
async def test_music_catalog_does_not_expose_paths_or_source_reference() -> None:
    registry = MusicRegistryDocument(
        schema_version="music_asset_registry_v1", assets=[music_asset()]
    )
    with patch("app.api.routers.operator.load_music_registry", return_value=registry):
        result = await list_music_assets()
    payload = result.model_dump(mode="json")
    assert payload["assets"][0]["asset_id"] == "track-01"
    assert "source_reference" not in payload["assets"][0]
    assert "/private/path" not in str(payload)


@pytest.mark.asyncio
async def test_invalid_music_registry_fails_with_safe_structured_error() -> None:
    with (
        patch(
            "app.api.routers.operator.load_music_registry",
            side_effect=ProgramAudioError("music_resolution_failed", "Music registry is invalid"),
        ),
        pytest.raises(Exception) as error,
    ):
        await list_music_assets()
    assert getattr(error.value, "status_code", None) == 503


def test_composer_inspection_strips_source_reference() -> None:
    plan = MagicMock()
    plan.model_dump.return_value = {
        "music": {
            "enabled": True,
            "asset": {"asset_id": "track", "source_reference": "/data/private.wav"},
        }
    }
    payload = _safe_audio_mix_payload(plan)
    assert payload == {"music": {"enabled": True, "asset": {"asset_id": "track"}}}
