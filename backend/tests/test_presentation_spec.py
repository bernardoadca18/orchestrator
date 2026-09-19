from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.workflow import PresentationSpecOut
from app.services.presentation_spec import (
    CAPTION_POLICY_V1,
    PresentationInputError,
    build_presentation_spec,
    canonical_hash,
    segment_captions,
)

FIXTURE = Path(__file__).parent / "fixtures" / "presentation_spec_v1_contract.json"
REAL_ZERO_DURATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "real_presentation_zero_duration_words.json"
)


def _words(*values: tuple[str, float, float]) -> list[dict]:
    return [{"palavra": word, "inicio": start, "fim": end} for word, start, end in values]


def _workflow(
    words: list[dict],
    *,
    display_title: str | None = None,
    audio_sha: str = "a" * 64,
) -> SimpleNamespace:
    raw_block = {
        "ordem": 0,
        "tipo": "structural-not-a-display-title",
        "conteudo": "texto",
    }
    if display_title is not None:
        raw_block["display_title"] = display_title
    return SimpleNamespace(
        id=uuid.uuid4(),
        audio_final_sha256="a" * 64,
        roteiro_snapshot={"titulo": "Vídeo", "blocos": [raw_block]},
        blocos=[
            SimpleNamespace(
                ordem=0,
                narration_start_seconds=2.0,
                narration_end_seconds=6.0,
            )
        ],
        transcricao=SimpleNamespace(
            audio_sha256=audio_sha,
            palavras_timestamps=words,
        ),
    )


def test_caption_policy_handles_one_word_short_phrase_and_touching_boundaries() -> None:
    one = segment_captions(_words(("Olá", 0.0, 0.4)), duration_seconds=1.0)
    assert [(item.text, item.start_seconds, item.end_seconds) for item in one] == [
        ("Olá", 0.0, 0.4)
    ]
    phrase = segment_captions(
        _words(("uma", 0.0, 0.2), ("frase", 0.2, 0.5), ("curta.", 0.5, 0.9)),
        duration_seconds=0.9,
    )
    assert [item.text for item in phrase] == ["uma frase curta."]


def test_mirrored_cross_service_fixture_has_canonical_identity() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = PresentationSpecOut.model_validate(payload)
    content = parsed.model_dump(mode="json")
    declared = content.pop("fingerprint")
    assert canonical_hash(content) == declared


def test_caption_policy_punctuation_and_long_pause_are_deterministic() -> None:
    words = _words(
        ("Olá", 0.0, 0.2),
        (",", 0.2, 0.3),
        ("mundo", 0.3, 0.7),
        ("!", 0.7, 0.9),
        ("Depois", 2.0, 2.3),
        ("voltamos?", 2.3, 2.8),
    )
    first = segment_captions(words, duration_seconds=3.0)
    second = segment_captions(words, duration_seconds=3.0)
    assert first == second
    assert [item.text for item in first] == ["Olá, mundo!", "Depois voltamos?"]
    assert first[0].end_seconds < first[1].start_seconds


def test_caption_policy_long_phrase_uses_at_most_two_bounded_lines() -> None:
    tokens = [
        "A",
        "cidade",
        "reage",
        "de",
        "maneira",
        "sutil",
        "enquanto",
        "o",
        "jogador",
        "atravessa",
        "ruas",
        "cheias",
    ]
    words = _words(
        *[(token, index * 0.25, index * 0.25 + 0.2) for index, token in enumerate(tokens)]
    )
    cues = segment_captions(words, duration_seconds=5.0)
    assert cues
    assert all(len(item.text.splitlines()) <= 2 for item in cues)
    assert all(len(line) <= 42 for item in cues for line in item.text.splitlines())
    assert any("\n" in item.text for item in cues)


