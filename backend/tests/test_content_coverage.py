from __future__ import annotations

from app.services.content_coverage import (
    analyze_content_coverage,
    unavailable_content_report,
)


def test_complete_speech_passes_and_reports_normalized_metrics() -> None:
    report = analyze_content_coverage(
        "Esta é uma narração completa sobre tecnologia e produção de vídeo.",
        "Esta e uma narracao completa sobre tecnologia e producao de video.",
    )
    assert report.status == "passed"
    assert report.metrics["coverage_ratio"] == 1.0
    assert report.metrics["deletion_ratio"] == 0.0


def test_missing_final_phrase_is_a_premature_ending() -> None:
    report = analyze_content_coverage(
        "A história começa com calma e termina explicando todos os detalhes importantes.",
        "A história começa com calma e termina",
    )
    assert report.status == "rejected"
    assert report.metrics["premature_ending"] is True
    assert "premature_ending_or_trailing_content_missing" in report.reasons


def test_missing_middle_phrase_reports_longest_contiguous_span() -> None:
    report = analyze_content_coverage(
        "Primeiro apresentamos o contexto depois explicamos seis detalhes muito importantes "
        "e finalmente concluímos o assunto.",
        "Primeiro apresentamos o contexto e finalmente concluímos o assunto.",
    )
    assert report.status == "rejected"
    assert report.metrics["longest_missing_contiguous_token_count"] >= 6
    assert "detalhes" in str(report.metrics["longest_missing_contiguous_text"])


def test_repeated_phrase_and_large_insertion_are_rejected() -> None:
    reference = "O sistema valida conteúdo antes de publicar o áudio final."
    hypothesis = (
        "O sistema valida conteúdo valida conteúdo valida conteúdo valida conteúdo "
        "antes de publicar o áudio final."
    )
    report = analyze_content_coverage(reference, hypothesis)
    assert report.status == "rejected"
    assert report.metrics["repetition_ratio"] > 0
    assert report.metrics["suspicious_repeated_phrases"]


def test_proper_name_disagreement_is_not_misclassified_as_skipped_speech() -> None:
    report = analyze_content_coverage(
        "Geralt usa Aard em Kaer Morhen durante a batalha.",
        "Geralt usa Arde em Quer Morrem durante a batalha.",
    )
    assert report.status == "passed"
    assert report.metrics["deletion_ratio"] == 0.0
    assert report.metrics["disagreement_classification"] == ("pronunciation_or_asr_disagreement")


def test_asr_unavailable_is_distinct_from_content_failure() -> None:
    report = unavailable_content_report()
    assert report.status == "unavailable"
    assert report.reasons == ("asr_unavailable",)
