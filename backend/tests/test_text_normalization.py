from __future__ import annotations

import pytest

from app.services.text_normalization import PortugueseTextNormalizer


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Em 2022 aconteceu.", "Em dois mil e vinte e dois aconteceu."),
        ("A taxa foi 10%.", "A taxa foi dez por cento."),
        ("Custou R$ 150.", "Custou cento e cinquenta reais."),
        ("Custou US$ 20.", "Custou vinte dólares americanos."),
        ("A distância é 3,5 km.", "A distância é três vírgula cinco quilômetros."),
        ("A reunião começa às 14:30.", "A reunião começa às catorze horas e trinta minutos."),
        ("Publicado em 14/03/2026.", "Publicado em catorze de março de dois mil e vinte e seis."),
        (
            "O áudio usa 24 kHz e 48 kHz.",
            "O áudio usa vinte e quatro quilohertz e quarenta e oito quilohertz.",
        ),
    ],
)
def test_unambiguous_portuguese_forms_are_normalized(source: str, expected: str) -> None:
    result = PortugueseTextNormalizer().normalize(source)
    assert result.canonical_text == source
    assert result.normalized_text == expected


@pytest.mark.parametrize(
    "source",
    [
        "GTA 6",
        "GTA VI",
        "RTX 2060",
        "Qwen3-TTS",
        "https://example.test/releases/2022",
        "/models/Qwen3-TTS/v1.2.3",
        "123e4567-e89b-12d3-a456-426614174000",
    ],
)
def test_ambiguous_identifiers_are_preserved(source: str) -> None:
    assert PortugueseTextNormalizer().normalize(source).normalized_text == source


def test_normalizer_identity_is_stable_and_versioned() -> None:
    first = PortugueseTextNormalizer()
    second = PortugueseTextNormalizer()
    changed = PortugueseTextNormalizer(version="pt_br_text_normalizer_v2")
    assert first.identity == second.identity
    assert first.identity != changed.identity
    with pytest.raises(ValueError, match="version"):
        PortugueseTextNormalizer(version=" ")
