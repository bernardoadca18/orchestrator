"""Deterministic text/ASR alignment primitives for narration time.

The implementation intentionally operates per editorial block. Sequence
alignment uses two score rows plus a byte-per-cell traceback matrix: O(n*m)
time, O(n*m) compact traceback memory and O(m) floating-point score memory.
Explicit guards prevent pathological script blocks from exhausting a worker.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

TOKEN_PATTERN = re.compile(r"\w+(?:['\u2019]\w+)*", re.UNICODE)
APOSTROPHE_TRANSLATION = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'"})


class AlignmentAlgorithmError(ValueError):
    """Raised when alignment inputs are invalid or exceed safe bounds."""


@dataclass(frozen=True)
class TextToken:
    text: str
    normalized: str
    accentless: str
    start_char: int
    end_char: int
    order: int


@dataclass(frozen=True)
class TimedWord:
    word: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class BlockRange:
    order: int
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class SequenceAlignment:
    source_to_target: tuple[int | None, ...]
    matched_count: int
    matched_ratio: float


@dataclass(frozen=True)
class ExcerptMatch:
    status: str
    method: str | None
    score: float
    start_char: int | None
    end_char: int | None
    token_indexes: tuple[int, ...]
    reason: str | None = None


@dataclass(frozen=True)
class BlockTextAlignment:
    canonical_tokens: tuple[TextToken, ...]
    synthesis_tokens: tuple[TextToken, ...]
    asr_words: tuple[TimedWord, ...]
    canonical_to_synthesis: tuple[int | None, ...]
    synthesis_to_asr_word: tuple[int | None, ...]
    canonical_to_asr_word: tuple[int | None, ...]
    canonical_synthesis_coverage: float
    synthesis_asr_coverage: float
    overall_coverage: float


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value).translate(APOSTROPHE_TRANSLATION)
    return value.casefold()


def _accentless(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize(text: str) -> tuple[TextToken, ...]:
    """Tokenize words while retaining exact offsets into the untouched text."""
    tokens: list[TextToken] = []
    for order, match in enumerate(TOKEN_PATTERN.finditer(text)):
        original = match.group(0)
        normalized = _normalized(original)
        tokens.append(
            TextToken(
                text=original,
                normalized=normalized,
                accentless=_accentless(normalized),
                start_char=match.start(),
                end_char=match.end(),
                order=order,
            )
        )
    return tuple(tokens)


def token_similarity(left: TextToken, right: TextToken) -> float:
    if unicodedata.normalize("NFC", left.text) == unicodedata.normalize("NFC", right.text):
        return 1.0
    if left.normalized == right.normalized:
        return 0.98
    if left.accentless == right.accentless:
        return 0.92
    ratio = SequenceMatcher(None, left.accentless, right.accentless, autojunk=False).ratio()
    return ratio * 0.88 if ratio >= 0.78 else 0.0


def align_token_sequences(
    source: tuple[TextToken, ...],
    target: tuple[TextToken, ...],
    *,
    max_tokens: int = 2_500,
    max_cells: int = 2_000_000,
    minimum_pair_score: float = 0.68,
) -> SequenceAlignment:
    """Globally align token sequences with deterministic gap/substitution handling."""
    n, m = len(source), len(target)
    if n > max_tokens or m > max_tokens or n * m > max_cells:
        raise AlignmentAlgorithmError("text block exceeds deterministic alignment limits")
    if not source:
        return SequenceAlignment((), 0, 1.0)
    if not target:
        return SequenceAlignment((None,) * n, 0, 0.0)

    gap = -0.8
    previous = [j * gap for j in range(m + 1)]
    trace = [bytearray(m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        trace[0][j] = 2  # left
    for i in range(1, n + 1):
        current = [i * gap] + [0.0] * m
        trace[i][0] = 1  # up
        for j in range(1, m + 1):
            similarity = token_similarity(source[i - 1], target[j - 1])
            diagonal = previous[j - 1] + (2.0 * similarity if similarity else -1.1)
            up = previous[j] + gap
            left = current[j - 1] + gap
            # Stable tie order: diagonal, then source deletion, then target insertion.
            if diagonal >= up and diagonal >= left:
                current[j] = diagonal
                trace[i][j] = 0
            elif up >= left:
                current[j] = up
                trace[i][j] = 1
            else:
                current[j] = left
                trace[i][j] = 2
        previous = current

    mapping: list[int | None] = [None] * n
    i, j = n, m
    while i or j:
        direction = trace[i][j]
        if i and j and direction == 0:
            if token_similarity(source[i - 1], target[j - 1]) >= minimum_pair_score:
                mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif i and (not j or direction == 1):
            i -= 1
        else:
            j -= 1
    matched = sum(value is not None for value in mapping)
    return SequenceAlignment(tuple(mapping), matched, matched / n)


def validate_timed_words(
    words: tuple[TimedWord, ...],
    *,
    duration_seconds: float,
    tolerance_seconds: float = 0.25,
    max_words: int = 250_000,
) -> None:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise AlignmentAlgorithmError("final narration duration is invalid")
    if len(words) > max_words:
        raise AlignmentAlgorithmError("transcription contains too many words")
    previous_start = -1.0
    for word in words:
        if not word.word.strip():
            raise AlignmentAlgorithmError("transcription contains an empty word")
        if not math.isfinite(word.start_seconds) or not math.isfinite(word.end_seconds):
            raise AlignmentAlgorithmError("transcription contains non-finite timestamps")
        if word.start_seconds < 0 or word.end_seconds < word.start_seconds:
            raise AlignmentAlgorithmError("transcription contains an invalid timestamp range")
        if word.start_seconds + tolerance_seconds < previous_start:
            raise AlignmentAlgorithmError("transcription words are not ordered by start time")
        if word.end_seconds > duration_seconds + tolerance_seconds:
            raise AlignmentAlgorithmError("transcription word exceeds final narration duration")
        previous_start = word.start_seconds


def partition_words_by_block(
    words: tuple[TimedWord, ...],
    blocks: tuple[BlockRange, ...],
    *,
    tolerance_seconds: float = 0.05,
) -> dict[int, tuple[TimedWord, ...]]:
    """Assign each ASR word to at most one block using midpoint then overlap."""
    ordered = sorted(blocks, key=lambda block: (block.start_seconds, block.order))
    for index, block in enumerate(ordered):
        if (
            not math.isfinite(block.start_seconds)
            or not math.isfinite(block.end_seconds)
            or block.start_seconds < 0
            or block.end_seconds < block.start_seconds
        ):
            raise AlignmentAlgorithmError("audio block has invalid narration offsets")
        if index and block.start_seconds < ordered[index - 1].end_seconds - tolerance_seconds:
            raise AlignmentAlgorithmError("audio block narration ranges overlap")

    assigned: dict[int, list[TimedWord]] = {block.order: [] for block in blocks}
    for word in words:
        midpoint = (word.start_seconds + word.end_seconds) / 2
        containing = [
            block
            for block in ordered
            if block.start_seconds <= midpoint < block.end_seconds
            or (
                block is ordered[-1]
                and math.isclose(midpoint, block.end_seconds, abs_tol=tolerance_seconds)
            )
        ]
        if containing:
            chosen = containing[0]
        else:
            overlapping: list[tuple[float, float, int, BlockRange]] = []
            for block in ordered:
                overlap = max(
                    0.0,
                    min(word.end_seconds, block.end_seconds + tolerance_seconds)
                    - max(word.start_seconds, block.start_seconds - tolerance_seconds),
                )
                if overlap > 0:
                    center_distance = abs(midpoint - (block.start_seconds + block.end_seconds) / 2)
                    overlapping.append((-overlap, center_distance, block.order, block))
            if not overlapping:
                continue
            chosen = min(overlapping)[-1]
        assigned[chosen.order].append(word)
    return {order: tuple(values) for order, values in assigned.items()}


def _asr_tokens(words: tuple[TimedWord, ...]) -> tuple[tuple[TextToken, ...], tuple[int, ...]]:
    tokens: list[TextToken] = []
    word_indexes: list[int] = []
    for word_index, word in enumerate(words):
        for token in tokenize(word.word):
            tokens.append(
                TextToken(
                    text=token.text,
                    normalized=token.normalized,
                    accentless=token.accentless,
                    start_char=token.start_char,
                    end_char=token.end_char,
                    order=len(tokens),
                )
            )
            word_indexes.append(word_index)
    return tuple(tokens), tuple(word_indexes)


def build_block_text_alignment(
    canonical_text: str,
    synthesis_text: str,
    asr_words: tuple[TimedWord, ...],
    *,
    max_tokens: int = 2_500,
    max_cells: int = 2_000_000,
) -> BlockTextAlignment:
    canonical = tokenize(canonical_text)
    synthesis = tokenize(synthesis_text)
    canonical_synthesis = align_token_sequences(
        canonical, synthesis, max_tokens=max_tokens, max_cells=max_cells
    )
    asr_tokens, asr_word_indexes = _asr_tokens(asr_words)
    synthesis_asr = align_token_sequences(
        synthesis, asr_tokens, max_tokens=max_tokens, max_cells=max_cells
    )
    synthesis_to_word = tuple(
        asr_word_indexes[index] if index is not None else None
        for index in synthesis_asr.source_to_target
    )
    canonical_to_word: list[int | None] = []
    for synthesis_index in canonical_synthesis.source_to_target:
        canonical_to_word.append(
            synthesis_to_word[synthesis_index] if synthesis_index is not None else None
        )
    overall_matches = sum(index is not None for index in canonical_to_word)
    overall = overall_matches / len(canonical) if canonical else 1.0
    return BlockTextAlignment(
        canonical_tokens=canonical,
        synthesis_tokens=synthesis,
        asr_words=asr_words,
        canonical_to_synthesis=canonical_synthesis.source_to_target,
        synthesis_to_asr_word=synthesis_to_word,
        canonical_to_asr_word=tuple(canonical_to_word),
        canonical_synthesis_coverage=canonical_synthesis.matched_ratio,
        synthesis_asr_coverage=synthesis_asr.matched_ratio,
        overall_coverage=overall,
    )


def _all_literal_spans(text: str, excerpt: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = 0
    while True:
        position = text.find(excerpt, position)
        if position < 0:
            return spans
        spans.append((position, position + len(excerpt)))
        position += max(1, len(excerpt))


def _tokens_for_span(tokens: tuple[TextToken, ...], start: int, end: int) -> tuple[int, ...]:
    return tuple(
        token.order for token in tokens if token.end_char > start and token.start_char < end
    )


def _choose_exact_candidate(
    candidates: list[tuple[int, int]],
    tokens: tuple[TextToken, ...],
    *,
    method: str,
    score: float,
    after_char: int | None,
) -> ExcerptMatch:
    if after_char is not None:
        monotonic = [candidate for candidate in candidates if candidate[0] >= after_char]
        if len(monotonic) == 1:
            candidates = monotonic
    if len(candidates) != 1:
        return ExcerptMatch("ambiguous", method, score, None, None, (), "excerpt repeats in block")
    start, end = candidates[0]
    return ExcerptMatch("matched", method, score, start, end, _tokens_for_span(tokens, start, end))


def match_excerpt(
    canonical_text: str,
    excerpt: str | None,
    *,
    after_char: int | None = None,
    fuzzy_threshold: float = 0.72,
    ambiguity_delta: float = 0.03,
    max_fuzzy_candidates: int = 8_000,
) -> ExcerptMatch:
    """Locate an excerpt inside its owning canonical block, exact-first."""
    if excerpt is None or not excerpt.strip():
        return ExcerptMatch("not_applicable", None, 0.0, None, None, (), "no narration excerpt")
    canonical_tokens = tokenize(canonical_text)
    excerpt_tokens = tokenize(excerpt)
    if not excerpt_tokens or not canonical_tokens:
        return ExcerptMatch("unresolved", None, 0.0, None, None, (), "excerpt has no tokens")

    literal = _all_literal_spans(canonical_text, excerpt)
    if literal:
        return _choose_exact_candidate(
            literal, canonical_tokens, method="literal", score=1.0, after_char=after_char
        )

    def token_candidates(*, accentless: bool) -> list[tuple[int, int]]:
        source = [
            token.accentless if accentless else token.normalized for token in canonical_tokens
        ]
        query = [token.accentless if accentless else token.normalized for token in excerpt_tokens]
        width = len(query)
        return [
            (canonical_tokens[index].start_char, canonical_tokens[index + width - 1].end_char)
            for index in range(len(source) - width + 1)
            if source[index : index + width] == query
        ]

    normalized = token_candidates(accentless=False)
    if normalized:
        return _choose_exact_candidate(
            normalized, canonical_tokens, method="normalized", score=0.98, after_char=after_char
        )
    accent_normalized = token_candidates(accentless=True)
    if accent_normalized:
        return _choose_exact_candidate(
            accent_normalized,
            canonical_tokens,
            method="accent_insensitive",
            score=0.94,
            after_char=after_char,
        )

    query_length = len(excerpt_tokens)
    minimum_width = max(1, query_length - 2)
    maximum_width = min(len(canonical_tokens), query_length + max(2, query_length // 2))
    candidates: list[tuple[float, int, int]] = []
    candidate_count = 0
    for width in range(minimum_width, maximum_width + 1):
        for start in range(len(canonical_tokens) - width + 1):
            candidate_count += 1
            if candidate_count > max_fuzzy_candidates:
                raise AlignmentAlgorithmError("excerpt fuzzy search exceeds candidate limit")
            window = canonical_tokens[start : start + width]
            alignment = align_token_sequences(
                excerpt_tokens,
                window,
                max_tokens=2_500,
                max_cells=2_000_000,
            )
            similarities = [
                token_similarity(excerpt_tokens[index], window[target])
                for index, target in enumerate(alignment.source_to_target)
                if target is not None
            ]
            similarity_sum = sum(similarities)
            score = 0.7 * (similarity_sum / max(query_length, width)) + 0.3 * (
                alignment.matched_count / query_length
            )
            if score:
                candidates.append((score, start, start + width))
    if not candidates:
        return ExcerptMatch("unresolved", "fuzzy", 0.0, None, None, (), "excerpt not found")
    candidates.sort(key=lambda item: (-item[0], abs((item[2] - item[1]) - query_length), item[1]))
    best_score, best_start, best_end = candidates[0]
    if best_score < fuzzy_threshold:
        return ExcerptMatch(
            "unresolved", "fuzzy", best_score, None, None, (), "fuzzy score below threshold"
        )

    best_indexes = set(range(best_start, best_end))
    competing = []
    for score, start, end in candidates[1:]:
        if best_score - score > ambiguity_delta:
            break
        indexes = set(range(start, end))
        overlap = len(best_indexes & indexes) / max(1, len(best_indexes | indexes))
        if overlap < 0.5:
            competing.append((score, start, end))
    if competing:
        possible = [(best_score, best_start, best_end), *competing]
        if after_char is not None:
            monotonic = [
                item for item in possible if canonical_tokens[item[1]].start_char >= after_char
            ]
            if len(monotonic) == 1:
                best_score, best_start, best_end = monotonic[0]
            else:
                return ExcerptMatch(
                    "ambiguous", "fuzzy", best_score, None, None, (), "multiple fuzzy matches"
                )
        else:
            return ExcerptMatch(
                "ambiguous", "fuzzy", best_score, None, None, (), "multiple fuzzy matches"
            )
    start_char = canonical_tokens[best_start].start_char
    end_char = canonical_tokens[best_end - 1].end_char
    return ExcerptMatch(
        "matched",
        "fuzzy",
        best_score,
        start_char,
        end_char,
        tuple(range(best_start, best_end)),
    )


def resolve_match_timestamps(
    match: ExcerptMatch,
    alignment: BlockTextAlignment,
    *,
    minimum_coverage: float = 0.60,
) -> tuple[str, float | None, float | None, float, str | None]:
    if match.status != "matched":
        return match.status, None, None, 0.0, match.reason
    if not match.token_indexes:
        return "unresolved", None, None, 0.0, "matched excerpt has no canonical tokens"
    word_indexes = [
        alignment.canonical_to_asr_word[index]
        for index in match.token_indexes
        if index < len(alignment.canonical_to_asr_word)
        and alignment.canonical_to_asr_word[index] is not None
    ]
    mapped_canonical = sum(
        index < len(alignment.canonical_to_asr_word)
        and alignment.canonical_to_asr_word[index] is not None
        for index in match.token_indexes
    )
    coverage = mapped_canonical / len(match.token_indexes)
    if not word_indexes or coverage < minimum_coverage:
        return "unresolved", None, None, coverage, "speech alignment coverage below threshold"
    unique_indexes = sorted(set(int(index) for index in word_indexes if index is not None))
    selected = [alignment.asr_words[index] for index in unique_indexes]
    return (
        "resolved",
        min(word.start_seconds for word in selected),
        max(word.end_seconds for word in selected),
        coverage,
        None,
    )
