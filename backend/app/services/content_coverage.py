"""Structural ASR evidence for narration content integrity.

WER and CER remain diagnostics. Blocking decisions require structural evidence:
deletion runs, missing sentences, premature endings, repetition, or reordering.
Localized substitutions are retained as pronunciation/ASR diagnostics and do
not, by themselves, prove that speech is missing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Literal

from app.services.narration_alignment_algorithm import TextToken, token_similarity, tokenize
from app.services.text_normalization import PortugueseTextNormalizer

CONTENT_COVERAGE_VERSION = "narration_content_coverage_v2"
CONTENT_COMPARISON_NORMALIZATION_VERSION = "narration_content_comparison_pt_br_v1"
_COMPARISON_NORMALIZER = PortugueseTextNormalizer(version=CONTENT_COMPARISON_NORMALIZATION_VERSION)
_SENTENCE_PATTERN = re.compile(r"[^.!?]+(?:[.!?]+|$)", re.UNICODE)

ContentClassification = Literal[
    "content_complete",
    "pronunciation_or_asr_disagreement",
    "content_inconclusive",
    "probable_content_loss",
    "premature_end",
    "probable_repetition",
    "probable_reordering",
]


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
            {
                "version": CONTENT_COVERAGE_VERSION,
                "comparison_normalization_version": (CONTENT_COMPARISON_NORMALIZATION_VERSION),
                "comparison_normalization_identity": _COMPARISON_NORMALIZER.identity,
                **asdict(self),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ContentCoverageReport:
    status: Literal["passed", "rejected", "unavailable"]
    classification: ContentClassification
    severity: Literal["none", "warning", "blocking"]
    retry_required: bool
    reasons: tuple[str, ...]
    metrics: dict[str, object]
    policy_version: str
    policy_identity: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "classification": self.classification,
            "severity": self.severity,
            "retry_required": self.retry_required,
            "retry_reason": self.classification if self.retry_required else None,
            "reasons": list(self.reasons),
            "metrics": self.metrics,
            "policy_version": self.policy_version,
            "policy_identity": self.policy_identity,
        }


def normalize_content_comparison_text(value: str) -> str:
    """Normalize representation only; canonical and synthesis text stay immutable."""
    return _COMPARISON_NORMALIZER.normalize(value).normalized_text


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


def _deletion_spans(
    operations: list[tuple[str, int | None, int | None]],
) -> list[tuple[int, int]]:
    """Return genuinely absent reference runs; substitutions are not deletions."""
    missing = sorted(
        ref_index
        for kind, ref_index, _ in operations
        if ref_index is not None and kind == "deletion"
    )
    spans: list[tuple[int, int]] = []
    for index in missing:
        if spans and index == spans[-1][1]:
            spans[-1] = (spans[-1][0], index + 1)
        else:
            spans.append((index, index + 1))
    return spans


def _sentence_diagnostics(
    text: str,
    reference: tuple[TextToken, ...],
    deleted_indexes: set[int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    sentences: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []
    for sentence_index, sentence_match in enumerate(_SENTENCE_PATTERN.finditer(text)):
        token_indexes = [
            index
            for index, token in enumerate(reference)
            if sentence_match.start() <= token.start_char < sentence_match.end()
        ]
        if not token_indexes:
            continue
        deleted = sum(index in deleted_indexes for index in token_indexes)
        structural_coverage = 1.0 - deleted / len(token_indexes)
        diagnostic: dict[str, object] = {
            "sentence_index": sentence_index,
            "token_count": len(token_indexes),
            "deleted_token_count": deleted,
            "structural_coverage_ratio": round(structural_coverage, 6),
        }
        sentences.append(diagnostic)
        if len(token_indexes) >= 3 and deleted >= 3 and structural_coverage <= 0.35:
            missing.append(diagnostic)
    return sentences, missing


def _region_structural_coverage(
    reference_count: int, deleted_indexes: set[int]
) -> dict[str, float]:
    result: dict[str, float] = {}
    labels = ("beginning", "middle", "ending")
    for region, label in enumerate(labels):
        start = reference_count * region // 3
        end = reference_count * (region + 1) // 3
        size = end - start
        deleted = sum(index in deleted_indexes for index in range(start, end))
        result[label] = round(1.0 - deleted / size, 6) if size else 1.0
    return result


def _disagreement_diagnostics(
    operations: list[tuple[str, int | None, int | None]],
    reference: tuple[TextToken, ...],
    hypothesis: tuple[TextToken, ...],
) -> list[dict[str, object]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    scores: dict[tuple[str, str, str], float] = {}
    for kind, ref_index, hyp_index in operations:
        if kind not in {"pronunciation_variant", "substitution"}:
            continue
        assert ref_index is not None and hyp_index is not None
        expected = reference[ref_index].text
        observed = hypothesis[hyp_index].text
        key = (expected, observed, kind)
        counter[key] += 1
        scores[key] = token_similarity(reference[ref_index], hypothesis[hyp_index])
    return [
        {
            "expected": expected,
            "observed": observed,
            "kind": kind,
            "occurrences": count,
            "token_similarity": round(scores[(expected, observed, kind)], 6),
            "review_status": "pending_manual_review",
            "auto_promoted": False,
        }
        for (expected, observed, kind), count in sorted(
            counter.items(), key=lambda item: (-item[1], item[0])
        )[:25]
    ]


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
    ref_ngrams = [tuple(ref_values[index : index + 3]) for index in range(len(ref_values) - 2)]
    hyp_ngrams = [tuple(hyp_values[index : index + 3]) for index in range(len(hyp_values) - 2)]
    ref_counts = Counter(ref_ngrams)
    hyp_counts = Counter(hyp_ngrams)
    hypothesis_positions = {
        phrase: index for index, phrase in enumerate(hyp_ngrams) if hyp_counts[phrase] == 1
    }
    ordered_hypothesis_positions = [
        hypothesis_positions[phrase]
        for phrase in ref_ngrams
        if ref_counts[phrase] == 1 and phrase in hypothesis_positions
    ]
    if len(ordered_hypothesis_positions) < 4:
        return 0

    # Count anchors outside the longest monotonic subsequence. Local ASR
    # disagreement merely removes anchors; it does not manufacture reordering.
    tails: list[int] = []
    for position in ordered_hypothesis_positions:
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if tails[middle] < position:
                low = middle + 1
            else:
                high = middle
        if low == len(tails):
            tails.append(position)
        else:
            tails[low] = position
    return len(ordered_hypothesis_positions) - len(tails)


def unavailable_content_report(
    thresholds: ContentCoverageThresholds | None = None,
    *,
    reason: str = "asr_unavailable",
) -> ContentCoverageReport:
    policy = thresholds or ContentCoverageThresholds()
    return ContentCoverageReport(
        status="unavailable",
        classification="content_inconclusive",
        severity="warning",
        retry_required=False,
        reasons=(reason,),
        metrics={
            "asr_confidence_available": False,
            "comparison_normalization_version": CONTENT_COMPARISON_NORMALIZATION_VERSION,
            "comparison_normalization_identity": _COMPARISON_NORMALIZER.identity,
        },
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
    normalized_reference_text = normalize_content_comparison_text(reference_text)
    normalized_hypothesis_text = normalize_content_comparison_text(hypothesis_text)
    reference = tokenize(normalized_reference_text)
    hypothesis = tokenize(normalized_hypothesis_text)
    if not reference:
        raise ValueError("content coverage reference must contain speech tokens")
    operations = _token_edit_operations(reference, hypothesis)
    counts = {
        name: sum(kind == name for kind, _, _ in operations)
        for name in ("match", "pronunciation_variant", "substitution", "deletion", "insertion")
    }
    matched = counts["match"] + counts["pronunciation_variant"]
    deletion_spans = _deletion_spans(operations)
    deleted_indexes = {
        ref_index
        for kind, ref_index, _ in operations
        if kind == "deletion" and ref_index is not None
    }
    longest = max(deletion_spans, key=lambda item: item[1] - item[0], default=(0, 0))
    longest_count = longest[1] - longest[0]
    trailing_count = (
        len(reference) - deletion_spans[-1][0]
        if deletion_spans and deletion_spans[-1][1] == len(reference)
        else 0
    )
    repetition_ratio, repeated_phrases = _repetition_diagnostics(hypothesis)
    out_of_order = _out_of_order_count(reference, hypothesis)
    deletion_ratio = counts["deletion"] / len(reference)
    insertion_ratio = counts["insertion"] / len(reference)
    substitution_ratio = counts["substitution"] / len(reference)
    coverage_ratio = matched / len(reference)
    structural_coverage_ratio = (len(reference) - counts["deletion"]) / len(reference)
    normalized_reference = " ".join(token.accentless for token in reference)
    normalized_hypothesis = " ".join(token.accentless for token in hypothesis)
    character_error_rate = _edit_distance(normalized_reference, normalized_hypothesis) / max(
        len(normalized_reference), 1
    )
    word_error_rate = (counts["substitution"] + counts["deletion"] + counts["insertion"]) / len(
        reference
    )
    premature = trailing_count >= policy.maximum_trailing_missing_tokens or (
        trailing_count >= 3 and trailing_count / len(reference) >= 0.10
    )
    sentence_coverage, missing_sentences = _sentence_diagnostics(
        normalized_reference_text, reference, deleted_indexes
    )
    disagreement_diagnostics = _disagreement_diagnostics(operations, reference, hypothesis)

    reasons: list[str] = []
    if deletion_ratio > policy.maximum_deletion_ratio:
        reasons.append("deletion_ratio_above_threshold")
    if longest_count >= policy.maximum_missing_contiguous_tokens:
        reasons.append("longest_missing_span_above_threshold")
    if missing_sentences:
        reasons.append("sentence_structurally_missing")
    if premature:
        reasons.append("premature_ending_or_trailing_content_missing")
    if repetition_ratio > policy.maximum_repetition_ratio:
        reasons.append("repetition_ratio_above_threshold")
    if out_of_order >= 3:
        reasons.append("obvious_out_of_order_segments")

    if premature:
        classification: ContentClassification = "premature_end"
    elif repetition_ratio > policy.maximum_repetition_ratio:
        classification = "probable_repetition"
    elif out_of_order >= 3:
        classification = "probable_reordering"
    elif reasons:
        classification = "probable_content_loss"
    elif coverage_ratio < policy.minimum_coverage_ratio and substitution_ratio > 0.40:
        # A weak lexical anchor set cannot prove loss when the sequence remains
        # structurally aligned. Preserve uncertainty instead of spending GPU on
        # a blind semantic retry.
        classification = "content_inconclusive"
        reasons.append("insufficient_structural_anchor_evidence")
    elif disagreement_diagnostics or counts["insertion"]:
        classification = "pronunciation_or_asr_disagreement"
    else:
        classification = "content_complete"
    if insertion_ratio > policy.maximum_insertion_ratio and not (
        repetition_ratio > policy.maximum_repetition_ratio
    ):
        reasons.append("insertion_ratio_above_threshold_nonblocking")

    blocking = classification in {
        "probable_content_loss",
        "premature_end",
        "probable_repetition",
        "probable_reordering",
    }
    inconclusive = classification == "content_inconclusive"
    status: Literal["passed", "rejected", "unavailable"] = (
        "rejected" if blocking else "unavailable" if inconclusive else "passed"
    )
    severity: Literal["none", "warning", "blocking"] = (
        "blocking" if blocking else "none" if classification == "content_complete" else "warning"
    )
    return ContentCoverageReport(
        status=status,
        classification=classification,
        severity=severity,
        retry_required=blocking,
        reasons=tuple(reasons),
        metrics={
            "reference_token_count": len(reference),
            "hypothesis_token_count": len(hypothesis),
            "normalized_wer": round(word_error_rate, 6),
            "normalized_cer": round(character_error_rate, 6),
            "deletion_ratio": round(deletion_ratio, 6),
            "insertion_ratio": round(insertion_ratio, 6),
            "substitution_ratio": round(substitution_ratio, 6),
            "repetition_ratio": round(repetition_ratio, 6),
            "coverage_ratio": round(coverage_ratio, 6),
            "structural_coverage_ratio": round(structural_coverage_ratio, 6),
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
            "sentence_coverage": sentence_coverage,
            "structurally_missing_sentences": missing_sentences,
            "region_structural_coverage": _region_structural_coverage(
                len(reference), deleted_indexes
            ),
            "pronunciation_or_asr_mismatches": disagreement_diagnostics,
            "pronunciation_review_policy": "diagnostic_only_manual_promotion",
            "asr_confidence_available": False,
            "comparison_basis": "actual_synthesis_text_structural_asr_alignment",
            "comparison_normalization_version": CONTENT_COMPARISON_NORMALIZATION_VERSION,
            "comparison_normalization_identity": _COMPARISON_NORMALIZER.identity,
            "wer_cer_role": "diagnostic_only_not_an_isolated_gate",
            "disagreement_classification": classification,
        },
        policy_version=CONTENT_COVERAGE_VERSION,
        policy_identity=policy.identity,
    )
