"""Chunk-aware content diagnostics from one timestamped ASR pass."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from itertools import pairwise

from app.services.content_coverage import (
    ContentCoverageThresholds,
    analyze_content_coverage,
)
from app.services.transcription_service import NarrationWord

CHUNK_CONTENT_DIAGNOSTICS_VERSION = "narration_chunk_content_diagnostics_v1"
_MAX_CHUNKS = 100


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def analyze_chunk_content(
    synthesis_text: str,
    words: Sequence[NarrationWord],
    provider_metadata: dict[str, object],
    *,
    thresholds: ContentCoverageThresholds,
    normalize_hypothesis: Callable[[str], str],
) -> dict[str, object]:
    """Partition existing ASR timestamps using Qwen's immutable chunk timeline.

    This function never invokes ASR. It is diagnostic: the aggregate structural
    report remains the blocking authority, while chunk reports locate warnings
    and failures in long-form narration.
    """
    raw_timeline = provider_metadata.get("assembly_timeline")
    if not isinstance(raw_timeline, list):
        return _unavailable("provider_chunk_timeline_unavailable")
    generated = [
        item
        for item in raw_timeline
        if isinstance(item, dict) and item.get("kind") == "generated_chunk"
    ]
    if not generated:
        return _unavailable("provider_chunk_timeline_unavailable")
    if len(generated) > _MAX_CHUNKS:
        return _unavailable("provider_chunk_count_exceeds_bound")

    windows: list[dict[str, float | int]] = []
    try:
        for fallback_index, item in enumerate(generated):
            chunk_index = int(item.get("chunk_index", fallback_index))
            start_seconds = float(item["start_seconds"])
            end_seconds = float(item["end_seconds"])
            start_char = int(item["text_start_char"])
            end_char = int(item["text_end_char"])
            if (
                not math.isfinite(start_seconds)
                or not math.isfinite(end_seconds)
                or start_seconds < 0
                or end_seconds <= start_seconds
                or start_char < 0
                or end_char <= start_char
                or end_char > len(synthesis_text)
            ):
                raise ValueError
            windows.append(
                {
                    "chunk_index": chunk_index,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "start_char": start_char,
                    "end_char": end_char,
                }
            )
    except (KeyError, TypeError, ValueError):
        return _unavailable("provider_chunk_timeline_malformed")
    windows.sort(key=lambda item: (float(item["start_seconds"]), int(item["chunk_index"])))
    if any(
        float(right["start_seconds"]) < float(left["end_seconds"])
        or int(right["start_char"]) < int(left["end_char"])
        for left, right in pairwise(windows)
    ):
        return _unavailable("provider_chunk_timeline_overlaps")

    assigned: dict[int, list[NarrationWord]] = {
        int(window["chunk_index"]): [] for window in windows
    }
    for word in words:
        midpoint = (word.start_seconds + word.end_seconds) / 2
        for position, window in enumerate(windows):
            start = float(window["start_seconds"])
            end = float(window["end_seconds"])
            if start <= midpoint < end or (
                position == len(windows) - 1 and math.isclose(midpoint, end, abs_tol=0.05)
            ):
                assigned[int(window["chunk_index"])].append(word)
                break

    chunks: list[dict[str, object]] = []
    for window in windows:
        chunk_index = int(window["chunk_index"])
        start_char = int(window["start_char"])
        end_char = int(window["end_char"])
        reference = synthesis_text[start_char:end_char]
        chunk_words = assigned[chunk_index]
        raw_hypothesis = " ".join(word.word.strip() for word in chunk_words if word.word.strip())
        report = analyze_content_coverage(
            reference,
            normalize_hypothesis(raw_hypothesis),
            thresholds=thresholds,
        )
        metrics = report.metrics
        chunks.append(
            {
                "chunk_index": chunk_index,
                "start_seconds": round(float(window["start_seconds"]), 6),
                "end_seconds": round(float(window["end_seconds"]), 6),
                "text_start_char": start_char,
                "text_end_char": end_char,
                "expected_text_sha256": _sha256_text(reference),
                "asr_text_sha256": _sha256_text(raw_hypothesis),
                "asr_word_count": len(chunk_words),
                "status": report.status,
                "classification": report.classification,
                "severity": report.severity,
                "retry_required": report.retry_required,
                "reasons": list(report.reasons),
                "normalized_wer": metrics.get("normalized_wer"),
                "normalized_cer": metrics.get("normalized_cer"),
                "coverage_ratio": metrics.get("coverage_ratio"),
                "structural_coverage_ratio": metrics.get("structural_coverage_ratio"),
                "deletion_ratio": metrics.get("deletion_ratio"),
                "substitution_ratio": metrics.get("substitution_ratio"),
                "longest_missing_contiguous_token_count": metrics.get(
                    "longest_missing_contiguous_token_count"
                ),
                "pronunciation_or_asr_mismatches": metrics.get(
                    "pronunciation_or_asr_mismatches", []
                ),
            }
        )
    return {
        "status": "available",
        "strategy_version": CHUNK_CONTENT_DIAGNOSTICS_VERSION,
        "asr_pass_count": 1,
        "decision_authority": "aggregate_structural_content_report",
        "chunk_count": len(chunks),
        "warning_chunk_count": sum(item["severity"] == "warning" for item in chunks),
        "blocking_chunk_count": sum(item["severity"] == "blocking" for item in chunks),
        "chunks": chunks,
    }


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "status": "unavailable",
        "strategy_version": CHUNK_CONTENT_DIAGNOSTICS_VERSION,
        "asr_pass_count": 1,
        "decision_authority": "aggregate_structural_content_report",
        "reason": reason,
        "chunks": [],
    }
