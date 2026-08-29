from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app as orchestrator_app
from app.services.audio_diagnostics import analyze_audio
from app.services.audio_serving import (
    AudioArtifactExpectation,
    AudioServingIntegrityError,
    audio_file_response,
    verify_audio_artifact,
)
from app.services.production_audio import IO_BLOCK_FRAMES, ProductionAudioError, inspect_wav


def _expectation(path: Path) -> AudioArtifactExpectation:
    metadata = inspect_wav(path)
    return AudioArtifactExpectation(
        sha256=metadata.sha256,
        byte_size=metadata.byte_size,
        duration_seconds=metadata.duration_seconds,
        sample_rate=metadata.sample_rate,
        channels=metadata.channels,
        frame_count=metadata.frame_count,
        subtype=metadata.subtype,
    )


def test_full_head_and_range_delivery_match_physical_file(tmp_path: Path, write_tone) -> None:
    wav = write_tone(tmp_path / "audio.wav", duration=1.0)
    path, metadata = verify_audio_artifact(wav, _expectation(wav))
    test_app = FastAPI()

    @test_app.api_route("/audio", methods=["GET", "HEAD"])
    async def serve():
        return audio_file_response(path, metadata, filename="audio.wav")

    with TestClient(test_app) as client:
        full = client.get("/audio")
        head = client.head("/audio")
        start = client.get("/audio", headers={"Range": "bytes=0-31"})
        middle = client.get("/audio", headers={"Range": "bytes=100-199"})
        tail = client.get("/audio", headers={"Range": "bytes=-32"})
        resumed = client.get("/audio", headers={"Range": "bytes=32-63"})
        invalid = client.get("/audio", headers={"Range": f"bytes={metadata.byte_size}-"})

    assert full.status_code == 200
    assert full.content == wav.read_bytes()
    assert int(full.headers["content-length"]) == metadata.byte_size
    assert full.headers["content-type"].startswith("audio/wav")
    assert full.headers["etag"] == f'"{metadata.sha256}"'
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == metadata.byte_size
    assert start.status_code == middle.status_code == tail.status_code == resumed.status_code == 206
    assert start.headers["content-range"] == f"bytes 0-31/{metadata.byte_size}"
    assert middle.headers["content-range"] == f"bytes 100-199/{metadata.byte_size}"
    assert tail.headers["content-range"] == (
        f"bytes {metadata.byte_size - 32}-{metadata.byte_size - 1}/{metadata.byte_size}"
    )
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == f"*/{metadata.byte_size}"


def test_wrong_persisted_metadata_and_truncated_riff_are_rejected(
    tmp_path: Path, write_tone
) -> None:
    wav = write_tone(tmp_path / "audio.wav")
    expected = _expectation(wav)
    with pytest.raises(AudioServingIntegrityError, match="checksum"):
        verify_audio_artifact(
            wav,
            AudioArtifactExpectation(
                sha256="0" * 64,
                byte_size=expected.byte_size,
                duration_seconds=expected.duration_seconds,
                sample_rate=expected.sample_rate,
                channels=expected.channels,
            ),
        )
    with pytest.raises(AudioServingIntegrityError, match="duration"):
        verify_audio_artifact(
            wav,
            AudioArtifactExpectation(
                sha256=expected.sha256,
                byte_size=expected.byte_size,
                duration_seconds=expected.duration_seconds + 0.1,
                sample_rate=expected.sample_rate,
                channels=expected.channels,
            ),
        )
    with pytest.raises(AudioServingIntegrityError, match="frame count"):
        verify_audio_artifact(
            wav,
            AudioArtifactExpectation(
                sha256=expected.sha256,
                byte_size=expected.byte_size,
                duration_seconds=expected.duration_seconds,
                sample_rate=expected.sample_rate,
                channels=expected.channels,
                frame_count=expected.frame_count + 1,
            ),
        )
    truncated = tmp_path / "truncated.wav"
    truncated.write_bytes(wav.read_bytes()[:-10])
    with pytest.raises(ProductionAudioError, match="RIFF"):
        inspect_wav(truncated)


def test_pause_and_transient_diagnostics_are_non_destructive(tmp_path: Path, write_tone) -> None:
    wav = write_tone(tmp_path / "audio.wav", duration=1.0)
    original = wav.read_bytes()
    report = analyze_audio(wav)
    assert report["artifact"]["sha256"] == inspect_wav(wav).sha256
    assert "transient_candidates" in report
    assert "pause_timeline" in report
    assert wav.read_bytes() == original


