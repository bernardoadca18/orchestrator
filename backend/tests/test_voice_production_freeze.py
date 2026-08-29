from app.core.config import Settings
from app.services.audio_service import derive_quality_retry_seed


def test_production_voice_defaults_are_frozen_and_overrideable() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.voice_profile == "bernardo_xvec_v2"
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
