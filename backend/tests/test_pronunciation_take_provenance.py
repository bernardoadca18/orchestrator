from __future__ import annotations

from types import SimpleNamespace

from app.models.workflow import AudioBlocoTake
from app.repositories.workflow_repository import _take_text_provenance


def test_take_provenance_is_a_snapshot_not_a_mutable_block_alias() -> None:
    block = SimpleNamespace(
        texto="A Rockstar Games chegou.",
        normalized_text="A Rockstar Games chegou.",
        synthesis_text="A Rócstar Gueimes chegou.",
        pronunciation_glossary_identity="l" * 64,
        pronunciation_replacements=[
            {
                "entry_id": "brand.rockstar-games",
                "entry_version": 1,
                "original_text": "Rockstar Games",
                "replacement_text": "Rócstar Gueimes",
            }
        ],
        text_normalizer_version="pt_br_text_normalizer_v1",
        text_normalizer_identity="n" * 64,
    )
    snapshot = _take_text_provenance(block)  # type: ignore[arg-type]
    block.pronunciation_replacements[0]["entry_version"] = 2
    block.synthesis_text = "A nova forma chegou."
    assert snapshot["synthesis_text"] == "A Rócstar Gueimes chegou."
    assert snapshot["pronunciation_replacements"][0]["entry_version"] == 1  # type: ignore[index]


def test_take_model_contains_nullable_historical_pronunciation_fields() -> None:
    expected = {
        "canonical_text",
        "normalized_text",
        "synthesis_text",
        "pronunciation_glossary_identity",
        "pronunciation_replacements",
        "text_normalizer_version",
        "text_normalizer_identity",
    }
    assert expected <= set(AudioBlocoTake.__table__.columns.keys())
    assert all(AudioBlocoTake.__table__.columns[name].nullable for name in expected)
