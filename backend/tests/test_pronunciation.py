from __future__ import annotations

from pathlib import Path

import pytest

from app.services.pronunciation import PronunciationConfigurationError, PronunciationPreprocessor
from app.services.text_normalization import PortugueseTextNormalizer


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_pronunciation_is_deterministic_and_preserves_canonical(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "glossary.yaml",
        """version: '1'\nglobal:\n  Aard: Árd\n  Kaer Morhen: Kér Mórren\nprojects: {}\n""",
    )
    processor = PronunciationPreprocessor.load(path)
    canonical = "Geralt usa AARD em Kaer Morhen, sem alterar aardvark."
    first = processor.preprocess(canonical)
    second = processor.preprocess(canonical)
    assert first.canonical_text == canonical
    assert first.synthesis_text == "Geralt usa Árd em Kér Mórren, sem alterar aardvark."
    assert first == second


def test_longest_overlapping_term_wins_and_project_can_override(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "glossary.yaml",
        """version: '2'
global:
  Kaer: Kér
  Kaer Morhen: Kér Mórren
projects:
  witcher:
    Aard: Aahrd
""",
    )
    result = PronunciationPreprocessor.load(path).preprocess(
        "Kaer Morhen — Aard!", project="witcher"
    )
    assert result.synthesis_text == "Kér Mórren — Aahrd!"


def test_glossary_change_changes_identity(tmp_path: Path) -> None:
    first = _write(tmp_path / "one.yaml", "version: '1'\nglobal: {Aard: Árd}\nprojects: {}\n")
    second = _write(tmp_path / "two.yaml", "version: '1'\nglobal: {Aard: Aahrd}\nprojects: {}\n")
    assert (
        PronunciationPreprocessor.load(first).preprocess("Aard").glossary_identity
        != PronunciationPreprocessor.load(second).preprocess("Aard").glossary_identity
    )


def test_invalid_glossary_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PronunciationConfigurationError):
        PronunciationPreprocessor.load(_write(tmp_path / "bad.yaml", "- invalid"))


def test_normalization_precedes_glossary_without_mutating_canonical(tmp_path: Path) -> None:
    glossary = _write(
        tmp_path / "glossary.yaml",
        "version: '1'\nglobal: {GTA: Gê Tê Á, Aard: Árd}\nprojects: {}\n",
    )
    canonical = "Em 2022, GTA 6 trouxe Aard."
    normalized = PortugueseTextNormalizer().normalize(canonical)
    pronounced = PronunciationPreprocessor.load(glossary).preprocess(normalized.normalized_text)
    assert normalized.canonical_text == canonical
    assert pronounced.synthesis_text == "Em dois mil e vinte e dois, Gê Tê Á 6 trouxe Árd."


def test_mixed_language_terms_are_bounded_and_auditable(tmp_path: Path) -> None:
    glossary = _write(
        tmp_path / "glossary.yaml",
        "version: '1'\nglobal: {Rockstar Games: Rócstar Gueimes, NPC: Êne Pê Cê}\nprojects: {}\n",
    )
    result = PronunciationPreprocessor.load(glossary).preprocess(
        "Rockstar Games criou um NPC; o nickname Rockstargames não muda."
    )
    assert result.synthesis_text == (
        "Rócstar Gueimes criou um Êne Pê Cê; o nickname Rockstargames não muda."
    )
    assert result.applied_replacements == (
        {"term": "Rockstar Games", "replacement": "Rócstar Gueimes", "count": 1},
        {"term": "NPC", "replacement": "Êne Pê Cê", "count": 1},
    )
