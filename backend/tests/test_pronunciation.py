from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from app.core.config import settings
from app.services.pronunciation import (
    PronunciationConfigurationError,
    PronunciationPreprocessor,
    pronunciation_preflight,
)
from app.services.text_normalization import PortugueseTextNormalizer


def _entry(
    entry_id: str,
    canonical: str,
    synthesis: str,
    **changes: object,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": entry_id,
        "canonical_term": canonical,
        "synthesis_representation": synthesis,
        "category": "custom",
        "locale": "pt-BR",
        "match": {"mode": "token", "case_sensitive": False},
        "enabled": True,
        "priority": 100,
        "revision": 1,
        "source": "test",
        "approval": {"status": "approved", "approved_by": "tester"},
    }
    entry.update(changes)
    return entry


def _write_lexicon(
    path: Path,
    entries: list[dict[str, object]],
    *,
    version: str = "1",
) -> Path:
    path.write_text(
        yaml.safe_dump(
            {"schema": "neovids_pronunciation_lexicon_v2", "version": version, "entries": entries},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _load(tmp_path: Path, entries: list[dict[str, object]]) -> PronunciationPreprocessor:
    return PronunciationPreprocessor.load(_write_lexicon(tmp_path / "lexicon.yaml", entries))


def test_no_matching_rules_preserves_text(tmp_path: Path) -> None:
    resolver = _load(tmp_path, [])
    result = resolver.preprocess("Texto sem regra.")
    assert result.synthesis_text == result.canonical_text == "Texto sem regra."
    assert result.applied_replacements == ()


def test_literal_matches_are_surgical_repeated_and_punctuation_safe(tmp_path: Path) -> None:
    resolver = _load(tmp_path, [_entry("initialism.npc", "NPC", "Êne Pê Cê")])
    result = resolver.preprocess("NPC, outro NPC! NPCs e xNPC não mudam.")
    assert result.synthesis_text == "Êne Pê Cê, outro Êne Pê Cê! NPCs e xNPC não mudam."
    assert [item["original_text"] for item in result.applied_replacements] == ["NPC", "NPC"]
    assert [(item["input_start"], item["input_end"]) for item in result.applied_replacements] == [
        (0, 3),
        (11, 14),
    ]


def test_unicode_is_normalized_to_nfc_for_matching(tmp_path: Path) -> None:
    resolver = _load(tmp_path, [_entry("term.cafe", "Café", "Cafézinho")])
    result = resolver.preprocess("Cafe\u0301, aberto.")
    assert result.synthesis_text == "Cafézinho, aberto."
    assert result.resolution_input_text == "Café, aberto."
    assert result.applied_replacements[0]["canonical_start"] is None


def test_case_sensitive_and_case_insensitive_entries(tmp_path: Path) -> None:
    sensitive = _entry("initialism.gpu", "GPU", "Gê Pê U")
    sensitive["match"] = {"mode": "token", "case_sensitive": True}
    resolver = _load(tmp_path, [sensitive, _entry("brand.qwen", "Qwen", "Quén")])
    result = resolver.preprocess("GPU gpu; QWEN e qwen.")
    assert result.synthesis_text == "Gê Pê U gpu; Quén e Quén."


def test_longest_match_wins_at_equal_priority(tmp_path: Path) -> None:
    resolver = _load(
        tmp_path,
        [
            _entry("game.red-dead", "Red Dead", "Réd Déd"),
            _entry("game.red-dead-redemption", "Red Dead Redemption", "Réd Déd Redémption"),
        ],
    )
    result = resolver.preprocess("Red Dead Redemption e Red Dead.")
    assert result.synthesis_text == "Réd Déd Redémption e Réd Déd."
    assert [item["entry_id"] for item in result.applied_replacements] == [
        "game.red-dead-redemption",
        "game.red-dead",
    ]


def test_explicit_priority_can_override_an_overlapping_rule(tmp_path: Path) -> None:
    high = _entry("brand.rockstar", "Rockstar", "Rócstar", priority=200)
    resolver = _load(
        tmp_path,
        [high, _entry("brand.rockstar-games", "Rockstar Games", "Rócstar Gueimes")],
    )
    result = resolver.preprocess("Rockstar Games")
    assert result.synthesis_text == "Rócstar Games"
    assert result.applied_replacements[0]["entry_id"] == "brand.rockstar"


def test_project_rule_explicitly_overrides_global_rule(tmp_path: Path) -> None:
    scoped = _entry("project.aard", "Aard", "Aahrd", projects=["witcher"])
    resolver = _load(tmp_path, [_entry("global.aard", "Aard", "Árd"), scoped])
    assert resolver.preprocess("Aard", project="witcher").synthesis_text == "Aahrd"
    assert resolver.preprocess("Aard", project="other").synthesis_text == "Árd"


def test_disabled_and_unapproved_entries_never_fire(tmp_path: Path) -> None:
    disabled = _entry("term.disabled", "GPU", "Gê Pê U", enabled=False)
    undecided = _entry("term.undecided", "CPU", "Cê Pê U")
    undecided["approval"] = {"status": "undecided"}
    resolver = _load(tmp_path, [disabled, undecided])
    assert resolver.preprocess("GPU CPU").synthesis_text == "GPU CPU"
    assert resolver.entries(production_only=True) == ()


def test_acronym_entry_and_number_normalization_interaction(tmp_path: Path) -> None:
    resolver = _load(
        tmp_path,
        [_entry("initialism.gta", "GTA", "Gê Tê Á", category="initialism")],
    )
    canonical = "Em 2022, GTA 6 chegou."
    normalization = PortugueseTextNormalizer().normalize(canonical)
    result = resolver.preprocess(normalization.normalized_text, canonical_text=canonical)
    assert normalization.normalized_text == "Em dois mil e vinte e dois, GTA 6 chegou."
    assert result.canonical_text == canonical
    assert result.synthesis_text == "Em dois mil e vinte e dois, Gê Tê Á 6 chegou."
    assert result.applied_replacements[0]["canonical_start"] is None


def test_resolution_is_idempotent_and_non_idempotent_config_is_rejected(tmp_path: Path) -> None:
    resolver = _load(tmp_path, [_entry("initialism.gta", "GTA", "Gê Tê Á")])
    first = resolver.preprocess("GTA")
    second = resolver.preprocess(first.synthesis_text)
    assert second.synthesis_text == first.synthesis_text

    with pytest.raises(PronunciationConfigurationError, match="non-idempotent"):
        _load(
            tmp_path,
            [
                _entry("term.first", "foo", "bar"),
                _entry("term.second", "bar", "baz"),
            ],
        )


def test_identity_tracks_semantics_but_not_order_or_notes(tmp_path: Path) -> None:
    entries = [_entry("term.one", "Aard", "Árd"), _entry("term.two", "NPC", "Êne Pê Cê")]
    first = PronunciationPreprocessor.load(_write_lexicon(tmp_path / "one.yaml", entries))
    reordered = list(reversed(deepcopy(entries)))
    reordered[0]["notes"] = "Formatting and operator notes are not synthesis semantics."
    second = PronunciationPreprocessor.load(_write_lexicon(tmp_path / "two.yaml", reordered))
    release_only = PronunciationPreprocessor.load(
        _write_lexicon(tmp_path / "release.yaml", deepcopy(entries), version="99")
    )
    changed = deepcopy(entries)
    changed[0]["synthesis_representation"] = "Aahrd"
    third = PronunciationPreprocessor.load(_write_lexicon(tmp_path / "three.yaml", changed))
    assert first.identity() == second.identity()
    assert first.identity() == release_only.identity()
    assert first.identity() != third.identity()


def test_provenance_contains_entry_versions_and_both_span_spaces(tmp_path: Path) -> None:
    entry = _entry("brand.rockstar-games", "Rockstar Games", "Rócstar Gueimes", category="brand")
    entry.update({"revision": 4, "source": "lab:run-123", "confidence": 0.9})
    resolver = _load(tmp_path, [entry])
    result = resolver.preprocess("A Rockstar Games chegou.")
    replacement = result.applied_replacements[0]
    assert replacement == {
        "entry_id": "brand.rockstar-games",
        "entry_version": 4,
        "lexicon_version": "1",
        "replacement_type": "brand",
        "locale": "pt-BR",
        "match_mode": "token",
        "case_sensitive": False,
        "priority": 100,
        "original_text": "Rockstar Games",
        "replacement_text": "Rócstar Gueimes",
        "changed": True,
        "occurrence": 1,
        "offset_space": "nfc_resolution_input_text",
        "input_start": 2,
        "input_end": 16,
        "canonical_start": 2,
        "canonical_end": 16,
        "synthesis_start": 2,
        "synthesis_end": 17,
        "source": "lab:run-123",
        "approval_status": "approved",
        "approved_by": "tester",
        "confidence": 0.9,
        "notes": None,
    }


def test_raw_spelling_can_be_an_explicit_approved_noop(tmp_path: Path) -> None:
    resolver = _load(tmp_path, [_entry("brand.raw", "Unreal Engine", "Unreal Engine")])
    result = resolver.preprocess("Use Unreal Engine.")
    assert result.synthesis_text == result.canonical_text
    assert result.applied_replacements[0]["changed"] is False


def test_literal_mode_is_explicit_and_can_match_inside_a_token(tmp_path: Path) -> None:
    literal = _entry("literal.gta", "GTA", "Gê Tê Á")
    literal["match"] = {"mode": "literal", "case_sensitive": False}
    resolver = _load(tmp_path, [literal])
    assert resolver.preprocess("xGTAx").synthesis_text == "xGê Tê Áx"


def test_legacy_mapping_remains_readable(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text("version: '1'\nglobal: {Aard: Árd}\nprojects: {}\n", encoding="utf-8")
    resolver = PronunciationPreprocessor.load(path)
    assert resolver.preprocess("Aard").synthesis_text == "Árd"
    assert resolver.entries()[0].source == "legacy_production_glossary"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"id": "INVALID ID"}, "entry id"),
        ({"canonical_term": ""}, "empty"),
        ({"synthesis_representation": ""}, "empty"),
        ({"locale": "not_a_locale"}, "locale"),
        ({"priority": -1}, "priority"),
        ({"revision": 0}, "revision"),
        ({"confidence": 1.1}, "confidence"),
    ],
)
def test_invalid_entry_configuration_is_rejected(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    entry = _entry("term.valid", "Aard", "Árd")
    entry.update(mutation)
    with pytest.raises(PronunciationConfigurationError, match=message):
        _load(tmp_path, [entry])


def test_ambiguous_equal_priority_collision_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PronunciationConfigurationError, match="ambiguous"):
        _load(
            tmp_path,
            [
                _entry("term.first", "GPU", "Gê Pê U"),
                _entry("term.second", "gpu", "Gu Pú"),
            ],
        )


