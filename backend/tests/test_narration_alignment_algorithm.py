from __future__ import annotations

import pytest

from app.services.narration_alignment_algorithm import (
    AlignmentAlgorithmError,
    BlockRange,
    TimedWord,
    align_token_sequences,
    build_block_text_alignment,
    match_excerpt,
    partition_words_by_block,
    resolve_match_timestamps,
    tokenize,
    validate_timed_words,
)


def test_tokenization_normalizes_in_levels_without_losing_original_spans() -> None:
    text = "  GERALT—usa  Áard, Scoia\u2019tael e d'Água em Kaer-Morhen.  "
    tokens = tokenize(text)
    assert [token.text for token in tokens] == [
        "GERALT",
        "usa",
        "Áard",
        "Scoia\u2019tael",
        "e",
        "d'Água",
        "em",
        "Kaer",
        "Morhen",
    ]
    assert tokens[2].normalized == "áard"
    assert tokens[2].accentless == "aard"
    assert tokens[3].normalized == "scoia'tael"
    for token in tokens:
        assert text[token.start_char : token.end_char] == token.text


@pytest.mark.parametrize(
    ("canonical", "synthesis", "minimum_coverage"),
    [
        ("Geralt usa Aard.", "Geralt usa Árd.", 1.0),
        ("Kaer Morhen é apresentada.", "Kér Mórren é apresentada.", 1.0),
        ("um dois três", "um extra dois três", 1.0),
        ("um removido dois", "um dois", 2 / 3),
        ("versão 2007 chegou", "versão dois mil e sete chegou", 2 / 3),
    ],
)
def test_canonical_synthesis_alignment_handles_local_changes(
    canonical: str, synthesis: str, minimum_coverage: float
) -> None:
    result = align_token_sequences(tokenize(canonical), tokenize(synthesis))
    assert result.matched_ratio >= minimum_coverage


def test_entirely_incompatible_text_has_no_false_token_mapping() -> None:
    result = align_token_sequences(tokenize("Geralt encontra Ciri"), tokenize("Yennefer usa magia"))
    assert result.matched_count == 0


def test_synthesis_asr_alignment_maps_real_word_timestamps_despite_error() -> None:
    words = (
        TimedWord("Geralt", 10.0, 10.4),
        TimedWord("aprendeu", 10.5, 11.0),
        TimedWord("sinal", 11.1, 11.4),
        TimedWord("Arde", 11.5, 12.0),
    )
    alignment = build_block_text_alignment(
        "Geralt aprendeu o sinal Aard",
        "Geralt aprendeu o sinal Árd",
        words,
    )
    match = match_excerpt("Geralt aprendeu o sinal Aard", "Geralt aprende o sinal Aard")
    status, start, end, coverage, _ = resolve_match_timestamps(match, alignment)
    assert status == "resolved"
    assert start == 10.0
    assert end == 12.0
    assert coverage >= 0.8


def test_partition_uses_one_owner_for_boundary_overlap_and_preserves_silence() -> None:
    blocks = (BlockRange(0, 0.0, 2.0), BlockRange(1, 2.5, 5.0))
    words = (
        TimedWord("inside", 0.2, 0.5),
        TimedWord("edge", 1.95, 2.55),
        TimedWord("second", 2.6, 3.0),
        TimedWord("outside", 5.2, 5.3),
    )
    result = partition_words_by_block(words, blocks)
    flattened = [word for assigned in result.values() for word in assigned]
    assert flattened.count(words[1]) == 1
    assert words[0] in result[0]
    assert words[2] in result[1]
    assert words[3] not in flattened


def test_invalid_timestamp_payload_is_rejected() -> None:
    with pytest.raises(AlignmentAlgorithmError, match="ordered"):
        validate_timed_words(
            (TimedWord("later", 2.0, 2.1), TimedWord("earlier", 1.0, 1.1)),
            duration_seconds=3.0,
        )
    with pytest.raises(AlignmentAlgorithmError, match="exceeds"):
        validate_timed_words(
            (TimedWord("outside", 3.0, 3.5),),
            duration_seconds=3.0,
        )


def test_excerpt_matching_prefers_exact_then_normalized_then_fuzzy() -> None:
    canonical = "Quando Geralt aprende o primeiro sinal, Aard, tudo muda."
    literal = match_excerpt(canonical, "Geralt aprende o primeiro sinal")
    assert literal.status == "matched"
    assert literal.method == "literal"

    normalized = match_excerpt(canonical, "geralt aprende o primeiro sinal!")
    assert normalized.status == "matched"
    assert normalized.method == "normalized"

    fuzzy = match_excerpt(canonical, "quando Geralt aprende Aard")
    assert fuzzy.status == "matched"
    assert fuzzy.method == "fuzzy"
    assert fuzzy.score >= 0.72


def test_repeated_excerpt_is_ambiguous_until_order_disambiguates() -> None:
    canonical = "e isso muda tudo; depois e isso muda tudo novamente"
    first = match_excerpt(canonical, "e isso muda tudo")
    assert first.status == "ambiguous"
    ordered = match_excerpt(canonical, "e isso muda tudo", after_char=20)
    assert ordered.status == "matched"
    assert ordered.start_char is not None and ordered.start_char > 20


def test_unrelated_or_missing_excerpt_never_fabricates_a_match() -> None:
    canonical = "Geralt encontra Ciri em Novigrad."
    unrelated = match_excerpt(canonical, "Yennefer derrota um dragão ancestral")
    assert unrelated.status == "unresolved"
    assert match_excerpt(canonical, None).status == "not_applicable"
    assert match_excerpt(canonical, "  ").status == "not_applicable"


def test_alignment_guard_rejects_pathological_sequences() -> None:
    source = tokenize(" ".join(f"a{i}" for i in range(20)))
    target = tokenize(" ".join(f"a{i}" for i in range(20)))
    with pytest.raises(AlignmentAlgorithmError, match="limits"):
        align_token_sequences(source, target, max_cells=100)
