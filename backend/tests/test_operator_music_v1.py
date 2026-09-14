from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.workflow import ProgramMusicAssetOut
from app.services.operator_music import MusicOverrideConflictError, OperatorMusicService
from app.services.program_audio import MusicRegistryDocument


def asset():
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
        source_reference="owned",
        license_type="owned",
        monetization_allowed=True,
        attribution_required=False,
        attribution_text=None,
    )


def workflow():
    return SimpleNamespace(
        id=uuid.uuid4(),
        music_override_asset_id=None,
        music_override_revision=0,
        music_override_last_idempotency_key=None,
        production_status="completed",
        production_phase="completed",
        production_error_history=None,
        production_error_category=None,
        production_error_message=None,
        production_completed_at=dt.datetime.now(dt.UTC),
        status="audio_concluido",
        # These identities prove the music mutation leaves upstream state alone.
        audio_final_sha256="narration",
        narration_alignment_marker="alignment",
        visual_plan_marker="visual",
        timeline_marker="timeline",
    )


def service_for(current):
    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    service = OperatorMusicService(session)
    service.workflows.obter_por_id_para_atualizacao = AsyncMock(return_value=current)
    service.workflows.obter_por_id = AsyncMock(return_value=current)
    service.timelines.current = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    return service


@pytest.mark.asyncio
async def test_valid_override_restarts_only_render_and_preserves_upstream() -> None:
    current = workflow()
    service = service_for(current)
    registry = MusicRegistryDocument(schema_version="music_asset_registry_v1", assets=[asset()])
    with (
        patch("app.services.operator_music.load_music_registry", return_value=registry),
        patch("app.services.operator_music.verify_music_asset"),
        patch(
            "app.services.operator_music.build_audio_mix_plan",
            return_value=SimpleNamespace(music=SimpleNamespace(enabled=True)),
        ),
    ):
        result = await service.set_override(current.id, "track-01", 0, "music:set:1")
    assert result.changed and result.revision == 1
    assert current.production_status == "queued" and current.production_phase == "render"
    assert current.audio_final_sha256 == "narration"
    assert current.narration_alignment_marker == "alignment"
    assert current.visual_plan_marker == "visual"
    assert current.timeline_marker == "timeline"


@pytest.mark.asyncio
async def test_invalid_or_concurrent_override_is_rejected() -> None:
    current = workflow()
    current.music_override_revision = 2
    service = service_for(current)
    with pytest.raises(MusicOverrideConflictError, match="override mudou"):
        await service.set_override(current.id, "track-01", 1, "music:set:2")


@pytest.mark.asyncio
async def test_clear_override_suppresses_legacy_registry_override_and_restarts_render() -> None:
    current = workflow()
    service = service_for(current)
    result = await service.clear_override(current.id, 0, "music:clear:1")
    assert result.changed and result.asset_id is None and result.revision == 1
    assert current.production_phase == "render"


@pytest.mark.asyncio
async def test_repeated_mutation_key_is_idempotent() -> None:
    current = workflow()
    current.music_override_asset_id = "track-01"
    current.music_override_revision = 1
    current.music_override_last_idempotency_key = "music:set:1"
    service = service_for(current)
    result = await service.set_override(current.id, "track-01", 0, "music:set:1")
    assert result.changed is False and result.revision == 1
