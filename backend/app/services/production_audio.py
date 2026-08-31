"""Validated block storage and deterministic 48 kHz narration assembly."""

from __future__ import annotations

import hashlib
import math
import os
import struct
from dataclasses import asdict, dataclass
from math import gcd
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]
from scipy.signal import resample_poly  # type: ignore[import-untyped]


class ProductionAudioError(RuntimeError):
    """Audio is invalid or cannot be published safely."""


class BlockDurationQualityError(ProductionAudioError):
    """A technically valid source WAV is grossly implausible for its text."""


IO_BLOCK_FRAMES = 262_144


@dataclass(frozen=True)
class WavPhysicalMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int
    subtype: str
    byte_size: int
    sha256: str
    frame_count: int
    format: str
    riff_declared_byte_size: int
    riff_data_byte_size: int


@dataclass(frozen=True)
class WavSignalMetrics:
    sample_peak_dbfs: float
    clipping_sample_count: int


@dataclass(frozen=True)
class AudioFileMetadata(WavPhysicalMetadata):
    sample_peak_dbfs: float
    clipping_sample_count: int


@dataclass(frozen=True)
class BlockNarrationOffset:
    order: int
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class AssemblyResult:
    metadata: AudioFileMetadata
    offsets: tuple[BlockNarrationOffset, ...]
    expected_frame_count: int


@dataclass(frozen=True)
class _RiffMetadata:
    declared_byte_size: int
    data_byte_size: int
    block_align: int


