"""Validated block storage and deterministic 48 kHz narration assembly."""

from __future__ import annotations

import hashlib
import math
import os
import struct
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy.signal import resample_poly


class ProductionAudioError(RuntimeError):
    """Audio is invalid or cannot be published safely."""


class BlockDurationQualityError(ProductionAudioError):
    """A technically valid source WAV is grossly implausible for its text."""


@dataclass(frozen=True)
class AudioFileMetadata:
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


def inspect_wav(path: Path) -> AudioFileMetadata:
    riff = _inspect_riff(path)
    try:
        info = sf.info(str(path))
        samples, sample_rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("WAV is unreadable") from exc
    if info.format != "WAV" or info.subtype != "PCM_16" or samples.size == 0:
        raise ProductionAudioError("audio result is not a non-empty PCM16 WAV")
    if sample_rate < 8_000 or sample_rate > 192_000 or info.channels < 1 or info.channels > 8:
        raise ProductionAudioError("WAV format metadata is outside safe bounds")
    if not np.all(np.isfinite(samples)):
        raise ProductionAudioError("WAV contains non-finite samples")
    duration = float(info.frames / info.samplerate)
    if not math.isfinite(duration) or duration <= 0:
        raise ProductionAudioError("WAV duration is invalid")
    if int(info.frames) * riff.block_align != riff.data_byte_size:
        raise ProductionAudioError("WAV sample count does not match its RIFF data size")
    absolute = np.abs(samples)
    peak = float(np.max(absolute))
    clipping_count = int(np.count_nonzero(absolute >= 0.999))
    peak_dbfs = 20.0 * math.log10(peak) if peak > 0 else float("-inf")
    return AudioFileMetadata(
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
        sample_peak_dbfs=peak_dbfs,
        clipping_sample_count=clipping_count,
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
    pieces: list[NDArray[np.float64]] = []
    offsets: list[BlockNarrationOffset] = []
    current_frame = 0
    silence_frames = round(max(silence_ms, 0) * sample_rate / 1000)
    for order, path in enumerate(block_paths):
        try:
            samples, source_rate = sf.read(str(path), dtype="float64", always_2d=True)
        except (OSError, RuntimeError) as exc:
            raise ProductionAudioError("source block WAV is unreadable") from exc
        if samples.size == 0 or not np.all(np.isfinite(samples)):
            raise ProductionAudioError("source block WAV contains invalid samples")
        mono = np.mean(samples, axis=1)
        if source_rate != sample_rate:
            divisor = gcd(source_rate, sample_rate)
            mono = resample_poly(mono, sample_rate // divisor, source_rate // divisor)
        mono = np.asarray(mono, dtype=np.float64)
        start_frame = current_frame
        pieces.append(mono)
        current_frame += len(mono)
        offsets.append(
            BlockNarrationOffset(
                order=order,
                start_seconds=start_frame / sample_rate,
                end_seconds=current_frame / sample_rate,
            )
        )
        if silence_frames and order < len(block_paths) - 1:
            pieces.append(np.zeros(silence_frames, dtype=np.float64))
            current_frame += silence_frames
    assembled = np.concatenate(pieces)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp.wav"
    try:
        sf.write(str(temporary), assembled, sample_rate, format="WAV", subtype="PCM_16")
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
