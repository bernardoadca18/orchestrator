from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.workflow import AudioMixPlanV1Out, ProgramMusicAssetOut
from app.services.program_audio import (
    AudioIntent,
    MusicRegistryDocument,
    ProgramAudioError,
    build_audio_mix_plan,
    build_speech_regions,
    canonical_hash,
    resolve_music_asset,
)

FIXTURE = Path(__file__).parent / "fixtures" / "audio_mix_plan_v1_contract.json"


def asset(asset_id: str = "calm-01", **overrides: object) -> ProgramMusicAssetOut:
    values = {
        "asset_id": asset_id,
        "sha256": ("a" if asset_id == "calm-01" else "b") * 64,
        "byte_size": 1234,
        "content_type": "audio/wav",
        "format": "wav",
        "duration_seconds": 12.0,
        "sample_rate": 48_000,
        "channels": 2,
        "integrated_lufs": -22.5,
        "true_peak_dbtp": -2.0,
        "moods": ["analytical"],
        "styles": ["documentary"],
        "energy": 0.4,
        "vocals": False,
        "loopable": True,
        "source_owner": "NeoVids operator",
        "source_reference": "operator-owned fixture",
        "license_type": "owned",
        "monetization_allowed": True,
        "attribution_required": False,
        "attribution_text": None,
    }
    values.update(overrides)
    return ProgramMusicAssetOut.model_validate(values)


def registry(*assets: ProgramMusicAssetOut) -> MusicRegistryDocument:
    return MusicRegistryDocument(
        schema_version="music_asset_registry_v1",
        assets=list(assets),
    )


def workflow(*, energy: float = 0.4, transition: str | None = "smooth") -> SimpleNamespace:
    workflow_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    return SimpleNamespace(
        id=workflow_id,
        roteiro_snapshot={
            "blocos": [
                {
                    "audio_intent": {
                        "music_enabled": True,
                        "moods": ["Analytical"],
                        "styles": ["Documentary"],
                        "energy": energy,
                        "vocals": False,
                        "transition": transition,
                        "sfx_intent": ["future-only"],
                    }
                },
                {
                    "audio_intent": {
                        "music_enabled": False,
                        "moods": [],
                        "styles": [],
                        "energy": None,
                        "vocals": False,
                        "transition": None,
                        "sfx_intent": [],
                    }
                },
            ]
        },
        audio_final_sha256="f" * 64,
        duracao_audio_segundos=6.0,
        audio_final_sample_rate=48_000,
        audio_final_channels=1,
        transcricao=SimpleNamespace(
            audio_sha256="f" * 64,
            palavras_timestamps=[
                {"palavra": "um", "inicio": 0.4, "fim": 0.8},
                {"palavra": "dois", "inicio": 0.9, "fim": 1.2},
                {"palavra": "tres", "inicio": 3.0, "fim": 3.3},
            ],
        ),
        blocos=[
            SimpleNamespace(ordem=0, narration_start_seconds=0.0, narration_end_seconds=3.0),
            SimpleNamespace(ordem=1, narration_start_seconds=3.0, narration_end_seconds=6.0),
        ],
    )


def timeline() -> SimpleNamespace:
    return SimpleNamespace(duration_seconds=6.0)


def test_registry_rejects_duplicate_identity_and_unsafe_licensing() -> None:
    with pytest.raises(ValidationError, match="duplicate music asset id"):
        registry(asset(), asset(sha256="c" * 64))
    with pytest.raises(ValidationError):
        asset(sha256="not-a-sha")
    with pytest.raises(ValidationError):
        asset(license_type="unknown")
    with pytest.raises(ValidationError):
        asset(monetization_allowed=False)
    with pytest.raises(ValidationError, match="attribution text"):
        asset(attribution_required=True)


