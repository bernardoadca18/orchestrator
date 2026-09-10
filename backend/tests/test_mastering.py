from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from app.services.mastering import (
    MasteringConfig,
    MasteringError,
    master_narration,
    mastering_config_identity,
)
from app.services.production_audio import inspect_wav


def test_mastering_v3_is_atomic_deterministic_and_preserves_duration(
    tmp_path: Path, write_tone
) -> None:
    source = write_tone(
        tmp_path / "assembled.wav", sample_rate=48_000, duration=2.0, amplitude=0.03
    )
    destination = tmp_path / "final.wav"
    result = master_narration(source, destination, MasteringConfig())
    assert result.applied_gain_db > 0
    assert result.after.true_peak_dbtp <= -0.98
    assert result.metadata.sample_rate == 48_000
    assert result.metadata.channels == 1
    assert result.metadata.subtype == "PCM_16"
    assert result.metadata.duration_seconds == pytest.approx(
        inspect_wav(source).duration_seconds, abs=0.01
    )
    second = master_narration(source, tmp_path / "second.wav", MasteringConfig())
    assert second.metadata.sha256 == result.metadata.sha256
    assert second.processing_identity == result.processing_identity
    assert result.output_sha256 == result.metadata.sha256
    assert result.input_sha256 == inspect_wav(source).sha256


def test_dynamics_recovers_loudness_without_exceeding_true_peak(tmp_path: Path, write_tone) -> None:
    source = write_tone(tmp_path / "hot.wav", sample_rate=48_000, duration=3.0, amplitude=0.9)
    result = master_narration(
        source,
        tmp_path / "final.wav",
        MasteringConfig(target_lufs=-10.0, min_acceptable_lufs=-11.0, max_acceptable_lufs=-9.0),
    )
    assert result.dynamics_applied is True
    assert result.status == "pass"
    assert result.after.integrated_lufs == pytest.approx(-10.0, abs=0.5)
    assert result.after.true_peak_dbtp <= -0.98


def test_silent_audio_is_rejected_without_overwriting(tmp_path: Path) -> None:
    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros(96_000), 48_000, subtype="PCM_16")
    destination = tmp_path / "final.wav"
    destination.write_bytes(b"existing")
    with pytest.raises(MasteringError):
        master_narration(silent, destination, MasteringConfig())
    assert destination.read_bytes() == b"existing"


def test_measurement_failure_does_not_publish(tmp_path: Path, write_tone) -> None:
    source = write_tone(tmp_path / "source.wav", sample_rate=48_000, duration=2.0)
    destination = tmp_path / "final.wav"
    with (
        patch(
            "app.services.mastering.measure_loudness_and_true_peak",
            side_effect=MasteringError("failed"),
        ),
        pytest.raises(MasteringError),
    ):
        master_narration(source, destination, MasteringConfig())
    assert not destination.exists()


def test_mastering_v3_reduces_crest_and_reaches_policy(tmp_path: Path) -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate * 5, dtype=np.float64) / sample_rate
    envelope = 0.15 + 0.85 * np.square(np.sin(2 * np.pi * 0.8 * time))
    samples = envelope * 0.8 * np.sin(2 * np.pi * 220 * time)
    samples[sample_rate * 2 : sample_rate * 2 + sample_rate // 2] = 0.0
    source = tmp_path / "dynamic.wav"
    sf.write(source, samples, sample_rate, format="WAV", subtype="PCM_16")
    destination = tmp_path / "dynamic-mastered.wav"
    result = master_narration(source, destination, MasteringConfig())
    assert result.after.integrated_lufs == pytest.approx(-16.0, abs=0.5)
    assert result.after.true_peak_dbtp <= -0.98
    assert result.after.sample_peak_dbfs < 0
    assert result.metadata.clipping_sample_count == 0
    assert result.metadata.frame_count == inspect_wav(source).frame_count
    assert result.metadata.sample_rate == sample_rate
    mastered, _ = sf.read(destination, dtype="float32")
    silent_region = mastered[sample_rate * 2 + sample_rate // 4 : int(sample_rate * 2.5)]
    assert np.max(np.abs(silent_region)) == 0


def test_mastering_streams_without_full_file_soundfile_read(tmp_path: Path, write_tone) -> None:
    source = write_tone(tmp_path / "source.wav", duration=2.0, amplitude=0.04)
    with patch("app.services.mastering.sf.read", side_effect=AssertionError("full decode")):
        result = master_narration(source, tmp_path / "mastered.wav", MasteringConfig())
    assert result.metadata.frame_count == inspect_wav(source).frame_count


def test_acceptable_signal_is_bypassed_without_aggressive_processing(
    tmp_path: Path, write_tone
) -> None:
    source = write_tone(tmp_path / "source.wav", duration=3.0, amplitude=0.16)
    before_lufs = master_narration(
        source,
        tmp_path / "calibrated.wav",
        MasteringConfig(target_lufs=-16.0),
    )
    calibrated = tmp_path / "calibrated.wav"
    result = master_narration(calibrated, tmp_path / "bypassed.wav", MasteringConfig())
    assert before_lufs.status == "pass"
    assert result.dynamics_applied is False
    assert result.input_sha256 == result.output_sha256


def test_config_identity_changes_with_dynamics_policy() -> None:
    assert mastering_config_identity(MasteringConfig()) == mastering_config_identity(
        MasteringConfig()
    )
    assert mastering_config_identity(MasteringConfig()) != mastering_config_identity(
        MasteringConfig(compressor_ratio=1.8)
    )
