"""Deterministic, non-destructive diagnostics for narration and voice references."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from numpy.typing import NDArray

from app.services.production_audio import ProductionAudioError, inspect_wav

DIAGNOSTICS_VERSION = "narration_audio_diagnostics_v1"


@dataclass(frozen=True)
class SilenceSpan:
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    source: str
    reason: str
    chunk_index: int | None = None
    boundary_type: str | None = None


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else -120.0


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    padded = np.pad(mask.astype(np.int8), (1, 1))
    edges = np.flatnonzero(np.diff(padded))
    return [(int(start), int(end)) for start, end in edges.reshape(-1, 2)]


def _silence_runs(
    mono: NDArray[np.float64],
    sample_rate: int,
    *,
    threshold_dbfs: float,
    minimum_seconds: float,
) -> list[tuple[float, float]]:
    threshold = 10.0 ** (threshold_dbfs / 20.0)
    minimum_frames = max(1, round(minimum_seconds * sample_rate))
    return [
        (start / sample_rate, end / sample_rate)
        for start, end in _runs(np.abs(mono) <= threshold)
        if end - start >= minimum_frames
    ]


def _overlap(start: float, end: float, candidate: dict[str, Any]) -> float:
    return max(
        0.0,
        min(end, float(candidate.get("end_seconds", 0.0)))
        - max(start, float(candidate.get("start_seconds", 0.0))),
    )


def _classify_silence(
    start: float,
    end: float,
    inserted_pauses: tuple[dict[str, Any], ...],
) -> SilenceSpan:
    duration = end - start
    best = max(inserted_pauses, key=lambda item: _overlap(start, end, item), default=None)
    best_overlap = _overlap(start, end, best) if best is not None else 0.0
    if best is not None and best_overlap >= min(duration, 0.05) * 0.8:
        return SilenceSpan(
            start_seconds=round(start, 6),
            end_seconds=round(end, 6),
            duration_seconds=round(duration, 6),
            source=str(best.get("source", "deterministic_inserted_pause")),
            reason=str(best.get("reason", "configured boundary pause")),
            chunk_index=(int(best["chunk_index"]) if best.get("chunk_index") is not None else None),
            boundary_type=(
                str(best["boundary_type"]) if best.get("boundary_type") is not None else None
            ),
        )
    return SilenceSpan(
        start_seconds=round(start, 6),
        end_seconds=round(end, 6),
        duration_seconds=round(duration, 6),
        source="model_internal_or_source_silence",
        reason="detected silence outside a deterministic inserted boundary",
    )


def _window_rms(mono: NDArray[np.float64], window_frames: int) -> NDArray[np.float64]:
    usable = len(mono) - (len(mono) % window_frames)
    if usable <= 0:
        return np.asarray([math.sqrt(float(np.mean(np.square(mono))))], dtype=np.float64)
    windows = mono[:usable].reshape(-1, window_frames)
    return np.sqrt(np.mean(np.square(windows), axis=1))


def _transient_candidates(
    mono: NDArray[np.float64], sample_rate: int, *, maximum: int = 50
) -> list[dict[str, float | bool]]:
    difference = np.abs(np.diff(mono, prepend=mono[0]))
    median = float(np.median(difference))
    mad = float(np.median(np.abs(difference - median)))
    robust_scale = max(mad * 1.4826, 1e-9)
    threshold = max(0.05, median + 12.0 * robust_scale)
    candidate_indexes = np.flatnonzero(difference >= threshold)
    context_frames = max(1, round(sample_rate * 0.05))
    cumulative_energy = np.concatenate(
        (np.asarray([0.0]), np.cumsum(np.square(mono), dtype=np.float64))
    )

    def context_rms(index: int) -> float:
        start = max(0, index - context_frames)
        end = min(len(mono), index + context_frames + 1)
        energy = cumulative_energy[end] - cumulative_energy[start]
        return math.sqrt(max(float(energy) / max(end - start, 1), 0.0))

    context = {int(index): context_rms(int(index)) for index in candidate_indexes}
    low_energy_limit = 10.0 ** (-35.0 / 20.0)
    selected: list[int] = []
    minimum_spacing = max(1, round(sample_rate * 0.04))
    ranked = sorted(
        (int(index) for index in candidate_indexes),
        key=lambda index: (
            context[index] <= low_energy_limit,
            float(difference[index]),
        ),
        reverse=True,
    )
    for index in ranked:
        if all(abs(int(index) - previous) >= minimum_spacing for previous in selected):
            selected.append(int(index))
            if len(selected) >= maximum:
                break
    selected.sort()
    return [
        {
            "time_seconds": round(index / sample_rate, 6),
            "step_amplitude": round(float(difference[index]), 8),
            "robust_z_score": round(float((difference[index] - median) / robust_scale), 3),
            "context_rms_dbfs": round(_dbfs(context[index]), 4),
            "low_energy_context": context[index] <= low_energy_limit,
        }
        for index in selected
    ]


def _hum_ratios(mono: NDArray[np.float64], sample_rate: int) -> dict[str, float | None]:
    maximum_frames = min(len(mono), sample_rate * 120)
    if maximum_frames < sample_rate:
        return {"50_hz_ratio": None, "60_hz_ratio": None}
    signal = mono[:maximum_frames] - float(np.mean(mono[:maximum_frames]))
    windowed = signal * np.hanning(len(signal))
    spectrum = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(len(windowed), 1.0 / sample_rate)
    baseline_mask = (frequencies >= 40.0) & (frequencies <= 70.0)
    baseline = float(np.median(spectrum[baseline_mask])) if np.any(baseline_mask) else 0.0
    result: dict[str, float | None] = {}
    for frequency in (50, 60):
        index = int(np.argmin(np.abs(frequencies - frequency)))
        result[f"{frequency}_hz_ratio"] = (
            round(float(spectrum[index] / baseline), 4) if baseline > 0 else None
        )
    return result


def analyze_audio(
    path: Path,
    *,
    inserted_pauses: Iterable[dict[str, Any]] = (),
    silence_threshold_dbfs: float = -45.0,
    minimum_silence_seconds: float = 0.15,
) -> dict[str, Any]:
    """Analyze without mutating audio; timestamps are diagnostic candidates only."""
    metadata = inspect_wav(path)
    try:
        samples, sample_rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("audio diagnostics could not decode WAV") from exc
    mono = np.mean(samples, axis=1)
    spans = _silence_runs(
        mono,
        sample_rate,
        threshold_dbfs=silence_threshold_dbfs,
        minimum_seconds=minimum_silence_seconds,
    )
    pause_inputs = tuple(inserted_pauses)
    timeline = tuple(_classify_silence(start, end, pause_inputs) for start, end in spans)
    window_rms = _window_rms(mono, max(1, round(sample_rate * 0.02)))
    threshold = 10.0 ** (silence_threshold_dbfs / 20.0)
    leading = spans[0][1] if spans and spans[0][0] == 0 else 0.0
    trailing = (
        metadata.duration_seconds - spans[-1][0]
        if spans and spans[-1][1] >= metadata.duration_seconds
        else 0.0
    )
    internal_total = sum(
        span.duration_seconds
        for span in timeline
        if span.source == "model_internal_or_source_silence"
    )
    inserted_total = sum(
        span.duration_seconds
        for span in timeline
        if span.source != "model_internal_or_source_silence"
    )
    return {
        "version": DIAGNOSTICS_VERSION,
        "artifact": asdict(metadata),
        "signal": {
            "dc_offset": round(float(np.mean(mono)), 9),
            "rms_dbfs": round(_dbfs(math.sqrt(float(np.mean(np.square(mono))))), 4),
            "noise_floor_estimate_dbfs": round(_dbfs(float(np.percentile(window_rms, 10))), 4),
            "silence_threshold_dbfs": silence_threshold_dbfs,
            "leading_silence_seconds": round(leading, 6),
            "trailing_silence_seconds": round(trailing, 6),
            "silence_run_count": len(timeline),
            "silence_total_seconds": round(sum(item.duration_seconds for item in timeline), 6),
            "silence_ratio": round(
                sum(item.duration_seconds for item in timeline) / metadata.duration_seconds, 6
            ),
            "longest_silence_seconds": round(
                max((item.duration_seconds for item in timeline), default=0.0), 6
            ),
            "model_internal_or_source_silence_seconds": round(internal_total, 6),
            "deterministic_inserted_silence_seconds": round(inserted_total, 6),
            "near_silent_sample_ratio": round(float(np.mean(np.abs(mono) <= threshold)), 6),
            "hum_indicators": _hum_ratios(mono, sample_rate),
        },
        "transient_candidates": _transient_candidates(mono, sample_rate),
        "pause_timeline": [asdict(item) for item in timeline],
        "interpretation": (
            "Transient and hum values are diagnostic candidates; this analyzer performs no cleanup."
        ),
    }
