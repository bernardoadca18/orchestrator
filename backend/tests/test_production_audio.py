from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from app.services.production_audio import (
    BlockDurationQualityError,
    ProductionAudioError,
    assemble_narration,
    inspect_wav,
    inspect_wav_metadata,
    publish_source_block,
)


def test_publish_validates_and_atomically_preserves_source(tmp_path: Path, write_tone) -> None:
    original = write_tone(tmp_path / "input.wav")
    expected = inspect_wav(original)
    destination = tmp_path / "blocks" / "source.wav"
    result = publish_source_block(
        original.read_bytes(),
        destination,
        expected_sha256=expected.sha256,
        expected_byte_size=expected.byte_size,
        expected_duration_seconds=expected.duration_seconds,
        expected_sample_rate=expected.sample_rate,
        expected_channels=expected.channels,
        expected_subtype=expected.subtype,
        expected_frame_count=expected.frame_count,
        expected_riff_data_byte_size=expected.riff_data_byte_size,
    )
    assert result.sha256 == expected.sha256
    assert destination.read_bytes() == original.read_bytes()
    assert not list(destination.parent.glob("*.tmp"))


def test_corrupt_or_checksum_mismatched_source_is_never_published(
    tmp_path: Path, write_tone
) -> None:
    destination = tmp_path / "source.wav"
    with pytest.raises(ProductionAudioError):
        publish_source_block(b"not wav", destination)
    source = write_tone(tmp_path / "valid.wav")
    with pytest.raises(ProductionAudioError, match="checksum"):
        publish_source_block(source.read_bytes(), destination, expected_sha256="0" * 64)
    with pytest.raises(ProductionAudioError, match="frame count"):
        publish_source_block(
            source.read_bytes(),
            destination,
            expected_frame_count=inspect_wav(source).frame_count + 1,
        )
    assert not destination.exists()


def test_publish_rejects_grossly_implausible_block_duration(tmp_path: Path, write_tone) -> None:
    source = write_tone(tmp_path / "runaway.wav", duration=20.0)
    destination = tmp_path / "published.wav"
    with pytest.raises(BlockDurationQualityError, match="implausible"):
        publish_source_block(
            source.read_bytes(),
            destination,
            text_length_chars=10,
            max_duration_seconds=120,
            max_seconds_per_char=0.5,
            duration_grace_seconds=5,
        )
    assert not destination.exists()


def test_assembly_is_48k_mono_pcm16_ordered_with_exact_pause_and_offsets(
    tmp_path: Path, write_tone
) -> None:
    first = write_tone(tmp_path / "first.wav", sample_rate=24_000, duration=0.75, frequency=180)
    second = write_tone(
        tmp_path / "second.wav", sample_rate=32_000, duration=1.25, frequency=420, channels=2
    )
    result = assemble_narration(
        [first, second], tmp_path / "final.wav", sample_rate=48_000, silence_ms=500
    )
    assert result.metadata.sample_rate == 48_000
    assert result.metadata.channels == 1
    assert result.metadata.subtype == "PCM_16"
    assert result.metadata.duration_seconds == pytest.approx(2.5, abs=1 / 48_000)
    assert result.offsets[0].start_seconds == 0
    assert result.offsets[0].end_seconds == pytest.approx(0.75, abs=1 / 48_000)
    assert result.offsets[1].start_seconds == pytest.approx(1.25, abs=1 / 48_000)
    assert result.offsets[1].end_seconds == pytest.approx(2.5, abs=1 / 48_000)
    samples, _ = sf.read(tmp_path / "final.wav")
    assert np.max(np.abs(samples[36_000:60_000])) == 0


def test_assembly_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(ProductionAudioError):
        assemble_narration([], tmp_path / "none.wav", sample_rate=48_000, silence_ms=0)


def test_metadata_inspection_never_runs_signal_decode(tmp_path: Path, write_tone) -> None:
    source = write_tone(tmp_path / "source.wav")
    with patch(
        "app.services.production_audio.scan_wav_signal",
        side_effect=AssertionError("signal scan must not run"),
    ):
        metadata = inspect_wav_metadata(source)
    assert metadata.frame_count == 48_000


def test_streaming_assembly_does_not_concatenate_complete_narration(
    tmp_path: Path, write_tone
) -> None:
    source = write_tone(tmp_path / "short.wav", duration=0.02)
    with patch(
        "app.services.production_audio.np.concatenate",
        side_effect=AssertionError("complete narration must not be concatenated"),
    ):
        result = assemble_narration(
            [source] * 120,
            tmp_path / "long.wav",
            sample_rate=48_000,
            silence_ms=10,
        )
    assert result.metadata.frame_count == (120 * 960) + (119 * 480)


def test_assembly_failure_preserves_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "final.wav"
    destination.write_bytes(b"existing artifact")
    invalid = tmp_path / "invalid.wav"
    invalid.write_bytes(b"not a wav")
    with pytest.raises(ProductionAudioError):
        assemble_narration([invalid], destination, sample_rate=48_000, silence_ms=0)
    assert destination.read_bytes() == b"existing artifact"
    assert not list(tmp_path.glob(".*.tmp.wav"))
