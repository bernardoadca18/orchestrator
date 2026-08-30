"""Versioned deterministic speech-quality policy over Stage 13 ASR evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.services.content_coverage import (
    ContentCoverageThresholds,
    analyze_content_coverage,
)


@dataclass(frozen=True)
class NarrationQualityDecision:
    status: Literal["healthy", "warning", "invalid"]
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


class NarrationSpeechQualityPolicy:
    def __init__(
        self,
        *,
        version: str,
        minimum_speech_coverage: float,
        max_repeated_ngram_occurrences: int,
        content_thresholds: ContentCoverageThresholds | None = None,
    ) -> None:
        if not version.strip() or not 0 <= minimum_speech_coverage <= 1:
            raise ValueError("invalid narration speech-quality policy")
        if max_repeated_ngram_occurrences < 2:
            raise ValueError("invalid repeated n-gram occurrence limit")
        self.version = version
        self.minimum_speech_coverage = minimum_speech_coverage
        self.hard_block_coverage = minimum_speech_coverage / 2
        self.max_repeated_ngram_occurrences = max_repeated_ngram_occurrences
        self.content_thresholds = content_thresholds or ContentCoverageThresholds()
        canonical = json.dumps(
            {
                "version": version,
                "minimum_speech_coverage": minimum_speech_coverage,
                "hard_block_coverage": self.hard_block_coverage,
                "max_repeated_ngram_occurrences": max_repeated_ngram_occurrences,
                "repetition_policy": "consecutive-ngram-at-least-12-tokens-v1",
                "content_coverage_policy_identity": self.content_thresholds.identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.identity = hashlib.sha256(canonical.encode()).hexdigest()

    def evaluate(
        self,
        block_alignments: Sequence[Any],
        asr_words: Sequence[str],
        *,
        synthesis_text: str | None = None,
        asr_text: str | None = None,
    ) -> NarrationQualityDecision:
        coverages = [float(block.synthesis_asr_coverage) for block in block_alignments]
        average = sum(coverages) / len(coverages) if coverages else 0.0
        no_words = sum(
            block.status == "no_words" and int(block.synthesis_token_count) > 0
            for block in block_alignments
        )
        catastrophic_blocks = sum(coverage < self.hard_block_coverage for coverage in coverages)
        repeated = self._has_catastrophic_repetition(asr_words)
        blocking_reasons: list[str] = []
        warning_reasons: list[str] = []
        if not block_alignments:
            blocking_reasons.append("no_alignment_blocks")
        if no_words:
            blocking_reasons.append("non_empty_block_without_asr_words")
        if repeated:
            blocking_reasons.append("catastrophic_repeated_asr_run")
        content = None
        if synthesis_text is not None and asr_text is not None:
            content = analyze_content_coverage(
                synthesis_text,
                asr_text,
                thresholds=self.content_thresholds,
            )
            if content.status == "rejected":
                blocking_reasons.append("content_coverage_rejected")
            elif content.classification != "content_complete":
                warning_reasons.append(f"content_{content.classification}")
        if catastrophic_blocks:
            target = (
                blocking_reasons
                if content is None
                else warning_reasons
            )
            target.append("catastrophic_block_coverage")
        if coverages and average < self.minimum_speech_coverage:
            target = (
                blocking_reasons
                if content is None
                else warning_reasons
            )
            target.append("average_synthesis_asr_coverage_below_threshold")
        if blocking_reasons:
            status: Literal["healthy", "warning", "invalid"] = "invalid"
        elif warning_reasons or any(
            coverage < self.minimum_speech_coverage for coverage in coverages
        ):
            status = "warning"
            if not warning_reasons:
                warning_reasons.append("isolated_low_coverage_block")
        else:
            status = "healthy"
        reasons = (*blocking_reasons, *warning_reasons)
        return NarrationQualityDecision(
            status=status,
            reasons=reasons,
            metrics={
                "block_count": len(block_alignments),
                "average_synthesis_asr_coverage": round(average, 6),
                "minimum_block_synthesis_asr_coverage": (
                    round(min(coverages), 6) if coverages else 0.0
                ),
                "no_word_block_count": no_words,
                "catastrophic_block_count": catastrophic_blocks,
                "catastrophic_repetition": repeated,
                "blocking_reasons": blocking_reasons,
                "warning_reasons": warning_reasons,
                "content_coverage": content.to_dict() if content is not None else None,
            },
            policy_version=self.version,
            policy_identity=self.identity,
        )

    def _has_catastrophic_repetition(self, words: Sequence[str]) -> bool:
        normalized = [
            token for word in words if (token := re.sub(r"[^\wÀ-ÿ]+", "", word.casefold()))
        ]
        for size in range(1, 6):
            for start in range(0, len(normalized) - size + 1):
                phrase = normalized[start : start + size]
                occurrences = 1
                cursor = start + size
                while normalized[cursor : cursor + size] == phrase:
                    occurrences += 1
                    cursor += size
                if occurrences >= self.max_repeated_ngram_occurrences and occurrences * size >= 12:
                    return True
        return False
