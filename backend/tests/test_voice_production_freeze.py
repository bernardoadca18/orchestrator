from app.core.config import Settings
from app.services.audio_service import derive_quality_retry_seed


def test_production_voice_defaults_are_frozen_and_overrideable() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.voice_profile == "bernardo_xvec_v3"
    assert defaults.voice_generation_profile == "narration_v2"
    assert defaults.voice_seed == 303
    assert defaults.voice_language == "Portuguese"

    override = Settings(
        _env_file=None,
        voice_profile="historical_voice",
        voice_generation_profile="historical_generation",
        voice_seed=101,
    )
    assert override.voice_profile == "historical_voice"
    assert override.voice_generation_profile == "historical_generation"
    assert override.voice_seed == 101


def test_quality_retry_derives_a_distinct_traceable_seed_from_production_default() -> None:
    first_retry = derive_quality_retry_seed(303, 1)
    assert first_retry != 303
    assert first_retry == derive_quality_retry_seed(303, 1)
    assert first_retry != derive_quality_retry_seed(303, 2)


def test_workflow_locale_resolves_current_pt_and_english_voice():
    from types import SimpleNamespace
    from app.services.content_locale import resolve_narration_voice, workflow_content_locale

    config = Settings(_env_file=None)
    assert workflow_content_locale(SimpleNamespace(roteiro_snapshot={})) == "pt-BR"
    pt = resolve_narration_voice("pt-BR", config)
    en = resolve_narration_voice("en-US", config)
    assert (pt.voice_profile, pt.generation_profile, pt.language, pt.seed) == (
        "bernardo_xvec_v3",
        "narration_v2",
        "Portuguese",
        303,
    )
    assert (en.voice_profile, en.generation_profile, en.language, en.seed, en.accent_mode) == (
        "bernardo_en_icl_v1",
        "narration_v2",
        "English",
        303,
        "off",
    )
    config.voice_profile = "operator_pt"
    config.voice_language = "operator_language"
    assert resolve_narration_voice("pt-BR", config).voice_profile == "operator_pt"
    assert resolve_narration_voice("pt-BR", config).language == "operator_language"