@pytest.mark.parametrize(
    ("words", "duration", "reason"),
    [
        (_words(("bad", -0.1, 0.2)), 1.0, "negative_start"),
        (_words(("bad", 0.4, 0.3)), 1.0, "end_not_after_start"),
        (
            _words(("one", 0.0, 0.5), ("overlap", 0.249, 0.8)),
            1.0,
            "overlaps_previous_word",
        ),
        (_words(("late", 0.8, 1.251)), 1.0, "end_exceeds_duration"),
        (_words(("long", 0.0, 5.6)), 6.0, "word_duration_exceeds_policy"),
        (_words(("nan", float("nan"), 0.2)), 1.0, "non_finite_start"),
        (_words(("inf", 0.0, float("inf"))), 1.0, "non_finite_end"),
    ],
)
def test_caption_policy_rejects_invalid_timestamps(
    words: list[dict], duration: float, reason: str
) -> None:
    with pytest.raises(PresentationInputError) as error:
        segment_captions(words, duration_seconds=duration)
    assert error.value.reason == reason
    assert error.value.details["word_index"] == len(words) - 1
    assert reason in str(error.value)


def test_caption_policy_reports_malformed_payload_separately() -> None:
    with pytest.raises(PresentationInputError, match="payload is malformed"):
        segment_captions([{"palavra": "missing", "inicio": 0.0}], duration_seconds=1.0)


def test_tiny_overlap_is_normalized_without_mutating_source_words() -> None:
    words = _words(("one", 0.0, 0.5), ("two", 0.4995, 0.8))
    original = [dict(item) for item in words]
    cues = segment_captions(words, duration_seconds=1.0)
    assert words == original
    assert cues[0].start_seconds == 0.0
    assert cues[0].end_seconds == 0.8


def test_tiny_end_over_duration_is_clamped_but_material_overrun_fails() -> None:
    cues = segment_captions(
        _words(("fim.", 0.8, 1.0005)),
        duration_seconds=1.0,
    )
    assert cues[0].end_seconds == 1.0
    with pytest.raises(PresentationInputError) as error:
        segment_captions(
            _words(("fim.", 0.8, 1.0 + CAPTION_POLICY_V1.duration_tolerance_seconds + 0.001)),
            duration_seconds=1.0,
        )
    assert error.value.reason == "end_exceeds_duration"


def test_real_zero_duration_failure_shape_gets_only_bounded_derived_timing() -> None:
    words = json.loads(REAL_ZERO_DURATION_FIXTURE.read_text(encoding="utf-8"))
    assert [
        (item["word_index"], item["palavra"]) for item in words if item["inicio"] == item["fim"]
    ] == [
        (680, "explicar,"),
        (1764, "se"),
    ]
    original = [dict(item) for item in words]
    first = segment_captions(words, duration_seconds=876.32)
    second = segment_captions(words, duration_seconds=876.32)
    assert words == original
    assert first == second
    assert any("explicar," in cue.text for cue in first)
    assert any("se a Rockstar" in cue.text for cue in first)


def test_zero_duration_at_physical_end_remains_invalid() -> None:
    with pytest.raises(PresentationInputError) as error:
        segment_captions(_words(("fim", 1.0, 1.0)), duration_seconds=1.0)
    assert error.value.reason == "end_not_after_normalization"


def test_cross_block_boundary_is_deterministic_after_point_normalization() -> None:
    words = _words(
        ("primeiro.", 0.0, 1.0),
        ("limite", 1.0, 1.0),
        ("seguinte.", 1.0, 1.4),
    )
    ranges = [(0, 0.0, 1.0), (1, 1.0, 2.0)]
    first = segment_captions(words, duration_seconds=2.0, block_ranges=ranges)
    second = segment_captions(words, duration_seconds=2.0, block_ranges=ranges)
    assert first == second
    assert [cue.text for cue in first] == ["primeiro.", "limite seguinte."]
    assert first[1].start_seconds == 1.0


def test_cue_may_end_exactly_at_audio_duration() -> None:
    cues = segment_captions(_words(("fim.", 0.5, 1.0)), duration_seconds=1.0)
    assert cues[0].end_seconds == 1.0


def test_captions_flush_at_block_boundary_without_rewriting_words() -> None:
    words = _words(("primeiro", 0.0, 0.4), ("segundo", 0.5, 0.9))
    original = json.dumps(words)
    cues = segment_captions(words, duration_seconds=1, block_ranges=[(0, 0, 0.5), (1, 0.5, 1)])
    assert [(cue.text, cue.start_seconds, cue.end_seconds) for cue in cues] == [
        ("primeiro", 0, 0.4), ("segundo", 0.5, 0.9),
    ]
    assert json.dumps(words) == original