def test_title_numerals_are_phrase_aware_and_preserve_canonical_text(tmp_path: Path) -> None:
    generic = _entry("initialism.gta", "GTA", "Gê Tê Á", category="initialism")
    exact_entries = []
    for numeral, spoken in (("IV", "quatro"), ("V", "cinco"), ("VI", "seis")):
        entry = _entry(
            f"title.gta-{numeral.casefold()}",
            f"GTA {numeral}",
            f"Gê Tê Á {spoken}",
            category="title_numeral",
            priority=300,
        )
        entry["match"] = {"mode": "title_numeral", "case_sensitive": False}
        exact_entries.append(entry)
    resolver = _load(tmp_path, [generic, *exact_entries])
    canonical = "GTA IV, gta v: GTA VI! GTA Vice City e capítulo VI."
    result = resolver.preprocess(canonical, canonical_text=canonical)
    assert result.canonical_text == canonical
    assert result.synthesis_text == (
        "Gê Tê Á quatro, Gê Tê Á cinco: Gê Tê Á seis! Gê Tê Á Vice City e capítulo VI."
    )
    assert [item["entry_id"] for item in result.applied_replacements[:3]] == [
        "title.gta-iv",
        "title.gta-v",
        "title.gta-vi",
    ]


def test_exact_title_number_precedes_generic_token(tmp_path: Path) -> None:
    exact = _entry("title.gta-6", "GTA 6", "Gê Tê Á seis", priority=300)
    exact["match"] = {"mode": "exact_phrase", "case_sensitive": False}
    resolver = _load(
        tmp_path,
        [_entry("initialism.gta", "GTA", "Gê Tê Á"), exact],
    )
    result = resolver.preprocess("GTA 6 e GTA 5.")
    assert result.synthesis_text == "Gê Tê Á seis e Gê Tê Á 5."
    assert result.applied_replacements[0]["match_mode"] == "exact_phrase"


