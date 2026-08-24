from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from app.services.mastering import MasteringConfig, MasteringError, master_narration
from app.services.production_audio import inspect_wav


def test_mastering_constant_gain_is_atomic_deterministic_and_preserves_duration(
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


def test_true_peak_constraint_wins_over_loudness(tmp_path: Path, write_tone) -> None:
    source = write_tone(tmp_path / "hot.wav", sample_rate=48_000, duration=2.0, amplitude=0.9)
    result = master_narration(
        source,
        tmp_path / "final.wav",
        MasteringConfig(target_lufs=-3.0, max_acceptable_lufs=0.0),
    )
    assert result.peak_constrained is True
    assert result.status == "warning"
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
        patch("app.services.mastering.measure_true_peak", side_effect=MasteringError("failed")),
        pytest.raises(MasteringError),
    ):
        master_narration(source, destination, MasteringConfig())
    assert not destination.exists()
