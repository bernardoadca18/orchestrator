from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.services.pronunciation import PronunciationCategory, PronunciationPreprocessor
from app.services.pronunciation_lab import (
    LAB_SCHEMA_VERSION,
    PronunciationLabValidationError,
    record_review,
)
from app.services.pronunciation_lexicon_store import (
    PronunciationLexiconMutationError,
    PronunciationLexiconStore,
)


def _empty_lexicon(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "schema": "neovids_pronunciation_lexicon_v2",
                "version": "1",
                "entries": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _run_directory(path: Path) -> Path:
    path.mkdir()
    record = {
        "schema": LAB_SCHEMA_VERSION,
        "run_id": path.name,
        "target_term": "Rockstar Games",
        "candidate_id": "manual.winner",
        "candidate_text": "Róckstar Games",
        "status": "completed",
    }
    (path / "manifest.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def test_promotion_is_preview_only_until_explicit_optimistic_apply(tmp_path: Path) -> None:
    path = _empty_lexicon(tmp_path / "lexicon.yaml")
    run = _run_directory(tmp_path / "lab-run")
    record_review(
        run,
        candidate_id="manual.winner",
        status="approved",
        reviewer="bernardo",
        notes="Approved after controlled listening.",
    )
    store = PronunciationLexiconStore(path)
    initial = PronunciationPreprocessor.load(path)
    preview = store.promote_lab_candidate(
        run_directory=run,
        candidate_id="manual.winner",
        entry_id="brand.rockstar-games",
        category=PronunciationCategory.BRAND,
    )
    assert preview.applied is False
    assert PronunciationPreprocessor.load(path).entries() == ()

    applied = store.promote_lab_candidate(
        run_directory=run,
        candidate_id="manual.winner",
        entry_id="brand.rockstar-games",
        category=PronunciationCategory.BRAND,
        expected_configuration_identity=initial.configuration_identity(),
        apply=True,
    )
    updated = PronunciationPreprocessor.load(path)
    entry = updated.get_entry("brand.rockstar-games")
    assert applied.applied is True
    assert entry.synthesis_representation == "Róckstar Games"
    assert entry.approved_by == "bernardo"
    assert entry.source == "pronunciation_lab:lab-run:manual.winner"
    assert updated.preprocess("Rockstar Games").synthesis_text == "Róckstar Games"
    assert applied.previous_production_identity != applied.next_production_identity


def test_stale_configuration_identity_blocks_apply(tmp_path: Path) -> None:
    path = _empty_lexicon(tmp_path / "lexicon.yaml")
    run = _run_directory(tmp_path / "lab-run")
    record_review(run, candidate_id="manual.winner", status="approved", reviewer="operator")
    with pytest.raises(PronunciationLexiconMutationError, match="changed"):
        PronunciationLexiconStore(path).promote_lab_candidate(
            run_directory=run,
            candidate_id="manual.winner",
            entry_id="brand.rockstar-games",
            category=PronunciationCategory.BRAND,
            expected_configuration_identity="stale",
            apply=True,
        )


def test_rejected_candidate_cannot_become_a_production_entry(tmp_path: Path) -> None:
    path = _empty_lexicon(tmp_path / "lexicon.yaml")
    run = _run_directory(tmp_path / "lab-run")
    record_review(run, candidate_id="manual.winner", status="rejected", reviewer="operator")
    with pytest.raises(PronunciationLabValidationError, match="approved review"):
        PronunciationLexiconStore(path).promote_lab_candidate(
            run_directory=run,
            candidate_id="manual.winner",
            entry_id="brand.rockstar-games",
            category=PronunciationCategory.BRAND,
        )


def test_disable_is_versioned_and_requires_explicit_apply(tmp_path: Path) -> None:
    path = _empty_lexicon(tmp_path / "lexicon.yaml")
    run = _run_directory(tmp_path / "lab-run")
    record_review(run, candidate_id="manual.winner", status="approved", reviewer="operator")
    store = PronunciationLexiconStore(path)
    initial = PronunciationPreprocessor.load(path)
    store.promote_lab_candidate(
        run_directory=run,
        candidate_id="manual.winner",
        entry_id="brand.rockstar-games",
        category=PronunciationCategory.BRAND,
        expected_configuration_identity=initial.configuration_identity(),
        apply=True,
    )
    enabled = PronunciationPreprocessor.load(path)
    preview = store.disable("brand.rockstar-games")
    assert preview.entry.enabled is False
    assert PronunciationPreprocessor.load(path).get_entry("brand.rockstar-games").enabled is True
    store.disable(
        "brand.rockstar-games",
        expected_configuration_identity=enabled.configuration_identity(),
        apply=True,
    )
    disabled = PronunciationPreprocessor.load(path).get_entry("brand.rockstar-games")
    assert disabled.enabled is False
    assert disabled.revision == 2