def test_preflight_warns_without_mutating_unknown_risky_terms(tmp_path: Path) -> None:
    resolver = _load(tmp_path, [_entry("initialism.npc", "NPC", "Êne Pê Cê")])
    canonical = "GTA VI terá NPCs e hardware novo."
    report = pronunciation_preflight(canonical, resolver)
    assert report["canonical_text_unchanged"] is True
    terms = {item["canonical_term"] for item in report["candidates"]}
    assert "GTA VI" in terms
    assert all("synthesis_text" not in item for item in report["candidates"])


def test_duplicate_and_overly_broad_rules_are_rejected(tmp_path: Path) -> None:
    duplicate = _entry("term.duplicate", "NPC", "Êne Pê Cê")
    with pytest.raises(PronunciationConfigurationError, match="unreachable duplicate"):
        _load(tmp_path, [_entry("term.original", "NPC", "Êne Pê Cê"), duplicate])

    broad = _entry("literal.v", "V", "cinco")
    broad["match"] = {"mode": "literal", "case_sensitive": False}
    with pytest.raises(PronunciationConfigurationError, match="overly broad"):
        _load(tmp_path, [broad])


def test_gta_title_numerals_are_promoted_to_production() -> None:
    """Verify GTA IV/V/VI entries are now production-active after validation.

    These were previously candidates but were promoted after production validation
    confirmed the PT-BR letter naming convention (Gê Tê Á + number).
    """
    configured = settings.pronunciation_glossary_path
    glossary = configured if configured.is_absolute() else Path(__file__).parents[1] / configured
    resolver = PronunciationPreprocessor.load(glossary)
    candidates = {
        entry.canonical_term: entry
        for entry in resolver.entries()
        if entry.category.value == "title_numeral"
    }
    assert set(candidates) >= {"GTA IV", "GTA V", "GTA VI"}
    # GTA entries are now production-active after validation on 2026-09-04
    for term in ["GTA IV", "GTA V", "GTA VI"]:
        assert candidates[term].production_active, f"{term} should be production-active"
    # Verify correct PT-BR pronunciation is applied
    result = resolver.preprocess("GTA VI", canonical_text="GTA VI")
    assert result.canonical_text == "GTA VI"
    assert result.synthesis_text == "Gê Tê Á seis"