@pytest.mark.parametrize(
    "canonical,expected",
    [
        ("GTA III e GTA III", "GTA III"),
        ("GTA IV e GTA V", "GTA VI"),
        ("cidade", "GTA VI"),
    ],
)
def test_gta_reconciliation_is_unique_canonical_block_display_only(canonical, expected) -> None:
    words = _words(("GTA", 2.0, 2.2), ("6", 2.2, 2.4))
    workflow = _workflow(words)
    original = json.dumps(words)
    baseline = build_presentation_spec(workflow, duration_seconds=8)
    workflow.roteiro_snapshot["blocos"][0]["conteudo"] = canonical
    spec = build_presentation_spec(workflow, duration_seconds=8)
    assert spec.captions.cues[0].text == expected
    assert json.dumps(workflow.transcricao.palavras_timestamps) == original
    if expected == "GTA III":
        assert spec.fingerprint != baseline.fingerprint


def test_presentation_uses_authoritative_audio_and_never_derives_title_from_tipo() -> None:
    workflow = _workflow(_words(("teste", 0.0, 0.5)))
    spec = build_presentation_spec(workflow, duration_seconds=8.0)
    assert spec is not None
    assert spec.section_titles == []
    assert spec.transitions == []
    assert spec.branding.preset == "branding_disabled_v1"
    assert spec.branding.enabled is False

    titled = build_presentation_spec(
        _workflow(_words(("teste", 0.0, 0.5)), display_title="  A cidade reage  "),
        duration_seconds=8.0,
    )
    assert titled is not None
    assert titled.section_titles[0].text == "A cidade reage"
    assert titled.section_titles[0].start_seconds == 2.0
    assert titled.section_titles[0].end_seconds == 4.4
    assert titled.transitions[0].boundary_seconds == 2.0


def test_stale_transcription_is_rejected() -> None:
    workflow = _workflow(_words(("teste", 0.0, 0.5)), audio_sha="b" * 64)
    with pytest.raises(PresentationInputError, match="another final narration"):
        build_presentation_spec(workflow, duration_seconds=8.0)


def test_spec_and_fingerprint_are_deterministic_and_content_sensitive() -> None:
    base_words = _words(("mesma", 0.0, 0.3), ("fala", 0.3, 0.8))
    first = build_presentation_spec(_workflow(base_words), duration_seconds=8.0)
    repeated = build_presentation_spec(_workflow(base_words), duration_seconds=8.0)
    changed_word = build_presentation_spec(
        _workflow(_words(("outra", 0.0, 0.3), ("fala", 0.3, 0.8))),
        duration_seconds=8.0,
    )
    changed_timing = build_presentation_spec(
        _workflow(_words(("mesma", 0.0, 0.3), ("fala", 0.3, 0.9))),
        duration_seconds=8.0,
    )
    assert first and repeated and changed_word and changed_timing
    assert first.fingerprint == repeated.fingerprint
    assert first.fingerprint != changed_word.fingerprint
    assert first.fingerprint != changed_timing.fingerprint


def test_python_contract_forbids_unknown_fields_assets_and_presets() -> None:
    spec = build_presentation_spec(_workflow(_words(("teste", 0.0, 0.5))), duration_seconds=8.0)
    assert spec is not None
    payload = spec.model_dump(mode="json")
    with pytest.raises(ValidationError):
        PresentationSpecOut.model_validate({**payload, "css": "position: fixed"})
    with pytest.raises(ValidationError):
        PresentationSpecOut.model_validate(
            {
                **payload,
                "branding": {
                    "preset": "logo_v1",
                    "enabled": True,
                    "url": "https://example.test/logo.png",
                },
            }
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (source, "GTA " + roman)
        for digit, roman in [("4", "IV"), ("5", "V"), ("6", "VI")]
        for source in [
            f"GTA {digit}",
            f"GT A{digit}",
            f"GTA,{digit}",
            f"gta{digit}",
            f"gt A , {digit}",
            f"GTA. {digit}",
        ]
    ]
    + [
        ("Olá, GTA,5!", "Olá, GTA V!"),
        ("GTA IV e GTA VI", "GTA IV e GTA VI"),
        ("GT 4 e A6", "GT 4 e A6"),
        ("GTA 7 e GTA 40", "GTA 7 e GTA 40"),
        ("AGTA4 GTA5x GTA 5.1 GTA 6,5", "AGTA4 GTA5x GTA 5.1 GTA 6,5"),
        ("Forza 5 custa 4,50.", "Forza 5 custa 4,50."),
    ],
)
def test_gta_caption_display_only(source: str, expected: str) -> None:
    words = _words((source, 0.2, 0.7))
    original = [dict(word) for word in words]
    cues = segment_captions(words, duration_seconds=1.0)
    assert [(cue.text, cue.start_seconds, cue.end_seconds) for cue in cues] == [
        (expected, 0.2, 0.7)
    ]
    assert words == original