def test_pause_diagnostics_attribute_configured_boundary_separately(tmp_path: Path) -> None:
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    tone = 0.1 * np.sin(2 * np.pi * 220 * time)
    samples = np.concatenate([tone, np.zeros(sample_rate // 2), tone])
    wav = tmp_path / "pause.wav"
    sf.write(wav, samples, sample_rate, format="WAV", subtype="PCM_16")
    report = analyze_audio(
        wav,
        inserted_pauses=[
            {
                "start_seconds": 1.0,
                "end_seconds": 1.5,
                "source": "orchestrator_block_boundary_pause",
                "reason": "test boundary",
                "boundary_type": "editorial_block",
            }
        ],
    )
    boundary = next(
        item
        for item in report["pause_timeline"]
        if item["source"] == "orchestrator_block_boundary_pause"
    )
    assert boundary["duration_seconds"] == pytest.approx(0.5, abs=2 / sample_rate)


def test_internal_pause_and_low_energy_transient_are_observable(tmp_path: Path) -> None:
    sample_rate = 24_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    tone = 0.02 * np.sin(2 * np.pi * 220 * time)
    samples = np.concatenate([tone, np.zeros(sample_rate // 2), tone])
    samples[sample_rate + sample_rate // 4] = 0.2
    wav = tmp_path / "internal-pause-click.wav"
    sf.write(wav, samples, sample_rate, format="WAV", subtype="PCM_16")

    report = analyze_audio(wav)
    internal = [
        item
        for item in report["pause_timeline"]
        if item["source"] == "model_internal_or_source_silence"
    ]
    assert internal and internal[0]["duration_seconds"] >= 0.24
    assert any(item["low_energy_context"] for item in report["transient_candidates"])


def test_streaming_diagnostics_preserve_silence_across_buffer_boundary(tmp_path: Path) -> None:
    sample_rate = 8_000
    total_frames = IO_BLOCK_FRAMES + sample_rate
    samples = np.full(total_frames, 0.05, dtype=np.float64)
    silence_start = IO_BLOCK_FRAMES - sample_rate // 2
    silence_end = IO_BLOCK_FRAMES + sample_rate // 2
    samples[silence_start:silence_end] = 0.0
    wav = tmp_path / "boundary-silence.wav"
    sf.write(wav, samples, sample_rate, format="WAV", subtype="PCM_16")

    report = analyze_audio(wav)

    assert report["signal"]["silence_run_count"] == 1
    assert report["signal"]["silence_total_seconds"] == pytest.approx(1.0, abs=1 / sample_rate)
    assert report["pause_timeline"][0]["start_seconds"] == pytest.approx(
        silence_start / sample_rate,
        abs=1 / sample_rate,
    )


def test_streaming_diagnostics_match_legacy_signal_metrics(tmp_path: Path) -> None:
    sample_rate = 24_000
    time = np.arange(sample_rate * 3, dtype=np.float64) / sample_rate
    samples = 0.03 * np.sin(2 * np.pi * 233 * time) + 0.004
    samples[: sample_rate // 4] = 0
    samples[sample_rate : sample_rate + sample_rate // 2] = 0
    wav = tmp_path / "representative.wav"
    sf.write(wav, samples, sample_rate, format="WAV", subtype="PCM_16")
    decoded, _ = sf.read(wav, dtype="float64")
    threshold = 10.0 ** (-45.0 / 20.0)
    legacy_rms = 20.0 * np.log10(np.sqrt(np.mean(np.square(decoded))))
    window_frames = round(sample_rate * 0.02)
    windows = decoded[: len(decoded) - (len(decoded) % window_frames)].reshape(
        -1, window_frames
    )
    legacy_noise_amplitude = float(
        np.percentile(np.sqrt(np.mean(np.square(windows), axis=1)), 10)
    )
    legacy_noise = (
        20.0 * np.log10(legacy_noise_amplitude) if legacy_noise_amplitude > 0 else -120.0
    )

    report = analyze_audio(wav)
    signal = report["signal"]

    assert signal["dc_offset"] == pytest.approx(float(np.mean(decoded)), abs=1e-9)
    assert signal["rms_dbfs"] == pytest.approx(float(legacy_rms), abs=0.001)
    assert signal["noise_floor_estimate_dbfs"] == pytest.approx(float(legacy_noise), abs=0.11)
    assert signal["near_silent_sample_ratio"] == pytest.approx(
        float(np.mean(np.abs(decoded) <= threshold)),
        abs=1e-6,
    )


def test_block_endpoint_rejects_stale_current_take_pointer(tmp_path: Path, write_tone) -> None:
    wav = write_tone(tmp_path / "audio.wav")
    metadata = inspect_wav(wav)
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    take_id = uuid.uuid4()
    block = SimpleNamespace(
        id=block_id,
        workflow_execucao_id=workflow_id,
        ordem=0,
        status="concluido",
        audio_path=str(wav),
        audio_sha256=metadata.sha256,
        audio_byte_size=metadata.byte_size,
        duracao_segundos=metadata.duration_seconds,
        audio_sample_rate=metadata.sample_rate,
        audio_channels=metadata.channels,
        audio_frame_count=metadata.frame_count,
        audio_subtype=metadata.subtype,
        current_take_id=take_id,
    )
    stale_take = SimpleNamespace(
        id=take_id,
        status="completed",
        is_current=True,
        audio_path=str(tmp_path / "old.wav"),
        audio_sha256="0" * 64,
        audio_byte_size=1,
    )
    repository = MagicMock()
    repository.obter_bloco_por_id = AsyncMock(return_value=block)
    repository.obter_take = AsyncMock(return_value=stale_take)

    async def fake_db():
        yield MagicMock()

    orchestrator_app.dependency_overrides[get_db] = fake_db
    try:
        with (
            patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
            TestClient(orchestrator_app) as client,
        ):
            response = client.get(f"/workflow/{workflow_id}/blocos/{block_id}/audio")
    finally:
        orchestrator_app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "take ativo" in response.json()["detail"]
