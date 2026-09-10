"""Transparent final narration mastering with real True Peak protection."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import soundfile as sf  # type: ignore[import-untyped]

from app.services.production_audio import AudioFileMetadata, inspect_wav


class MasteringError(RuntimeError):
    """The final narration cannot be published safely."""


@dataclass(frozen=True)
class LoudnessMetrics:
    integrated_lufs: float
    sample_peak_dbfs: float
    true_peak_dbtp: float


@dataclass(frozen=True)
class MasteringConfig:
    version: str = "transparent_dynamics_v3_ffmpeg_loudnorm"
    target_lufs: float = -16.0
    max_true_peak_dbtp: float = -1.0
    true_peak_tolerance_db: float = 0.02
    true_peak_safety_margin_db: float = 0.05
    min_acceptable_lufs: float = -18.0
    max_acceptable_lufs: float = -15.0
    duration_tolerance_seconds: float = 0.01
    compressor_enabled: bool = True
    compressor_threshold_db: float = -18.0
    compressor_ratio: float = 2.0
    compressor_attack_ms: float = 15.0
    compressor_release_ms: float = 220.0
    compressor_knee: float = 2.5
    loudness_range_target_lu: float = 9.0


@dataclass(frozen=True)
class MasteringResult:
    before: LoudnessMetrics
    after: LoudnessMetrics
    applied_gain_db: float
    peak_constrained: bool
    status: str
    reason: str
    metadata: AudioFileMetadata
    config_identity: str
    processing_identity: str
    input_sha256: str
    output_sha256: str
    dynamics_applied: bool
    compressor_peak_reduction_db: float

    def to_dict(self) -> dict[str, object]:
        return {
            "before": asdict(self.before),
            "after": asdict(self.after),
            "applied_gain_db": self.applied_gain_db,
            "peak_constrained": self.peak_constrained,
            "status": self.status,
            "reason": self.reason,
            "config_identity": self.config_identity,
            "processing_identity": self.processing_identity,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "dynamics_applied": self.dynamics_applied,
            "compressor_peak_reduction_db": self.compressor_peak_reduction_db,
        }


_LOUDNORM_JSON = re.compile(r'\{\s*"input_i".*?\}', re.DOTALL)


def _canonical_identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def mastering_config_identity(config: MasteringConfig) -> str:
    return _canonical_identity(asdict(config))


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise MasteringError("FFmpeg is required for mastering")
    return executable


def _ffmpeg_version(executable: str) -> str:
    result = subprocess.run([executable, "-version"], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout:
        raise MasteringError("FFmpeg version could not be determined")
    return result.stdout.splitlines()[0]


def _compressor_filter(config: MasteringConfig) -> str:
    threshold = 10 ** (config.compressor_threshold_db / 20)
    return (
        f"acompressor=threshold={threshold:.9f}:ratio={config.compressor_ratio}:"
        f"attack={config.compressor_attack_ms}:release={config.compressor_release_ms}:"
        f"makeup=1:knee={config.compressor_knee}:detection=rms"
    )


def _analyze_dynamics(
    executable: str, source: Path, config: MasteringConfig, true_peak_target: float
) -> dict[str, float]:
    filters = []
    if config.compressor_enabled:
        filters.append(_compressor_filter(config))
    filters.append(
        f"loudnorm=I={config.target_lufs}:TP={true_peak_target}:"
        f"LRA={config.loudness_range_target_lu}:print_format=json"
    )
    result = subprocess.run(
        [
            executable,
            "-nostdin",
            "-hide_banner",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            ",".join(filters),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = _LOUDNORM_JSON.findall(result.stderr)
    if result.returncode != 0 or not matches:
        raise MasteringError("FFmpeg mastering analysis failed")
    try:
        raw = json.loads(matches[-1])
        return {
            "input_i": float(raw["input_i"]),
            "input_tp": float(raw["input_tp"]),
            "input_lra": float(raw["input_lra"]),
            "input_thresh": float(raw["input_thresh"]),
            "target_offset": float(raw["target_offset"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MasteringError("FFmpeg returned invalid mastering analysis") from exc


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
    config_id = mastering_config_identity(config)
    executable = _ffmpeg()
    tool_version = _ffmpeg_version(executable)
    processing_identity = _canonical_identity(
        {
            "config_identity": config_id,
            "input_sha256": source_metadata.sha256,
            "tool_version": tool_version,
        }
    )
    already_acceptable = (
        config.min_acceptable_lufs <= before.integrated_lufs <= config.max_acceptable_lufs
        and before.true_peak_dbtp <= config.max_true_peak_dbtp
    )
    true_peak_target = config.max_true_peak_dbtp - config.true_peak_safety_margin_db
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp.wav"
    try:
        if already_acceptable:
            shutil.copyfile(source, temporary)
            measured = {
                "input_i": before.integrated_lufs,
                "input_tp": before.true_peak_dbtp,
                "input_lra": 0.0,
                "input_thresh": 0.0,
                "target_offset": 0.0,
            }
            applied_gain = 0.0
            dynamics_applied = False
        else:
            measured = _analyze_dynamics(executable, source, config, true_peak_target)
            loudnorm = (
                f"loudnorm=I={config.target_lufs}:TP={true_peak_target}:"
                f"LRA={config.loudness_range_target_lu}:"
                f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:"
                f"measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}:linear=true:print_format=summary"
            )
            filters = [loudnorm]
            if config.compressor_enabled:
                filters.insert(0, _compressor_filter(config))
            result = subprocess.run(
                [
                    executable,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-af",
                    ",".join(filters),
                    "-ar",
                    str(info.samplerate),
                    "-ac",
                    str(info.channels),
                    "-c:a",
                    "pcm_s16le",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise MasteringError("FFmpeg mastering DSP failed")
            applied_gain = config.target_lufs - measured["input_i"]
            dynamics_applied = config.compressor_enabled
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
        if after.integrated_lufs < config.min_acceptable_lufs:
            raise MasteringError("mastered narration is quieter than the accepted range")
        else:
            status = "pass"
            reason = (
                "input already satisfied mastering policy; DSP bypassed"
                if already_acceptable
                else "transparent dynamics, loudness and True Peak are within policy"
            )
        os.replace(temporary, destination)
        published = inspect_wav(destination)
        if published != metadata:
            destination.unlink(missing_ok=True)
            raise MasteringError("mastered WAV changed during atomic publication")
        return MasteringResult(
            before=before,
            after=after,
            applied_gain_db=applied_gain,
            peak_constrained=False,
            status=status,
            reason=reason,
            metadata=published,
            config_identity=config_id,
            processing_identity=processing_identity,
            input_sha256=source_metadata.sha256,
            output_sha256=published.sha256,
            dynamics_applied=dynamics_applied,
            compressor_peak_reduction_db=(
                max(0.0, before.true_peak_dbtp - measured["input_tp"]) if dynamics_applied else 0.0
            ),
        )
    finally:
        temporary.unlink(missing_ok=True)
