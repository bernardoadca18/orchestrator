"""Small checks for physical edit timing and pre-tempo rate semantics."""

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.services import audio_edit_service as edits
from app.services.audio_service import detect_strong_speaker_outliers
from app.services.speaking_rate_calibration import SpeakingRateCalibrationService


@pytest.mark.asyncio
@pytest.mark.parametrize("locale,factor", [("pt-BR", 1.15), ("en-US", 1.0)])
@pytest.mark.parametrize("sample_count", [0, 3])
async def test_raw_take_calibration_scales_once_and_identity_tracks_tempo(
    locale, factor, sample_count
):
    config = Settings(_env_file=None)
    take = NS(
        id=uuid4(),
        audio_duration_seconds=10,
        synthesis_text="um " * 20,
        artifact_integrity={},
        completed_at=None,
        voice_profile="test",
        generation_profile="test",
    )
    session = NS(execute=AsyncMock(return_value=NS(all=lambda: [(take, None)] * sample_count)))
    service = SpeakingRateCalibrationService(session, config)
    snapshot = await service.snapshot(content_locale=locale)
    raw_wpm = 120 if sample_count else config.speaking_rate_fallback_words_per_minute
    raw_cps = 5.9 if sample_count else config.speaking_rate_fallback_chars_per_second
    assert snapshot["words_per_minute"] == pytest.approx(raw_wpm * factor)
    assert snapshot["chars_per_second"] == pytest.approx(raw_cps * factor)
    assert snapshot["version"] == "accepted_take_speaking_rate_v3"
    assert snapshot["tempo_version"] == "ffmpeg_narration_tempo_v1"
    assert snapshot["tempo_factor"] == factor
    config.audio_narration_tempo_pt_br = config.audio_narration_tempo_en_us = 1.2
    changed = await service.snapshot(content_locale=locale)
    assert changed["identity"] != snapshot["identity"]


def test_speaker_outliers_keep_real_indexes_and_require_three_numeric_scores():
    scores = [(0, None), (2, 0.992), (4, 0.991), (7, 0.941), (9, 0.993)]
    report = detect_strong_speaker_outliers(scores)
    assert report["offending_chunk_indexes"] == [7]
    assert report["evaluated_chunk_count"] == 4
    assert report["scores_by_chunk_index"][7] == 0.941
    assert detect_strong_speaker_outliers([(2, 0.979), (4, 0.982), (7, 0.981)]) is None
    assert detect_strong_speaker_outliers([(2, 0.992), (7, 0.941), (9, None)]) is None


@dataclass
class Metadata:
    duration_seconds: float
    frame_count: int
    sha256: str = "f" * 64
    sample_rate: int = 48000
    channels: int = 1
    byte_size: int = 864044
    subtype: str = "PCM_16"


@pytest.mark.asyncio
async def test_audio_edit_passes_physical_ratio_and_persists_paced_offsets(monkeypatch, tmp_path):
    # Deliberately use 0.9, not 1/1.15, to detect nominal-factor scaling.
    raw, final = Metadata(10, 480000), Metadata(9, 432000)
    offsets = [NS(start_seconds=0, end_seconds=4), NS(start_seconds=4.5, end_seconds=10)]
    narration = NS(metadata=raw, expected_frame_count=480000, offsets=offsets)
    blocks = [NS(id=uuid4(), ordem=index) for index in range(2)]
    workflow = NS(
        id=uuid4(),
        blocos=blocks,
        roteiro_snapshot={"content_locale": "pt-BR"},
        audio_edit_revision=1,
    )
    update = NS(
        id=uuid4(),
        workflow_execucao_id=workflow.id,
        target_revision=1,
        status="running",
        edit_snapshot={"version": "audio_edit_snapshot_v1", "blocks": [{}, {}]},
    )
    session = NS(
        execute=AsyncMock(
            side_effect=[
                NS(scalar_one=lambda: workflow),
                NS(scalar_one=lambda: update),
                NS(scalars=lambda: blocks),
            ]
        ),
        commit=AsyncMock(),
    )
    service = edits.AudioEditMaterializationService(
        session, config=Settings(_env_file=None, audio_storage_path=str(tmp_path))
    )
    service.repo = NS(obter_por_id=AsyncMock(return_value=workflow))
    service._rebuild_block = AsyncMock(
        side_effect=[NS(block_id=b.id, path=tmp_path / str(b.id)) for b in blocks]
    )
    publish = AsyncMock()
    monkeypatch.setattr(service, "_publish_database_state", publish)
    monkeypatch.setattr(edits, "assemble_narration", lambda *a, **kw: narration)
    tempo = {"mastered_narration_duration_seconds": 9, "tempo_factor": 1.15}
    monkeypatch.setattr(edits, "process_narration_tempo_candidate", lambda *a, **kw: tempo)
    monkeypatch.setattr(
        edits, "master_narration", lambda source, *a: NS(metadata=final, to_dict=lambda: {})
    )
    monkeypatch.setattr(edits, "inspect_wav", lambda *a: final)
    await service._materialize(update)
    args = publish.call_args.kwargs
    assert args["time_scale"] == 0.9
    assert args["tempo"] is tempo
    await edits.AudioEditMaterializationService._publish_database_state(
        service,
        update,
        workflow,
        {},
        narration,
        Path("final.wav"),
        final,
        **args,
    )
    assert [(b.narration_start_seconds, b.narration_end_seconds) for b in blocks] == [
        (0, 3.6),
        (4.05, 9),
    ]
    assert workflow.audio_integrity["expected_frame_count"] == final.frame_count
    assert workflow.narration_diagnostics["assembly"]["expected_frame_count"] == raw.frame_count
    assert workflow.narration_diagnostics["tempo"] == tempo
