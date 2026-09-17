"""Deterministic, streaming narration-stem post-processing for human A/B review."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.audio_diagnostics import analyze_audio
from app.services.mastering import MasteringError, measure_loudness_and_true_peak
from app.services.production_audio import inspect_wav

POSTPROCESS_IMPLEMENTATION_VERSION = "ffmpeg_narration_stem_post_v1"
TEMPO_IMPLEMENTATION_VERSION = "ffmpeg_narration_tempo_v1"
_LOUDNORM_JSON = re.compile(r'\{\s*"input_i".*?\}', re.DOTALL)


@dataclass(frozen=True)
class NarrationPostProfile:
    name: str
    version: str
    target_lufs: float
    true_peak_dbtp: float
    lra_target: float
    compressor_threshold_db: float
    compressor_ratio: float
    compressor_attack_ms: float
    compressor_release_ms: float
    compressor_knee: float
    output_sample_rate: int = 48_000


CONSERVATIVE = NarrationPostProfile(
    name="narration_post_conservative_v1",
    version="1",
    target_lufs=-17.0,
    true_peak_dbtp=-1.5,
    lra_target=11.0,
    compressor_threshold_db=-16.0,
    compressor_ratio=1.6,
    compressor_attack_ms=20.0,
    compressor_release_ms=250.0,
    compressor_knee=2.0,
)

BALANCED = NarrationPostProfile(
    name="narration_post_balanced_v1",
    version="1",
    target_lufs=-16.0,
    true_peak_dbtp=-1.5,
    lra_target=9.0,
    compressor_threshold_db=-18.0,
    compressor_ratio=2.0,
    compressor_attack_ms=15.0,
    compressor_release_ms=220.0,
    compressor_knee=2.5,
)

POST_PROFILES = {item.name: item for item in (CONSERVATIVE, BALANCED)}


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise MasteringError("FFmpeg is required for narration post-processing")
    return executable


def _ffmpeg_version(executable: str) -> str:
    result = subprocess.run([executable, "-version"], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout:
        raise MasteringError("FFmpeg version could not be determined")
    return result.stdout.splitlines()[0]


def _compressor_filter(profile: NarrationPostProfile) -> str:
    threshold = 10 ** (profile.compressor_threshold_db / 20)
    return (
        f"acompressor=threshold={threshold:.9f}:ratio={profile.compressor_ratio}:"
        f"attack={profile.compressor_attack_ms}:release={profile.compressor_release_ms}:"
        f"makeup=1:knee={profile.compressor_knee}:detection=rms"
    )


def _loudnorm_analysis(
    executable: str, source: Path, profile: NarrationPostProfile
) -> dict[str, float]:
    filters = (
        f"{_compressor_filter(profile)},"
        f"loudnorm=I={profile.target_lufs}:TP={profile.true_peak_dbtp}:"
        f"LRA={profile.lra_target}:print_format=json"
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
            filters,
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
        raise MasteringError("FFmpeg loudnorm analysis failed")
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
        raise MasteringError("FFmpeg returned invalid loudnorm analysis") from exc


def _metrics(path: Path) -> dict[str, Any]:
    physical = inspect_wav(path)
    integrated_lufs, true_peak = measure_loudness_and_true_peak(path)
    diagnostics = analyze_audio(path)
    signal = diagnostics["signal"]
    rms = float(signal["rms_dbfs"])
    return {
        "artifact": asdict(physical),
        "integrated_lufs": integrated_lufs,
        "loudness_range_lu": _analysis_lra(path),
        "sample_peak_dbfs": physical.sample_peak_dbfs,
        "true_peak_dbtp": true_peak,
        "rms_dbfs": rms,
        "crest_factor_db": round(physical.sample_peak_dbfs - rms, 4),
        "clipping_sample_count": physical.clipping_sample_count,
        "dc_offset": signal["dc_offset"],
        "silence_ratio": round(
            float(signal["silence_total_seconds"]) / physical.duration_seconds, 6
        ),
        "duration_seconds": physical.duration_seconds,
        "sample_rate": physical.sample_rate,
        "channels": physical.channels,
    }


def _analysis_lra(path: Path) -> float:
    executable = _ffmpeg()
    result = subprocess.run(
        [
            executable,
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-23:TP=-2:LRA=11:print_format=json",
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
        raise MasteringError("FFmpeg LRA analysis failed")
    return float(json.loads(matches[-1])["input_lra"])


def process_narration_stem(
    source: Path,
    destination: Path,
    profile: NarrationPostProfile,
) -> dict[str, object]:
    """Publish a new stem atomically while proving that raw input is unchanged."""

    started = time.monotonic()
    source_before = inspect_wav(source)
    source_metrics = _metrics(source)
    executable = _ffmpeg()
    tool_version = _ffmpeg_version(executable)
    measured = _loudnorm_analysis(executable, source, profile)
    profile_payload = asdict(profile)
    identity_payload = {
        "implementation": POSTPROCESS_IMPLEMENTATION_VERSION,
        "profile": profile_payload,
        "input_sha256": source_before.sha256,
        "input_sample_rate": source_before.sample_rate,
        "tool_version": tool_version,
    }
    identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp.wav"
    loudnorm = (
        f"loudnorm=I={profile.target_lufs}:TP={profile.true_peak_dbtp}:LRA={profile.lra_target}:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary"
    )
    filters = f"{_compressor_filter(profile)},{loudnorm}"
    try:
        result = subprocess.run(
            [
                executable,
                "-nostdin",
                "-hide_banner",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-af",
                filters,
                "-ar",
                str(profile.output_sample_rate),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise MasteringError("FFmpeg narration post-processing failed")
        output = inspect_wav(temporary)
        output_metrics = _metrics(temporary)
        source_after = inspect_wav(source)
        if source_after.sha256 != source_before.sha256:
            raise MasteringError("raw narration changed during post-processing")
        if abs(output.duration_seconds - source_before.duration_seconds) > 0.02:
            raise MasteringError("post-processing changed narration duration")
        if output.sample_rate != profile.output_sample_rate or output.channels != 1:
            raise MasteringError("post-processing output format is invalid")
        if output.clipping_sample_count:
            raise MasteringError("post-processing output contains clipping")
        if float(output_metrics["true_peak_dbtp"]) > profile.true_peak_dbtp + 0.1:
            raise MasteringError("post-processing output exceeds True Peak target")
        if not math.isclose(
            float(output_metrics["integrated_lufs"]), profile.target_lufs, abs_tol=0.5
        ):
            raise MasteringError("post-processing output missed loudness target")
        os.replace(temporary, destination)
        published = inspect_wav(destination)
        if published.sha256 != output.sha256:
            destination.unlink(missing_ok=True)
            raise MasteringError("post-processed stem changed during atomic publication")
        return {
            "version": POSTPROCESS_IMPLEMENTATION_VERSION,
            "identity": identity,
            "profile": profile_payload,
            "tool_version": tool_version,
            "input_sha256": source_before.sha256,
            "output_sha256": published.sha256,
            "before": source_metrics,
            "after": output_metrics,
            "processing_seconds": round(time.monotonic() - started, 3),
            "status": "passed",
        }
    finally:
        temporary.unlink(missing_ok=True)


def process_narration_tempo_candidate(
    source: Path,
    destination: Path,
    *,
    tempo_factor: float,
) -> dict[str, object]:
    """Create a pitch-preserving production pacing artifact atomically."""

    if not 1.0 <= tempo_factor <= 1.5:
        raise ValueError("tempo_factor must be between 1.0 and 1.5 for this conservative harness")
    started = time.monotonic()
    source_before = inspect_wav(source)
    before = _metrics(source)
    executable = _ffmpeg()
    tool_version = _ffmpeg_version(executable)
    identity_payload = {
        "implementation": TEMPO_IMPLEMENTATION_VERSION,
        "tempo_factor": round(tempo_factor, 6),
        "input_sha256": source_before.sha256,
        "input_sample_rate": source_before.sample_rate,
        "tool_version": tool_version,
    }
    identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp.wav"
    try:
        result = subprocess.run(
            [
                executable,
                "-nostdin",
                "-hide_banner",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-af",
                f"atempo={tempo_factor:.6f}",
                "-ar",
                str(source_before.sample_rate),
                "-ac",
                str(source_before.channels),
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise MasteringError("FFmpeg tempo candidate processing failed")
        output = inspect_wav(temporary)
        after = _metrics(temporary)
        source_after = inspect_wav(source)
        if source_after.sha256 != source_before.sha256:
            raise MasteringError("raw narration changed during tempo processing")
        expected_duration = source_before.duration_seconds / tempo_factor
        if abs(output.duration_seconds - expected_duration) > max(0.08, expected_duration * 0.002):
            raise MasteringError("tempo candidate duration ratio is outside tolerance")
        if (
            output.sample_rate != source_before.sample_rate
            or output.channels != source_before.channels
        ):
            raise MasteringError("tempo candidate changed the audio format")
        if output.clipping_sample_count:
            raise MasteringError("tempo candidate introduced clipping")
        os.replace(temporary, destination)
        published = inspect_wav(destination)
        return {
            "version": TEMPO_IMPLEMENTATION_VERSION,
            "identity": identity,
            "tempo_factor": tempo_factor,
            "tool_version": tool_version,
            "input_sha256": source_before.sha256,
            "output_sha256": published.sha256,
            "raw_tts_duration_seconds": None,
            "assembled_raw_duration_seconds": source_before.duration_seconds,
            "mastered_narration_duration_seconds": published.duration_seconds,
            "before": before,
            "after": after,
            "processing_seconds": round(time.monotonic() - started, 3),
            "status": "passed",
        }
    finally:
        temporary.unlink(missing_ok=True)


def resolve_narration_tempo(content_locale: str | None, config: Any) -> float:
    """Resolve the explicitly evidenced production tempo per locale."""
    locale = (content_locale or "pt-BR").replace("_", "-").lower()
    return float(config.audio_narration_tempo_en_us if locale == "en-us" else config.audio_narration_tempo_pt_br)
