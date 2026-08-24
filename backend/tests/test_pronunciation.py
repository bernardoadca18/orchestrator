from __future__ import annotations

from pathlib import Path

import pytest

from app.services.pronunciation import PronunciationConfigurationError, PronunciationPreprocessor


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
