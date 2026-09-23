"""Deterministic, non-destructive diagnostics for narration and voice references."""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from numpy.typing import NDArray

from app.services.production_audio import (
    IO_BLOCK_FRAMES,
    AudioFileMetadata,
    ProductionAudioError,
    inspect_wav_metadata,
)

DIAGNOSTICS_VERSION = "narration_audio_diagnostics_v2_streaming"


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


def _histogram_median(histogram: NDArray[np.int64], *, scale: float) -> float:
    total = int(np.sum(histogram))
    if total <= 0:
        return 0.0
    cumulative = np.cumsum(histogram)
    lower_rank = (total - 1) // 2
    upper_rank = total // 2
    lower = int(np.searchsorted(cumulative, lower_rank + 1))
    upper = int(np.searchsorted(cumulative, upper_rank + 1))
    return (lower + upper) / (2.0 * scale)


def _noise_floor_from_histogram(histogram: NDArray[np.int64]) -> float:
    total = int(np.sum(histogram))
    if total <= 0:
        return -120.0
    rank = max(1, math.ceil(total * 0.10))
    index = int(np.searchsorted(np.cumsum(histogram), rank))
    return -120.0 + index / 10.0


def _streaming_transient_candidates(
    path: Path,
    sample_rate: int,
    frame_count: int,
    difference_histogram: NDArray[np.int64],
    *,
    maximum: int = 50,
) -> list[dict[str, float | bool]]:
    median = _histogram_median(difference_histogram, scale=32768.0)
    indexes = np.arange(len(difference_histogram), dtype=np.float64) / 32768.0
    distances = np.rint(np.abs(indexes - median) * 32768.0).astype(np.int64)
    mad_histogram = np.bincount(
        distances,
        weights=difference_histogram,
        minlength=len(difference_histogram),
    ).astype(np.int64)
    mad = _histogram_median(mad_histogram, scale=32768.0)
    robust_scale = max(mad * 1.4826, 1e-9)
    threshold = max(0.05, median + 12.0 * robust_scale)
    context_frames = max(1, round(sample_rate * 0.05))
    low_energy_limit = 10.0 ** (-35.0 / 20.0)
    candidate_limit = maximum * 20
    candidates: list[tuple[int, float, int, float]] = []
    try:
        with sf.SoundFile(str(path), mode="r") as source:
            core_start = 0
            while core_start < frame_count:
                core_end = min(frame_count, core_start + IO_BLOCK_FRAMES)
                read_start = max(0, core_start - context_frames - 1)
                read_end = min(frame_count, core_end + context_frames)
                source.seek(read_start)
                samples = source.read(
                    read_end - read_start,
                    dtype="float64",
                    always_2d=True,
                )
                mono = np.mean(samples, axis=1, dtype=np.float64)
                local_start = core_start - read_start
                local_end = core_end - read_start
                core = mono[local_start:local_end]
                previous = core[0] if core_start == 0 else mono[local_start - 1]
                difference = np.abs(np.diff(core, prepend=previous))
                for local_index in np.flatnonzero(difference >= threshold):
                    global_index = core_start + int(local_index)
                    buffer_index = local_start + int(local_index)
                    context_start = max(0, buffer_index - context_frames)
                    context_end = min(len(mono), buffer_index + context_frames + 1)
                    context = mono[context_start:context_end]
                    context_rms = math.sqrt(float(np.mean(np.square(context))))
                    low_energy = int(context_rms <= low_energy_limit)
                    entry = (low_energy, float(difference[local_index]), -global_index, context_rms)
                    if len(candidates) < candidate_limit:
                        heapq.heappush(candidates, entry)
                    elif entry[:3] > candidates[0][:3]:
                        heapq.heapreplace(candidates, entry)
                core_start = core_end
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("audio transient scan failed") from exc

    ranked = sorted(candidates, key=lambda item: item[:3], reverse=True)
    selected: list[tuple[int, float, float, bool]] = []
    minimum_spacing = max(1, round(sample_rate * 0.04))
    for low_energy, difference, negative_index, context_rms in ranked:
        index = -negative_index
        if all(abs(index - previous[0]) >= minimum_spacing for previous in selected):
            selected.append((index, difference, context_rms, bool(low_energy)))
            if len(selected) >= maximum:
                break
    selected.sort(key=lambda item: item[0])
    return [
        {
            "time_seconds": round(index / sample_rate, 6),
            "step_amplitude": round(difference, 8),
            "robust_z_score": round((difference - median) / robust_scale, 3),
            "context_rms_dbfs": round(_dbfs(context_rms), 4),
            "low_energy_context": low_energy,
        }
        for index, difference, context_rms, low_energy in selected
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
    physical = inspect_wav_metadata(path)
    sample_rate = physical.sample_rate
    threshold = 10.0 ** (silence_threshold_dbfs / 20.0)
    minimum_frames = max(1, round(minimum_silence_seconds * sample_rate))
    window_frames = max(1, round(sample_rate * 0.02))
    difference_histogram = np.zeros(65_536, dtype=np.int64)
    noise_histogram = np.zeros(1_201, dtype=np.int64)
    hum_samples = np.empty(min(physical.frame_count, sample_rate * 120), dtype=np.float64)
    hum_count = 0
    total_sum = 0.0
    total_square_sum = 0.0
    mono_count = 0
    near_silent_count = 0
    signal_peak = 0.0
    clipping_count = 0
    previous_sample: float | None = None
    silence_start: int | None = None
    spans_frames: list[tuple[int, int]] = []
    window_energy = 0.0
    window_count = 0
    global_frame = 0
    try:
        with sf.SoundFile(str(path), mode="r") as source:
            for samples in source.blocks(
                blocksize=IO_BLOCK_FRAMES,
                dtype="float64",
                always_2d=True,
            ):
                if not np.all(np.isfinite(samples)):
                    raise ProductionAudioError("audio diagnostics found non-finite samples")
                absolute = np.abs(samples)
                signal_peak = max(signal_peak, float(np.max(absolute)))
                clipping_count += int(np.count_nonzero(absolute >= 0.999))
                mono = np.mean(samples, axis=1, dtype=np.float64)
                total_sum += float(np.sum(mono, dtype=np.float64))
                total_square_sum += float(np.sum(np.square(mono), dtype=np.float64))
                mono_count += len(mono)
                silent_mask = np.abs(mono) <= threshold
                near_silent_count += int(np.count_nonzero(silent_mask))

                if silence_start is not None and (not len(silent_mask) or not silent_mask[0]):
                    if global_frame - silence_start >= minimum_frames:
                        spans_frames.append((silence_start, global_frame))
                    silence_start = None
                for start, end in _runs(silent_mask):
                    run_start = (
                        silence_start
                        if start == 0 and silence_start is not None
                        else global_frame + start
                    )
                    if end < len(silent_mask):
                        if global_frame + end - run_start >= minimum_frames:
                            spans_frames.append((run_start, global_frame + end))
                        silence_start = None
                    else:
                        silence_start = run_start

                previous = mono[0] if previous_sample is None else previous_sample
                differences = np.abs(np.diff(mono, prepend=previous))
                quantized = np.clip(np.rint(differences * 32768.0), 0, 65_535).astype(np.int64)
                difference_histogram += np.bincount(quantized, minlength=65_536)
                previous_sample = float(mono[-1])

                position = 0
                if window_count:
                    take = min(window_frames - window_count, len(mono))
                    window_energy += float(np.sum(np.square(mono[:take]), dtype=np.float64))
                    window_count += take
                    position = take
                    if window_count == window_frames:
                        rms = math.sqrt(window_energy / window_count)
                        index = int(np.clip(round((_dbfs(rms) + 120.0) * 10.0), 0, 1200))
                        noise_histogram[index] += 1
                        window_energy = 0.0
                        window_count = 0
                usable = ((len(mono) - position) // window_frames) * window_frames
                if usable:
                    windows = mono[position : position + usable].reshape(-1, window_frames)
                    energies = np.mean(np.square(windows), axis=1, dtype=np.float64)
                    rms_values = np.sqrt(energies)
                    db_values = np.full(rms_values.shape, -120.0, dtype=np.float64)
                    nonzero = rms_values > 0
                    db_values[nonzero] = 20.0 * np.log10(rms_values[nonzero])
                    indexes = np.clip(
                        np.rint((db_values + 120.0) * 10.0),
                        0,
                        1200,
                    ).astype(np.int64)
                    noise_histogram += np.bincount(indexes, minlength=1201)
                    position += usable
                if position < len(mono):
                    remainder = mono[position:]
                    window_energy = float(np.sum(np.square(remainder), dtype=np.float64))
                    window_count = len(remainder)

                if hum_count < len(hum_samples):
                    take = min(len(mono), len(hum_samples) - hum_count)
                    hum_samples[hum_count : hum_count + take] = mono[:take]
                    hum_count += take
                global_frame += len(mono)
    except ProductionAudioError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("audio diagnostics could not decode WAV") from exc
    if silence_start is not None and global_frame - silence_start >= minimum_frames:
        spans_frames.append((silence_start, global_frame))
    if mono_count != physical.frame_count or mono_count == 0:
        raise ProductionAudioError("audio diagnostics frame count mismatch")
    if int(np.sum(noise_histogram)) == 0 and window_count:
        rms = math.sqrt(window_energy / window_count)
        index = int(np.clip(round((_dbfs(rms) + 120.0) * 10.0), 0, 1200))
        noise_histogram[index] += 1
    spans = [(start / sample_rate, end / sample_rate) for start, end in spans_frames]
    pause_inputs = tuple(inserted_pauses)
    timeline = tuple(_classify_silence(start, end, pause_inputs) for start, end in spans)
    leading = spans[0][1] if spans and spans[0][0] == 0 else 0.0
    trailing = (
        physical.duration_seconds - spans[-1][0]
        if spans and spans[-1][1] >= physical.duration_seconds
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
    metadata = AudioFileMetadata(
        **asdict(physical),
        sample_peak_dbfs=(20.0 * math.log10(signal_peak) if signal_peak > 0 else float("-inf")),
        clipping_sample_count=clipping_count,
    )
    return {
        "version": DIAGNOSTICS_VERSION,
        "artifact": asdict(metadata),
        "signal": {
            "dc_offset": round(total_sum / mono_count, 9),
            "rms_dbfs": round(_dbfs(math.sqrt(total_square_sum / mono_count)), 4),
            "noise_floor_estimate_dbfs": round(_noise_floor_from_histogram(noise_histogram), 4),
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
            "near_silent_sample_ratio": round(near_silent_count / mono_count, 6),
            "hum_indicators": _hum_ratios(hum_samples[:hum_count], sample_rate),
        },
        "transient_candidates": _streaming_transient_candidates(
            path,
            sample_rate,
            physical.frame_count,
            difference_histogram,
        ),
        "pause_timeline": [asdict(item) for item in timeline],
        "interpretation": (
            "Transient and hum values are diagnostic candidates; this analyzer performs no cleanup."
        ),
    }