def test_resolver_is_deterministic_and_override_wins() -> None:
    intent = AudioIntent(
        music_enabled=True,
        moods=["analytical"],
        styles=["documentary"],
        energy=0.4,
    )
    inferior = asset(
        "ambient-02",
        moods=["analytical"],
        styles=["ambient"],
        energy=0.9,
    )
    catalog = registry(inferior, asset())
    workflow_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    assert resolve_music_asset(catalog, [intent], workflow_id=workflow_id).asset_id == "calm-01"
    reordered = registry(asset(), inferior)
    assert resolve_music_asset(reordered, [intent], workflow_id=workflow_id).asset_id == "calm-01"
    assert (
        resolve_music_asset(
            catalog, [intent], workflow_id=workflow_id, override_asset_id="ambient-02"
        ).asset_id
        == "ambient-02"
    )


def test_resolver_rejects_incompatible_vocals_and_no_match() -> None:
    workflow_id = uuid.uuid4()
    with pytest.raises(ProgramAudioError, match="vocals"):
        resolve_music_asset(
            registry(asset()),
            [AudioIntent(music_enabled=True, vocals=True)],
            workflow_id=workflow_id,
        )
    with pytest.raises(ProgramAudioError, match="No compatible"):
        resolve_music_asset(
            registry(asset()),
            [AudioIntent(music_enabled=True, moods=["dramatic"], styles=["orchestral"])],
            workflow_id=workflow_id,
        )


def test_speech_regions_merge_close_words_preserve_pause_and_bound_size() -> None:
    regions = build_speech_regions(
        [
            {"inicio": 1.0, "fim": 1.2},
            {"inicio": 0.0, "fim": 0.4},
            {"inicio": 0.5, "fim": 0.9},
            {"inicio": 9.8, "fim": 10.5},
        ],
        duration_seconds=10,
    )
    assert regions == [
        {"start_seconds": 0.0, "end_seconds": 1.2},
        {"start_seconds": 9.8, "end_seconds": 10.0},
    ]
    with pytest.raises(ProgramAudioError, match="bounded size"):
        build_speech_regions(
            [
                {"inicio": index * 2.0, "fim": index * 2.0 + 0.1}
                for index in range(513)
            ],
            duration_seconds=2000,
        )


def test_plan_is_strict_fingerprinted_and_preserves_music_off_range() -> None:
    plan = build_audio_mix_plan(
        workflow(),
        timeline(),
        Settings(_env_file=None),
        registry=registry(asset()),
    )
    assert plan is not None and plan.music.enabled
    assert len(plan.music.segments) == 1
    assert plan.music.segments[0].end_seconds == 3.0
    payload = plan.model_dump(mode="json")
    declared = payload.pop("fingerprint")
    assert canonical_hash(payload) == declared
    changed = copy.deepcopy(payload)
    changed["ducking"]["ducked_gain_db"] = -19.0
    assert canonical_hash(changed) != declared
    changed = copy.deepcopy(payload)
    changed["program_master"]["target_lufs"] = -15.0
    assert canonical_hash(changed) != declared
    with pytest.raises(ValidationError):
        AudioMixPlanV1Out.model_validate({**payload, "fingerprint": declared, "path": "/tmp/x"})


def test_mirrored_cross_service_fixture_has_javascript_compatible_identity() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = AudioMixPlanV1Out.model_validate(payload)
    content = parsed.model_dump(mode="json")
    declared = content.pop("fingerprint")
    assert canonical_hash(content) == declared


def test_plan_changes_with_audible_energy_and_rejects_unknown_transition() -> None:
    first = build_audio_mix_plan(
        workflow(energy=0.2), timeline(), Settings(_env_file=None), registry=registry(asset())
    )
    second = build_audio_mix_plan(
        workflow(energy=0.8), timeline(), Settings(_env_file=None), registry=registry(asset())
    )
    assert first and second and first.fingerprint != second.fingerprint
    with pytest.raises(ProgramAudioError, match="transition"):
        build_audio_mix_plan(
            workflow(transition="arbitrary-filter"),
            timeline(),
            Settings(_env_file=None),
            registry=registry(asset()),
        )
