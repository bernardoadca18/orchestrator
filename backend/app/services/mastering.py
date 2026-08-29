"""Transparent final narration mastering with real True Peak protection."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf

from app.services.production_audio import IO_BLOCK_FRAMES, AudioFileMetadata, inspect_wav


class MasteringError(RuntimeError):
    """The final narration cannot be published safely."""


@dataclass(frozen=True)
class LoudnessMetrics:
    integrated_lufs: float
    sample_peak_dbfs: float
    true_peak_dbtp: float


@dataclass(frozen=True)
class MasteringConfig:
    version: str = "transparent_gain_v2_streaming_ebur128"
    target_lufs: float = -16.0
    max_true_peak_dbtp: float = -1.0
    true_peak_tolerance_db: float = 0.02
    true_peak_safety_margin_db: float = 0.05
    min_acceptable_lufs: float = -18.0
    max_acceptable_lufs: float = -15.0
    duration_tolerance_seconds: float = 0.01


@dataclass(frozen=True)
class MasteringResult:
    before: LoudnessMetrics
    after: LoudnessMetrics
    applied_gain_db: float
    peak_constrained: bool
    status: str
    reason: str
    metadata: AudioFileMetadata

    def to_dict(self) -> dict[str, object]:
        return {
            "before": asdict(self.before),
            "after": asdict(self.after),
            "applied_gain_db": self.applied_gain_db,
            "peak_constrained": self.peak_constrained,
            "status": self.status,
            "reason": self.reason,
        }


def _amplitude_to_db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else float("-inf")


def measure_loudness_and_true_peak(path: Path) -> tuple[float, float]:
    """Measure EBU R128 integrated loudness and True Peak in one FFmpeg pass."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MasteringError("FFmpeg is required for loudness measurement")
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-filter:a",
        "ebur128=metadata=1:peak=true,ametadata=mode=print:file=-",
        "-f",
        "null",
        "-",
    ]
    try:
        process = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MasteringError("FFmpeg loudness measurement failed") from exc
    if process.returncode != 0:
        raise MasteringError("FFmpeg loudness measurement failed")
    loudness_prefix = "lavfi.r128.I="
    true_peak_prefix = "lavfi.r128.true_peak="
    try:
        loudness_values = [
            float(line.removeprefix(loudness_prefix))
            for line in process.stdout.splitlines()
            if line.startswith(loudness_prefix)
        ]
        true_peak_values = [
            float(line.removeprefix(true_peak_prefix))
            for line in process.stdout.splitlines()
            if line.startswith(true_peak_prefix)
        ]
    except ValueError as exc:
        raise MasteringError("FFmpeg returned invalid loudness metadata") from exc
    if not loudness_values or not math.isfinite(loudness_values[-1]):
        raise MasteringError("FFmpeg returned no valid integrated loudness measurement")
    if not true_peak_values or not all(
        math.isfinite(value) and value >= 0 for value in true_peak_values
    ):
        raise MasteringError("FFmpeg returned no valid True Peak measurement")
    integrated_lufs = loudness_values[-1]
    if integrated_lufs <= -70.0:
        raise MasteringError("Integrated LUFS is invalid for silent audio")
    return integrated_lufs, _amplitude_to_db(max(true_peak_values))


def measure_true_peak(path: Path) -> float:
    return measure_loudness_and_true_peak(path)[1]


def analyze(
    path: Path,
    *,
    sample_peak_dbfs: float | None = None,
) -> tuple[LoudnessMetrics, sf.SoundFile]:
    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError) as exc:
        raise MasteringError("narration WAV is unreadable") from exc
    if info.frames <= 0:
        raise MasteringError("narration WAV contains no samples")
    loudness, true_peak = measure_loudness_and_true_peak(path)
    peak = sample_peak_dbfs
    if peak is None:
        peak = inspect_wav(path).sample_peak_dbfs
    return LoudnessMetrics(loudness, peak, true_peak), info


def master_narration(source: Path, destination: Path, config: MasteringConfig) -> MasteringResult:
    source_metadata = inspect_wav(source)
    before, info = analyze(source, sample_peak_dbfs=source_metadata.sample_peak_dbfs)
    gain_for_loudness = config.target_lufs - before.integrated_lufs
    gain_allowed_by_peak = (
        config.max_true_peak_dbtp - config.true_peak_safety_margin_db - before.true_peak_dbtp
    )
    applied_gain = min(gain_for_loudness, gain_allowed_by_peak)
    peak_constrained = gain_allowed_by_peak < gain_for_loudness - 1e-9
    gain_factor = 10.0 ** (applied_gain / 20.0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp.wav"
    try:
        try:
            with (
                sf.SoundFile(str(source), mode="r") as input_audio,
                sf.SoundFile(
                    str(temporary),
                    mode="w",
                    samplerate=info.samplerate,
                    channels=info.channels,
                    format="WAV",
                    subtype="PCM_16",
                ) as output_audio,
            ):
                for samples in input_audio.blocks(
                    blocksize=IO_BLOCK_FRAMES,
                    dtype="float64",
                    always_2d=True,
                ):
                    if not np.all(np.isfinite(samples)):
                        raise MasteringError("narration WAV contains invalid samples")
                    np.multiply(samples, gain_factor, out=samples)
                    output_audio.write(samples)
        except MasteringError:
            raise
        except (OSError, RuntimeError) as exc:
            raise MasteringError("mastering gain pass failed") from exc
        metadata = inspect_wav(temporary)
        after, after_info = analyze(
            temporary,
            sample_peak_dbfs=metadata.sample_peak_dbfs,
        )
        if after_info.samplerate != info.samplerate or after_info.channels != info.channels:
            raise MasteringError("mastering changed sample rate or channels")
        if (
            abs(metadata.duration_seconds - source_metadata.duration_seconds)
            > config.duration_tolerance_seconds
        ):
            raise MasteringError("mastering changed narration duration")
        if metadata.frame_count != source_metadata.frame_count:
            raise MasteringError("mastering changed narration sample count")
        if after.sample_peak_dbfs >= 0:
            raise MasteringError("mastered narration contains digital clipping")
        if after.true_peak_dbtp > config.max_true_peak_dbtp + config.true_peak_tolerance_db:
            raise MasteringError("mastered narration exceeds the True Peak ceiling")
        if after.integrated_lufs > config.max_acceptable_lufs:
            raise MasteringError("mastered narration is louder than the accepted range")
        if peak_constrained:
            status = "warning"
            reason = "True Peak headroom constrained the loudness target"
        elif after.integrated_lufs < config.min_acceptable_lufs:
            raise MasteringError("mastered narration is quieter than the accepted range")
        else:
            status = "pass"
            reason = "loudness and True Peak are within policy"
        os.replace(temporary, destination)
        published = inspect_wav(destination)
        if published != metadata:
            destination.unlink(missing_ok=True)
            raise MasteringError("mastered WAV changed during atomic publication")
        return MasteringResult(
            before=before,
            after=after,
            applied_gain_db=applied_gain,
            peak_constrained=peak_constrained,
            status=status,
            reason=reason,
            metadata=published,
        )
    finally:
        temporary.unlink(missing_ok=True)
