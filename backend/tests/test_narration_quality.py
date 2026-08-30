from __future__ import annotations

from types import SimpleNamespace

from app.services.narration_quality import NarrationSpeechQualityPolicy


def _block(coverage: float, *, status: str = "aligned", words: int = 10):
    return SimpleNamespace(
        status=status,
        synthesis_token_count=10,
        synthesis_asr_coverage=coverage,
        asr_word_count=words,
    )


def _policy() -> NarrationSpeechQualityPolicy:
    return NarrationSpeechQualityPolicy(
        version="narration_speech_quality_v1",
        minimum_speech_coverage=0.60,
        max_repeated_ngram_occurrences=4,
    )


def test_good_coverage_and_pronunciation_only_difference_pass() -> None:
    decision = _policy().evaluate(
        [_block(0.82), _block(0.74)],
        ["Geralt", "usa", "Arde", "em", "Kaer", "Morhen"],
    )
    assert decision.status == "healthy"


def test_missing_speech_is_invalid() -> None:
    decision = _policy().evaluate([_block(0.0, status="no_words", words=0)], [])
    assert decision.status == "invalid"
    assert "non_empty_block_without_asr_words" in decision.reasons


def test_catastrophic_low_coverage_is_invalid() -> None:
    decision = _policy().evaluate([_block(0.2), _block(0.25)], ["fala", "parcial"])
    assert decision.status == "invalid"
    assert "catastrophic_block_coverage" in decision.reasons


def test_low_lexical_alignment_with_local_pronunciation_evidence_is_warning() -> None:
    decision = _policy().evaluate(
        [_block(0.2)],
        ["Os", "vaículos", "usam", "o", "môtór"],
        synthesis_text="Os veículos usam o motor.",
        asr_text="Os vaículos usam o môtór.",
    )

    assert decision.status == "warning"
    content = decision.metrics["content_coverage"]
    assert isinstance(content, dict)
    assert content["classification"] == "pronunciation_or_asr_disagreement"
    assert content["retry_required"] is False


def test_structural_missing_sentence_remains_invalid_with_good_average_alignment() -> None:
    decision = _policy().evaluate(
        [_block(0.8)],
        ["A", "primeira", "frase", "A", "terceira", "frase"],
        synthesis_text=(
            "A primeira frase apresenta o contexto. "
            "A segunda frase contém detalhes realmente importantes. "
            "A terceira frase encerra a conclusão."
        ),
        asr_text=(
            "A primeira frase apresenta o contexto. "
            "A terceira frase encerra a conclusão."
        ),
    )

    assert decision.status == "invalid"
    assert "content_coverage_rejected" in decision.reasons


def test_obvious_repeated_asr_run_is_invalid() -> None:
    words = ["a", "mesma", "frase"] * 4
    decision = _policy().evaluate([_block(1.0)], words)
    assert decision.status == "invalid"
    assert "catastrophic_repeated_asr_run" in decision.reasons


def test_policy_identity_tracks_threshold_semantics() -> None:
    baseline = _policy()
    changed = NarrationSpeechQualityPolicy(
        version="narration_speech_quality_v1",
        minimum_speech_coverage=0.65,
        max_repeated_ngram_occurrences=4,
    )
    assert baseline.identity != changed.identity