def _inspect_riff(path: Path) -> _RiffMetadata:
    """Strictly validate the RIFF envelope instead of trusting a decoder alone."""
    file_size = path.stat().st_size
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                raise ProductionAudioError("audio result is not a standard RIFF/WAVE file")
            declared_size = struct.unpack_from("<I", header, 4)[0] + 8
            if declared_size != file_size:
                raise ProductionAudioError("RIFF declared size does not match physical file size")

            fmt: tuple[int, int, int] | None = None
            format_chunk_count = 0
            data_sizes: list[int] = []
            position = 12
            while position < file_size:
                chunk_header = stream.read(8)
                if len(chunk_header) != 8:
                    raise ProductionAudioError("RIFF chunk header is truncated")
                chunk_id = chunk_header[:4]
                chunk_size = struct.unpack_from("<I", chunk_header, 4)[0]
                position += 8
                padded_size = chunk_size + (chunk_size % 2)
                if position + padded_size > file_size:
                    raise ProductionAudioError("RIFF chunk exceeds the physical file size")
                if chunk_id == b"fmt ":
                    format_chunk_count += 1
                    payload = stream.read(chunk_size)
                    if len(payload) < 16:
                        raise ProductionAudioError("WAV format chunk is truncated")
                    audio_format, channels, _, _, block_align, bits = struct.unpack_from(
                        "<HHIIHH", payload
                    )
                    if audio_format not in {1, 0xFFFE}:
                        raise ProductionAudioError("WAV must use PCM encoding")
                    if channels <= 0 or block_align <= 0 or bits <= 0:
                        raise ProductionAudioError("WAV format chunk is invalid")
                    fmt = (channels, block_align, bits)
                    if padded_size > chunk_size:
                        stream.seek(padded_size - chunk_size, os.SEEK_CUR)
                else:
                    if chunk_id == b"data":
                        data_sizes.append(chunk_size)
                    stream.seek(padded_size, os.SEEK_CUR)
                position += padded_size
            if (
                position != file_size
                or fmt is None
                or format_chunk_count != 1
                or len(data_sizes) != 1
            ):
                raise ProductionAudioError("WAV must contain one complete format and data chunk")
    except OSError as exc:
        raise ProductionAudioError("WAV container is unreadable") from exc
    return _RiffMetadata(
        declared_byte_size=declared_size,
        data_byte_size=data_sizes[0],
        block_align=fmt[1],
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_wav_metadata(path: Path) -> WavPhysicalMetadata:
    """Validate RIFF/PCM metadata and hash a WAV without decoding its signal."""

    riff = _inspect_riff(path)
    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("WAV is unreadable") from exc
    if info.format != "WAV" or info.subtype != "PCM_16" or info.frames <= 0:
        raise ProductionAudioError("audio result is not a non-empty PCM16 WAV")
    if (
        info.samplerate < 8_000
        or info.samplerate > 192_000
        or info.channels < 1
        or info.channels > 8
    ):
        raise ProductionAudioError("WAV format metadata is outside safe bounds")
    duration = float(info.frames / info.samplerate)
    if not math.isfinite(duration) or duration <= 0:
        raise ProductionAudioError("WAV duration is invalid")
    if int(info.frames) * riff.block_align != riff.data_byte_size:
        raise ProductionAudioError("WAV sample count does not match its RIFF data size")
    return WavPhysicalMetadata(
        duration_seconds=duration,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        subtype=info.subtype,
        byte_size=path.stat().st_size,
        sha256=sha256_file(path),
        frame_count=int(info.frames),
        format=info.format,
        riff_declared_byte_size=riff.declared_byte_size,
        riff_data_byte_size=riff.data_byte_size,
    )


def scan_wav_signal(path: Path, *, block_frames: int = IO_BLOCK_FRAMES) -> WavSignalMetrics:
    """Scan signal-level integrity with fixed-size decode buffers."""

    if block_frames <= 0:
        raise ValueError("block_frames must be positive")
    peak = 0.0
    clipping_count = 0
    sample_count = 0
    try:
        with sf.SoundFile(str(path), mode="r") as source:
            for samples in source.blocks(
                blocksize=block_frames,
                dtype="float64",
                always_2d=True,
            ):
                if not np.all(np.isfinite(samples)):
                    raise ProductionAudioError("WAV contains non-finite samples")
                absolute = np.abs(samples)
                if absolute.size:
                    peak = max(peak, float(np.max(absolute)))
                    clipping_count += int(np.count_nonzero(absolute >= 0.999))
                    sample_count += int(absolute.size)
    except ProductionAudioError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("WAV signal could not be decoded") from exc
    if sample_count == 0:
        raise ProductionAudioError("audio result contains no decodable samples")
    return WavSignalMetrics(
        sample_peak_dbfs=(20.0 * math.log10(peak) if peak > 0 else float("-inf")),
        clipping_sample_count=clipping_count,
    )


def inspect_wav(path: Path) -> AudioFileMetadata:
    """Compatibility inspection combining physical metadata and a bounded signal scan."""

    physical = inspect_wav_metadata(path)
    signal = scan_wav_signal(path)
    return AudioFileMetadata(
        **asdict(physical),
        sample_peak_dbfs=signal.sample_peak_dbfs,
        clipping_sample_count=signal.clipping_sample_count,
    )


def publish_source_block(
    audio_bytes: bytes,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_byte_size: int | None = None,
    expected_duration_seconds: float | None = None,
    expected_sample_rate: int | None = None,
    expected_channels: int | None = None,
    expected_subtype: str | None = None,
    expected_frame_count: int | None = None,
    expected_riff_data_byte_size: int | None = None,
    text_length_chars: int | None = None,
    max_duration_seconds: float | None = None,
    max_seconds_per_char: float | None = None,
    duration_grace_seconds: float = 0.0,
) -> AudioFileMetadata:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(audio_bytes)
        metadata = inspect_wav(temporary)
        if (
            text_length_chars is not None
            and max_duration_seconds is not None
            and max_seconds_per_char is not None
        ):
            relative_limit = duration_grace_seconds + (
                max_seconds_per_char * max(text_length_chars, 1)
            )
            if (
                metadata.duration_seconds > max_duration_seconds
                or metadata.duration_seconds > relative_limit
            ):
                raise BlockDurationQualityError(
                    "Voice Service audio duration is implausible for the synthesis text"
                )
        if expected_sha256 and metadata.sha256 != expected_sha256:
            raise ProductionAudioError("Voice Service audio checksum mismatch")
        if expected_byte_size is not None and metadata.byte_size != expected_byte_size:
            raise ProductionAudioError("Voice Service audio byte size mismatch")
        if expected_duration_seconds is not None and not math.isclose(
            metadata.duration_seconds,
            expected_duration_seconds,
            rel_tol=0.0,
            abs_tol=1.0 / metadata.sample_rate,
        ):
            raise ProductionAudioError("Voice Service audio duration mismatch")
        expected_physical = {
            "sample rate": (expected_sample_rate, metadata.sample_rate),
            "channels": (expected_channels, metadata.channels),
            "subtype": (expected_subtype, metadata.subtype),
            "frame count": (expected_frame_count, metadata.frame_count),
            "RIFF data size": (
                expected_riff_data_byte_size,
                metadata.riff_data_byte_size,
            ),
        }
        for label, (expected, physical) in expected_physical.items():
            if expected is not None and expected != physical:
                raise ProductionAudioError(f"Voice Service audio {label} mismatch")
        os.replace(temporary, destination)
        published = inspect_wav(destination)
        if published != metadata:
            destination.unlink(missing_ok=True)
            raise ProductionAudioError("published source WAV changed during atomic publication")
        return published
    finally:
        temporary.unlink(missing_ok=True)


def assemble_narration(
    block_paths: list[Path],
    destination: Path,
    *,
    sample_rate: int,
    silence_ms: int,
) -> AssemblyResult:
    if not block_paths:
        raise ProductionAudioError("cannot assemble an empty narration")
    destination.parent.mkdir(parents=True, exist_ok=True)
    offsets: list[BlockNarrationOffset] = []
    current_frame = 0
    silence_frames = round(max(silence_ms, 0) * sample_rate / 1000)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp.wav"
    try:
        with sf.SoundFile(
            str(temporary),
            mode="w",
            samplerate=sample_rate,
            channels=1,
            format="WAV",
            subtype="PCM_16",
        ) as output:
            for order, path in enumerate(block_paths):
                start_frame = current_frame
                try:
                    with sf.SoundFile(str(path), mode="r") as source:
                        source_rate = int(source.samplerate)
                        if source.frames <= 0 or source.channels <= 0 or source_rate <= 0:
                            raise ProductionAudioError("source block WAV contains invalid samples")
                        if source_rate == sample_rate:
                            block_frames = 0
                            for samples in source.blocks(
                                blocksize=IO_BLOCK_FRAMES,
                                dtype="float64",
                                always_2d=True,
                            ):
                                if not np.all(np.isfinite(samples)):
                                    raise ProductionAudioError(
                                        "source block WAV contains invalid samples"
                                    )
                                mono = np.mean(samples, axis=1, dtype=np.float64)
                                output.write(mono)
                                block_frames += len(mono)
                        else:
                            # Qwen blocks are quality-bounded. Keeping one mismatched-rate
                            # block resident preserves scipy resample_poly's exact boundary
                            # semantics without ever retaining the complete narration.
                            samples = source.read(dtype="float64", always_2d=True)
                            if samples.size == 0 or not np.all(np.isfinite(samples)):
                                raise ProductionAudioError(
                                    "source block WAV contains invalid samples"
                                )
                            mono = np.mean(samples, axis=1, dtype=np.float64)
                            divisor = gcd(source_rate, sample_rate)
                            resampled = resample_poly(
                                mono,
                                sample_rate // divisor,
                                source_rate // divisor,
                            )
                            output.write(resampled)
                            block_frames = len(resampled)
                except ProductionAudioError:
                    raise
                except (OSError, RuntimeError) as exc:
                    raise ProductionAudioError("source block WAV is unreadable") from exc
                if block_frames <= 0:
                    raise ProductionAudioError("source block WAV contains invalid samples")
                current_frame += block_frames
                offsets.append(
                    BlockNarrationOffset(
                        order=order,
                        start_seconds=start_frame / sample_rate,
                        end_seconds=current_frame / sample_rate,
                    )
                )
                if silence_frames and order < len(block_paths) - 1:
                    remaining = silence_frames
                    silence = np.zeros(min(IO_BLOCK_FRAMES, silence_frames), dtype=np.float64)
                    while remaining:
                        frames = min(remaining, len(silence))
                        output.write(silence[:frames])
                        remaining -= frames
                    current_frame += silence_frames
        metadata = inspect_wav(temporary)
        if (
            metadata.sample_rate != sample_rate
            or metadata.channels != 1
            or metadata.subtype != "PCM_16"
        ):
            raise ProductionAudioError("assembled narration format is not canonical")
        if metadata.frame_count != current_frame:
            raise ProductionAudioError(
                "assembled WAV sample count differs from the expected timeline"
            )
        os.replace(temporary, destination)
        published = inspect_wav(destination)
        if published != metadata:
            destination.unlink(missing_ok=True)
            raise ProductionAudioError("assembled WAV changed during atomic publication")
    finally:
        temporary.unlink(missing_ok=True)
    return AssemblyResult(
        metadata=published,
        offsets=tuple(offsets),
        expected_frame_count=current_frame,
    )
