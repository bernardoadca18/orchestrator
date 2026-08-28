"""ASR-normalized content coverage for detecting missing, repeated, or reordered speech."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Literal

from app.services.narration_alignment_algorithm import TextToken, token_similarity, tokenize

CONTENT_COVERAGE_VERSION = "narration_content_coverage_v1"


@dataclass(frozen=True)
class ContentCoverageThresholds:
    minimum_coverage_ratio: float = 0.72
    maximum_deletion_ratio: float = 0.22
    maximum_insertion_ratio: float = 0.30
    maximum_repetition_ratio: float = 0.20
    maximum_missing_contiguous_tokens: int = 6
    maximum_trailing_missing_tokens: int = 4

    @property
    def identity(self) -> str:
        payload = json.dumps(
            {"version": CONTENT_COVERAGE_VERSION, **asdict(self)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ContentCoverageReport:
    status: Literal["passed", "rejected", "unavailable"]
    reasons: tuple[str, ...]
    metrics: dict[str, object]
    policy_version: str
    policy_identity: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "metrics": self.metrics,
            "policy_version": self.policy_version,
            "policy_identity": self.policy_identity,
        }


def _token_equal(left: TextToken, right: TextToken) -> bool:
    return token_similarity(left, right) >= 0.68


def _token_edit_operations(
    reference: tuple[TextToken, ...], hypothesis: tuple[TextToken, ...]
) -> list[tuple[str, int | None, int | None]]:
    n, m = len(reference), len(hypothesis)
    if n > 2_500 or m > 2_500 or n * m > 2_000_000:
        raise ValueError("content coverage input exceeds deterministic alignment limits")
    previous = list(range(m + 1))
    trace = [bytearray(m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        trace[0][j] = 2
    for i in range(1, n + 1):
        current = [i] + [0] * m
        trace[i][0] = 1
        for j in range(1, m + 1):
            diagonal = previous[j - 1] + (
                0 if _token_equal(reference[i - 1], hypothesis[j - 1]) else 1
            )
            deletion = previous[j] + 1
            insertion = current[j - 1] + 1
            if diagonal <= deletion and diagonal <= insertion:
                current[j] = diagonal
                trace[i][j] = 0
            elif deletion <= insertion:
                current[j] = deletion
                trace[i][j] = 1
            else:
                current[j] = insertion
                trace[i][j] = 2
        previous = current

    operations: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i or j:
        direction = trace[i][j]
        if i and j and direction == 0:
            if _token_equal(reference[i - 1], hypothesis[j - 1]):
                kind = (
                    "match"
                    if reference[i - 1].accentless == hypothesis[j - 1].accentless
                    else "pronunciation_variant"
                )
            else:
                kind = "substitution"
            operations.append((kind, i - 1, j - 1))
            i -= 1
            j -= 1
        elif i and (not j or direction == 1):
            operations.append(("deletion", i - 1, None))
            i -= 1
        else:
            operations.append(("insertion", None, j - 1))
            j -= 1
    operations.reverse()
    return operations


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, start=1):
        current = [index]
        for other_index, other in enumerate(right, start=1):
            current.append(
                min(
                    previous[other_index] + 1,
                    current[-1] + 1,
                    previous[other_index - 1] + (char != other),
                )
            )
        previous = current
    return previous[-1]


def _missing_spans(
    operations: list[tuple[str, int | None, int | None]], reference: tuple[TextToken, ...]
) -> list[tuple[int, int]]:
    missing = sorted(
        ref_index
        for kind, ref_index, _ in operations
        if ref_index is not None and kind in {"deletion", "substitution"}
    )
    spans: list[tuple[int, int]] = []
    for index in missing:
        if spans and index == spans[-1][1]:
            spans[-1] = (spans[-1][0], index + 1)
        else:
            spans.append((index, index + 1))
    return spans


def _repetition_diagnostics(tokens: tuple[TextToken, ...]) -> tuple[float, list[str]]:
    values = [token.accentless for token in tokens]
    involved: set[int] = set()
    phrases: list[str] = []
    for size in range(2, 7):
        cursor = 0
        while cursor + size * 2 <= len(values):
            phrase = values[cursor : cursor + size]
            repeats = 1
            while values[cursor + repeats * size : cursor + (repeats + 1) * size] == phrase:
                repeats += 1
            if repeats >= 2:
                involved.update(range(cursor + size, cursor + repeats * size))
                rendered = " ".join(token.text for token in tokens[cursor : cursor + size])
                if rendered not in phrases:
                    phrases.append(rendered)
                cursor += repeats * size
            else:
                cursor += 1
    return len(involved) / len(tokens) if tokens else 0.0, phrases[:10]


def _out_of_order_count(reference: tuple[TextToken, ...], hypothesis: tuple[TextToken, ...]) -> int:
    if len(reference) < 6 or len(hypothesis) < 6:
        return 0
    ref_values = [token.accentless for token in reference]
    hyp_values = [token.accentless for token in hypothesis]
    positions: list[tuple[int, int]] = []
    for index in range(len(ref_values) - 2):
        phrase = ref_values[index : index + 3]
        if ref_values.count(phrase[0]) > max(3, len(ref_values) // 4):
            continue
        matches = [
            position
            for position in range(len(hyp_values) - 2)
            if hyp_values[position : position + 3] == phrase
        ]
        if len(matches) == 1:
            positions.append((index, matches[0]))
    return sum(
        left_ref < right_ref and left_hyp > right_hyp
        for (left_ref, left_hyp), (right_ref, right_hyp) in pairwise(positions)
    )


def unavailable_content_report(
    thresholds: ContentCoverageThresholds | None = None,
    *,
    reason: str = "asr_unavailable",
) -> ContentCoverageReport:
    policy = thresholds or ContentCoverageThresholds()
    return ContentCoverageReport(
        status="unavailable",
        reasons=(reason,),
        metrics={"asr_confidence_available": False},
        policy_version=CONTENT_COVERAGE_VERSION,
        policy_identity=policy.identity,
    )


def analyze_content_coverage(
    reference_text: str,
    hypothesis_text: str,
    *,
    thresholds: ContentCoverageThresholds | None = None,
) -> ContentCoverageReport:
    policy = thresholds or ContentCoverageThresholds()
    reference = tokenize(reference_text)
    hypothesis = tokenize(hypothesis_text)
    if not reference:
        raise ValueError("content coverage reference must contain speech tokens")
    operations = _token_edit_operations(reference, hypothesis)
    counts = {
        name: sum(kind == name for kind, _, _ in operations)
        for name in ("match", "pronunciation_variant", "substitution", "deletion", "insertion")
    }
    matched = counts["match"] + counts["pronunciation_variant"]
    missing_spans = _missing_spans(operations, reference)
    longest = max(missing_spans, key=lambda item: item[1] - item[0], default=(0, 0))
    longest_count = longest[1] - longest[0]
    trailing_count = (
        len(reference) - missing_spans[-1][0]
        if missing_spans and missing_spans[-1][1] == len(reference)
        else 0
    )
    repetition_ratio, repeated_phrases = _repetition_diagnostics(hypothesis)
    out_of_order = _out_of_order_count(reference, hypothesis)
    deletion_ratio = counts["deletion"] / len(reference)
    insertion_ratio = counts["insertion"] / len(reference)
    coverage_ratio = matched / len(reference)
    normalized_reference = " ".join(token.accentless for token in reference)
    normalized_hypothesis = " ".join(token.accentless for token in hypothesis)
    character_error_rate = _edit_distance(normalized_reference, normalized_hypothesis) / max(
        len(normalized_reference), 1
    )
    word_error_rate = (counts["substitution"] + counts["deletion"] + counts["insertion"]) / len(
        reference
    )
    premature = trailing_count >= 3 and trailing_count / len(reference) >= 0.10
    reasons: list[str] = []
    substitution_ratio = counts["substitution"] / len(reference)
    if coverage_ratio < policy.minimum_coverage_ratio and (
        deletion_ratio > 0.05 or substitution_ratio > 0.40
    ):
        reasons.append("coverage_ratio_below_threshold")
    if deletion_ratio > policy.maximum_deletion_ratio:
        reasons.append("deletion_ratio_above_threshold")
    if insertion_ratio > policy.maximum_insertion_ratio:
        reasons.append("insertion_ratio_above_threshold")
    if repetition_ratio > policy.maximum_repetition_ratio:
        reasons.append("repetition_ratio_above_threshold")
    if longest_count >= policy.maximum_missing_contiguous_tokens:
        reasons.append("longest_missing_span_above_threshold")
    if trailing_count >= policy.maximum_trailing_missing_tokens or premature:
        reasons.append("premature_ending_or_trailing_content_missing")
    if out_of_order:
        reasons.append("obvious_out_of_order_segments")
    if reasons:
        disagreement_classification = "model_content_failure_candidate"
    elif counts["pronunciation_variant"] or counts["substitution"]:
        disagreement_classification = "pronunciation_or_asr_disagreement"
    else:
        disagreement_classification = "content_match"
    return ContentCoverageReport(
        status="rejected" if reasons else "passed",
        reasons=tuple(reasons),
        metrics={
            "reference_token_count": len(reference),
            "hypothesis_token_count": len(hypothesis),
            "normalized_wer": round(word_error_rate, 6),
            "normalized_cer": round(character_error_rate, 6),
            "deletion_ratio": round(deletion_ratio, 6),
            "insertion_ratio": round(insertion_ratio, 6),
            "repetition_ratio": round(repetition_ratio, 6),
            "coverage_ratio": round(coverage_ratio, 6),
            "substitution_count": counts["substitution"],
            "deletion_count": counts["deletion"],
            "insertion_count": counts["insertion"],
            "pronunciation_variant_match_count": counts["pronunciation_variant"],
            "longest_missing_contiguous_token_count": longest_count,
            "longest_missing_contiguous_text": " ".join(
                token.text for token in reference[longest[0] : longest[1]]
            ),
            "trailing_missing_token_count": trailing_count,
            "premature_ending": premature,
            "obvious_out_of_order_segment_count": out_of_order,
            "suspicious_repeated_phrases": repeated_phrases,
            "asr_confidence_available": False,
            "comparison_basis": "actual_synthesis_text_asr_normalized",
            "disagreement_classification": disagreement_classification,
        },
        policy_version=CONTENT_COVERAGE_VERSION,
        policy_identity=policy.identity,
    )