def test_gta_caption_display_joins_asr_tokens_without_changing_timing() -> None:
    words = _words(("GT", 0.0, 0.2), ("A", 0.2, 0.3), (",", 0.3, 0.4), ("6", 0.4, 0.7))
    cues = segment_captions(words, duration_seconds=1.0)
    assert [(cue.text, cue.start_seconds, cue.end_seconds) for cue in cues] == [
        ("GTA VI", 0.0, 0.7)
    ]


def test_gta_caption_display_wraps_normalized_text_within_line_limit() -> None:
    source = "Uma comparação detalhada entre GTA4 e GTA6"
    cues = segment_captions(_words((source, 0.0, 0.7)), duration_seconds=1.0)
    assert cues[0].text.replace("\n", " ") == "Uma comparação detalhada entre GTA IV e GTA VI"
    assert all(len(line) <= 42 for line in cues[0].text.split("\n"))
    assert (cues[0].start_seconds, cues[0].end_seconds) == (0.0, 0.7)


def test_faceless_pop_culture_lists_disables_burned_in_captions() -> None:
    """Regression: faceless_pop_culture_lists must have burned_in_captions=False."""
    from app.services.visual_director_service import (
        PRODUCTION_PROFILE_CONFIGS,
        resolve_production_profile_config,
    )

    # Verify the config directly
    lists_config = PRODUCTION_PROFILE_CONFIGS.get("faceless_pop_culture_lists")
    assert lists_config is not None, "faceless_pop_culture_lists config missing"
    assert lists_config.burned_in_captions is False, (
        "faceless_pop_culture_lists must have burned_in_captions=False"
    )

    # Verify via resolver
    resolved = resolve_production_profile_config("faceless_pop_culture_lists")
    assert resolved is not None
    assert resolved.burned_in_captions is False

    # Verify other profiles preserve captions (default True)
    for profile_id in ["faceless_general", "faceless_informational", "faceless_pop_culture", "game_analysis"]:
        config = resolve_production_profile_config(profile_id)
        assert config is not None, f"{profile_id} config missing"
        assert config.burned_in_captions is True, f"{profile_id} must preserve default burned_in_captions=True"


def test_presentation_spec_omits_captions_when_profile_disables_them() -> None:
    """Regression: PresentationSpec.captions is None when burned_in_captions=False."""
    from app.services.visual_director_service import resolve_production_profile_config

    words = _words(("Teste", 0.0, 0.5), ("de", 0.5, 0.7), ("legenda.", 0.7, 1.2))
    workflow = _workflow(words)

    # Simulate faceless_pop_culture_lists profile
    profile_config = resolve_production_profile_config("faceless_pop_culture_lists")
    assert profile_config is not None
    burned_in_captions = profile_config.burned_in_captions

    spec = build_presentation_spec(
        workflow,
        duration_seconds=6.0,
        burned_in_captions=burned_in_captions,
    )
    assert spec is not None
    assert spec.captions is None, "faceless_pop_culture_lists must produce PresentationSpec with captions=None"

    # Verify default profile produces captions
    default_profile = resolve_production_profile_config("faceless_general")
    assert default_profile is not None
    spec_with_captions = build_presentation_spec(
        workflow,
        duration_seconds=6.0,
        burned_in_captions=default_profile.burned_in_captions,
    )
    assert spec_with_captions is not None
    assert spec_with_captions.captions is not None, "default profile must produce captions"
