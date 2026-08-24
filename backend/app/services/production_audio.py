"""Validated block storage and deterministic 48 kHz narration assembly."""

from __future__ import annotations

import hashlib
import math
import os
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


@dataclass(frozen=True)
class AudioFileMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int
    subtype: str
    byte_size: int
    sha256: str
    frame_count: int


@dataclass(frozen=True)
class BlockNarrationOffset:
    order: int
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class AssemblyResult:
    metadata: AudioFileMetadata
    offsets: tuple[BlockNarrationOffset, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_wav(path: Path) -> AudioFileMetadata:
    try:
        info = sf.info(str(path))
        samples, sample_rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("WAV is unreadable") from exc
    if info.format != "WAV" or samples.size == 0:
        raise ProductionAudioError("audio result is not a non-empty WAV")
    if sample_rate < 8_000 or sample_rate > 192_000 or info.channels < 1 or info.channels > 8:
        raise ProductionAudioError("WAV format metadata is outside safe bounds")
    if not np.all(np.isfinite(samples)):
        raise ProductionAudioError("WAV contains non-finite samples")
    duration = float(info.frames / info.samplerate)
    if not math.isfinite(duration) or duration <= 0:
        raise ProductionAudioError("WAV duration is invalid")
    return AudioFileMetadata(
        duration_seconds=duration,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        subtype=info.subtype,
        byte_size=path.stat().st_size,
        sha256=sha256_file(path),
        frame_count=int(info.frames),
    )


def publish_source_block(
    audio_bytes: bytes,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    expected_byte_size: int | None = None,
) -> AudioFileMetadata:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(audio_bytes)
        metadata = inspect_wav(temporary)
        if expected_sha256 and metadata.sha256 != expected_sha256:
            raise ProductionAudioError("Voice Service audio checksum mismatch")
        if expected_byte_size is not None and metadata.byte_size != expected_byte_size:
            raise ProductionAudioError("Voice Service audio byte size mismatch")
        os.replace(temporary, destination)
        return metadata
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
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return AssemblyResult(metadata=metadata, offsets=tuple(offsets))
