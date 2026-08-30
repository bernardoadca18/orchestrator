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
    assert report.classification == "content_complete"
    assert report.retry_required is False


def test_missing_final_phrase_is_a_premature_ending() -> None:
    report = analyze_content_coverage(
        "A história começa com calma e termina explicando todos os detalhes importantes.",
        "A história começa com calma e termina",
    )
    assert report.status == "rejected"
    assert report.metrics["premature_ending"] is True
    assert "premature_ending_or_trailing_content_missing" in report.reasons
    assert report.classification == "premature_end"
    assert report.retry_required is True


def test_missing_middle_phrase_reports_longest_contiguous_span() -> None:
    report = analyze_content_coverage(
        "Primeiro apresentamos o contexto depois explicamos seis detalhes muito importantes "
        "e finalmente concluímos o assunto.",
        "Primeiro apresentamos o contexto e finalmente concluímos o assunto.",
    )
    assert report.status == "rejected"
    assert report.metrics["longest_missing_contiguous_token_count"] >= 6
    assert "detalhes" in str(report.metrics["longest_missing_contiguous_text"])
    assert report.classification == "probable_content_loss"
    assert report.retry_required is True


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
    assert report.classification == "probable_repetition"
    assert report.retry_required is True


def test_proper_name_disagreement_is_not_misclassified_as_skipped_speech() -> None:
    report = analyze_content_coverage(
        "Geralt usa Aard em Kaer Morhen durante a batalha.",
        "Geralt usa Arde em Quer Morrem durante a batalha.",
    )
    assert report.status == "passed"
    assert report.metrics["deletion_ratio"] == 0.0
    assert report.metrics["disagreement_classification"] == ("pronunciation_or_asr_disagreement")
    assert report.retry_required is False


def test_asr_unavailable_is_distinct_from_content_failure() -> None:
    report = unavailable_content_report()
    assert report.status == "unavailable"
    assert report.reasons == ("asr_unavailable",)
    assert report.classification == "content_inconclusive"
    assert report.retry_required is False


def test_real_workflow_local_pronunciations_are_warning_not_content_loss() -> None:
    report = analyze_content_coverage(
        "Os veículos usavam o motor enquanto o Xbox trezentos e sessenta carregava a missão.",
        "Os vaículos usavam o môtór enquanto o ixbox 360 carregava a missão.",
    )

    assert report.status == "passed"
    assert report.classification == "pronunciation_or_asr_disagreement"
    assert report.retry_required is False
    assert report.metrics["deletion_count"] == 0
    assert report.metrics["longest_missing_contiguous_token_count"] == 0


def test_numeric_representation_is_comparison_equivalent() -> None:
    report = analyze_content_coverage(
        "O Xbox 360 chegou em 2005.",
        "O Xbox trezentos e sessenta chegou em dois mil e cinco.",
    )

    assert report.status == "passed"
    assert report.classification == "content_complete"
    assert report.metrics["normalized_wer"] == 0.0


def test_complete_sentence_removed_is_structural_content_loss() -> None:
    report = analyze_content_coverage(
        "A primeira frase apresenta o contexto. "
        "A segunda descreve o encontro com todos os inimigos. "
        "A terceira encerra a missão com segurança.",
        "A primeira frase apresenta o contexto. "
        "A terceira encerra a missão com segurança.",
    )

    assert report.status == "rejected"
    assert report.classification == "probable_content_loss"
    assert report.retry_required is True
    assert report.metrics["structurally_missing_sentences"]


def test_large_structural_reordering_is_rejected() -> None:
    report = analyze_content_coverage(
        "Primeiro entramos silenciosamente pela garagem abandonada. "
        "Depois encontramos o mapa secreto dentro do escritório principal. "
        "Finalmente escapamos pela ponte antes da chegada dos guardas.",
        "Primeiro entramos silenciosamente pela garagem abandonada. "
        "Finalmente escapamos pela ponte antes da chegada dos guardas. "
        "Depois encontramos o mapa secreto dentro do escritório principal.",
    )

    assert report.status == "rejected"
    assert report.classification == "probable_reordering"
    assert report.retry_required is True


def test_long_form_scattered_local_substitutions_do_not_accumulate_into_rejection() -> None:
    expected_sentences = [
        "Os veículos atravessaram a cidade enquanto o motor permaneceu estável.",
        "O Xbox 360 carregou a missão e mostrou o primeiro objetivo.",
        "A equipe encontrou a entrada correta depois de consultar o mapa.",
        "Durante o trajeto todos os personagens permaneceram no veículo.",
        "A sequência continuou normalmente até a ponte principal.",
        "Depois disso o grupo encontrou os inimigos perto da estação.",
        "O plano foi concluído sem perder nenhuma etapa importante.",
        "No final a missão confirmou o resultado esperado para o jogador.",
    ]
    hypothesis_sentences = [
        sentence.replace("veículos", "vaículos")
        .replace("veículo", "vaículo")
        .replace("motor", "môtór")
        .replace("Xbox 360", "ixbox trezentos e sessenta")
        for sentence in expected_sentences
    ]
    report = analyze_content_coverage(
        " ".join(expected_sentences * 3),
        " ".join(hypothesis_sentences * 3),
    )

    assert report.status == "passed"
    assert report.classification == "pronunciation_or_asr_disagreement"
    assert report.retry_required is False
    assert report.metrics["structural_coverage_ratio"] == 1.0
    assert report.metrics["pronunciation_variant_match_count"] > 0
