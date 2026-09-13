from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.workflow import PresentationSpecOut
from app.services.presentation_spec import (
    PresentationInputError,
    build_presentation_spec,
    canonical_hash,
    segment_captions,
)

FIXTURE = Path(__file__).parent / "fixtures" / "presentation_spec_v1_contract.json"


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
    "words,duration",
    [
        (_words(("bad", -0.1, 0.2)), 1.0),
        (_words(("bad", 0.4, 0.3)), 1.0),
        (_words(("one", 0.0, 0.5), ("overlap", 0.4, 0.8)), 1.0),
        (_words(("late", 0.8, 1.1)), 1.0),
        ([{"palavra": "missing", "inicio": 0.0}], 1.0),
    ],
)
def test_caption_policy_rejects_invalid_timestamps(words: list[dict], duration: float) -> None:
    with pytest.raises(PresentationInputError):
        segment_captions(words, duration_seconds=duration)


def test_cue_may_end_exactly_at_audio_duration() -> None:
    cues = segment_captions(_words(("fim.", 0.5, 1.0)), duration_seconds=1.0)
    assert cues[0].end_seconds == 1.0


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
