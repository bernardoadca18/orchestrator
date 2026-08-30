from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from app.services.mastering import MasteringError
from app.services.narration_postprocessing import (
    CONSERVATIVE,
    process_narration_stem,
)
from app.services.production_audio import inspect_wav


def _voice_like(path: Path) -> Path:
    sample_rate = 48_000
    time = np.arange(sample_rate * 6, dtype=np.float64) / sample_rate
    envelope = 0.35 + 0.65 * np.square(np.sin(2 * np.pi * 0.7 * time))
    samples = envelope * (
        0.16 * np.sin(2 * np.pi * 180 * time)
        + 0.04 * np.sin(2 * np.pi * 360 * time)
    )
    sf.write(path, samples, sample_rate, format="WAV", subtype="PCM_16")
    return path


def test_postprocessing_is_deterministic_preserves_raw_and_duration(tmp_path: Path) -> None:
    source = _voice_like(tmp_path / "raw.wav")
    raw_before = inspect_wav(source)
    first = process_narration_stem(source, tmp_path / "first.wav", CONSERVATIVE)
    second = process_narration_stem(source, tmp_path / "second.wav", CONSERVATIVE)
    raw_after = inspect_wav(source)
    output = inspect_wav(tmp_path / "first.wav")

    assert raw_after.sha256 == raw_before.sha256
    assert first["output_sha256"] == second["output_sha256"]
    assert first["identity"] == second["identity"]
    assert output.duration_seconds == pytest.approx(raw_before.duration_seconds, abs=0.02)
    assert output.sample_rate == 48_000
    assert output.channels == 1
    assert output.clipping_sample_count == 0
    assert first["after"]["true_peak_dbtp"] <= CONSERVATIVE.true_peak_dbtp + 0.1
    assert first["after"]["integrated_lufs"] == pytest.approx(
        CONSERVATIVE.target_lufs, abs=0.5
    )


def test_failed_postprocessing_preserves_raw_and_existing_destination(tmp_path: Path) -> None:
    source = _voice_like(tmp_path / "raw.wav")
    raw_sha = inspect_wav(source).sha256
    destination = tmp_path / "master.wav"
    destination.write_bytes(b"existing")
    with (
        patch(
            "app.services.narration_postprocessing._loudnorm_analysis",
            side_effect=MasteringError("analysis failed"),
        ),
        pytest.raises(MasteringError),
    ):
        process_narration_stem(source, destination, CONSERVATIVE)
    assert inspect_wav(source).sha256 == raw_sha
    assert destination.read_bytes() == b"existing"
