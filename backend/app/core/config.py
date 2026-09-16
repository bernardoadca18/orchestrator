"""Configuração central do orchestrator, via variáveis de ambiente."""

import re
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.timestamp_contract import WORD_TIMESTAMP_TOLERANCE_SECONDS

STATIC_IMAGE_MINIMUM_HOLD_SECONDS = 2.0
STATIC_IMAGE_PREFERRED_HOLD_SECONDS = 5.0
STATIC_IMAGE_MAXIMUM_HOLD_SECONDS = 8.0
STATIC_IMAGE_MAXIMUM_CONSECUTIVE_SECONDS = 15.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Banco de dados / fila ---------------------------------------------
    database_url: str = "postgresql+asyncpg://orchestrator:orchestrator@localhost:5434/orchestrator"
    redis_url: str = "redis://localhost:6380/0"

    # --- Serviços integrados -------------------------------------------------
    ideas_hub_api_url: str = "http://localhost:8000"
    ideas_hub_api_timeout: int = 30
    ideas_hub_http_max_retries: int = 2
    ideas_hub_http_backoff_seconds: float = 0.25

    voice_api_url: str = "http://localhost:8889"
    voice_profile: str = "bernardo_xvec_v3"
    voice_generation_profile: str = "narration_v2"
    voice_language: str = "Portuguese"
    voice_seed: int = 303
    speaking_rate_fallback_words_per_minute: float = 145.0
    speaking_rate_fallback_chars_per_second: float = 14.5
    voice_poll_interval_seconds: float = 5.0
    voice_job_timeout_seconds: float = 1800.0
    voice_create_timeout_seconds: float = 30.0
    voice_status_timeout_seconds: float = 15.0
    voice_download_timeout_seconds: float = 120.0
    voice_http_max_retries: int = 2
    voice_http_backoff_seconds: float = 1.0
    voice_quality_max_retries: int = 2

    media_indexer_api_url: str = "http://localhost:8012"
    media_indexer_api_timeout_seconds: float = 30.0
    media_indexer_http_max_retries: int = 2
    media_indexer_http_backoff_seconds: float = 0.25

    video_composer_api_url: str = "http://localhost:8013"
    video_composer_http_timeout_seconds: float = 15.0
    video_composer_http_max_retries: int = 2
    video_composer_http_backoff_seconds: float = 0.5
    video_composer_poll_interval_seconds: float = 2.0
    video_composer_render_timeout_seconds: float = 3600.0
    video_composer_timeout_grace_seconds: float = 120.0
    video_composer_render_profile: str = "youtube_1080p30_v1"
    video_composer_program_audio_render_profile: str = "youtube_1080p30_program_audio_v1"

    production_qa_strategy_version: str = "production_qa_v2"
    production_qa_deep_max_video_bytes: int = 10 * 1024 * 1024 * 1024
    production_qa_task_max_retries: int = 2
    production_qa_task_retry_seconds: float = 5.0

    # --- Armazenamento de áudio ----------------------------------------------
    audio_storage_path: str = "/data/audio"
    music_library_root: Path = Path("/data/music")
    music_registry_path: Path = Path("/data/music/registry.json")
    pronunciation_glossary_path: Path = Path("config/pronunciation_glossary.yaml")
    pronunciation_locale: str = "pt-BR"
    text_normalizer_version: str = "pt_br_text_normalizer_v1"

    # --- Parâmetros calibráveis do fluxo -------------------------------------
    # Silêncio inserido entre blocos narrativos na concatenação final.
    silencio_entre_blocos_ms: int = 500
    audio_final_sample_rate: int = 48_000
    audio_target_lufs: float = -16.0
    audio_max_true_peak_db: float = -1.0
    audio_true_peak_tolerance_db: float = 0.02
    audio_true_peak_safety_margin_db: float = 0.05
    audio_min_acceptable_lufs: float = -18.0
    audio_max_acceptable_lufs: float = -15.0
    audio_duration_tolerance_seconds: float = 0.01
    audio_mastering_compressor_enabled: bool = True
    audio_mastering_compressor_threshold_db: float = -18.0
    audio_mastering_compressor_ratio: float = 2.0
    audio_mastering_compressor_attack_ms: float = 15.0
    audio_mastering_compressor_release_ms: float = 220.0
    audio_mastering_compressor_knee: float = 2.5
    audio_mastering_loudness_range_target_lu: float = 9.0
    audio_block_max_duration_seconds: float = 120.0
    audio_block_max_seconds_per_char: float = 0.5
    audio_block_duration_grace_seconds: float = 15.0
    audio_take_content_validation_enabled: bool = True
    audio_accent_qa_mode: Literal["off", "shadow", "enforce"] = "shadow"
    audio_accent_qa_model_path: Path = Path("config/accent_detector_model.json")
    audio_content_minimum_coverage_ratio: float = 0.72
    audio_content_maximum_deletion_ratio: float = 0.22
    audio_content_maximum_insertion_ratio: float = 0.30
    audio_content_maximum_repetition_ratio: float = 0.20
    audio_content_minimum_asr_confidence: float = 0.50
    audio_content_maximum_missing_tokens: int = 6
    audio_content_maximum_trailing_missing_tokens: int = 4
    audio_diagnostics_silence_threshold_dbfs: float = -45.0
    audio_diagnostics_minimum_silence_seconds: float = 0.15
    workflow_max_tentativas: int = 3

    transcription_model: str = "small"
    transcription_device: str = "cpu"
    transcription_compute_type: str = "int8"
    transcription_language: str | None = None
    transcription_local_files_only: bool = False
    transcription_model_lifetime: Literal["resident", "per_job"] = "resident"

    narration_alignment_strategy_version: str = "narration_alignment_v3"
    narration_alignment_fuzzy_min_score: float = 0.72
    narration_alignment_ambiguity_delta: float = 0.03
    narration_alignment_min_speech_coverage: float = 0.60
    narration_alignment_block_tolerance_seconds: float = 0.05
    narration_alignment_timestamp_tolerance_seconds: float = WORD_TIMESTAMP_TOLERANCE_SECONDS
    narration_alignment_max_tokens: int = 2_500
    narration_alignment_max_cells: int = 2_000_000
    narration_quality_policy_version: str = "narration_speech_quality_v2"
    narration_quality_max_repeated_ngram_occurrences: int = 4

    visual_director_strategy_version: str = "visual_director_v4_evidence_band"
    visual_director_max_queries: int = 100
    visual_director_search_top_k: int = 5
    visual_director_max_alternatives: int = 2
    visual_director_search_concurrency: int = 4
    visual_director_min_semantic_score: float = 0.25
    visual_director_media_search_strategy_version: str = "media_search_v4_hybrid_evidence"
    visual_director_hybrid_strategy_version: str = "visual_director_v2_hybrid_sources"
    hybrid_visual_fusion_version: str = "hybrid_visual_fusion_v1"
    visual_director_image_search_top_k: int = 5
    visual_director_image_min_semantic_score: float = 0.25
    visual_director_image_filter_combinations_max: int = 16
    hybrid_visual_fusion_rrf_k: int = 60
    hybrid_visual_fusion_video_weight: float = 1.0
    hybrid_visual_fusion_image_weight: float = 1.0
    visual_diversity_policy_version: str = "visual_diversity_policy_v1"
    visual_diversity_hybrid_policy_version: str = "visual_diversity_policy_v2"
    visual_diversity_minimum_window_count: int = 4
    visual_diversity_maximum_reuse_ratio: float = 0.75
    visual_diversity_maximum_dominant_window_ratio: float = 0.75
    visual_diversity_maximum_dominant_static_ratio: float = 0.65
    visual_diversity_maximum_static_reuse_count: int = 3

    production_timeline_strategy_version: str = "production_timeline_v1"
    production_timeline_schema_version: str = "production_timeline_schema_v1"
    production_timeline_validator_version: str = "production_timeline_validator_v1"
    production_timeline_hybrid_strategy_version: str = "production_timeline_v2"
    production_timeline_hybrid_schema_version: str = "production_timeline_schema_v2"
    production_timeline_hybrid_validator_version: str = "production_timeline_validator_v2"
    production_timeline_min_visual_seconds: float = 0.5
    production_timeline_max_sources_per_query: int = 2
    production_timeline_hybrid_max_sources_per_query: int = 4
    production_timeline_gap_fill_enabled: bool = True
    production_timeline_coalesce_contiguous: bool = True
    static_image_presentation_policy_version: str = "static_image_presentation_v1"
    static_image_minimum_hold_seconds: float = STATIC_IMAGE_MINIMUM_HOLD_SECONDS
    static_image_preferred_hold_seconds: float = STATIC_IMAGE_PREFERRED_HOLD_SECONDS
    static_image_maximum_hold_seconds: float = STATIC_IMAGE_MAXIMUM_HOLD_SECONDS
    static_image_maximum_consecutive_seconds: float = STATIC_IMAGE_MAXIMUM_CONSECUTIVE_SECONDS

    # --- API -----------------------------------------------------------------
    api_port: int = 8001
    cors_origins: str = "http://localhost:5174"

    # --- Logging --------------------------------------------------------------
    json_logging: bool = True

    @model_validator(mode="after")
    def validate_audio_quality_limits(self) -> "Settings":
        if self.audio_block_duration_grace_seconds >= self.audio_block_max_duration_seconds:
            raise ValueError("audio block duration grace must be below the absolute duration limit")
        return self

    @field_validator(
        "voice_api_url", "ideas_hub_api_url", "media_indexer_api_url", "video_composer_api_url"
    )
    @classmethod
    def strip_service_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("audio_final_sample_rate")
    @classmethod
    def validate_sample_rate(cls, value: int) -> int:
        if value < 8_000 or value > 192_000:
            raise ValueError("audio_final_sample_rate must be between 8000 and 192000")
        return value

    @field_validator("transcription_language", mode="before")
    @classmethod
    def normalize_transcription_language(cls, value: str | None) -> str | None:
        """Use ``None`` for Faster-Whisper language autodetection."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("voice_seed")
    @classmethod
    def validate_seed(cls, value: int) -> int:
        if value < 0 or value > 2**31 - 1:
            raise ValueError("voice_seed must be between 0 and 2147483647")
        return value

    @field_validator(
        "narration_alignment_fuzzy_min_score",
        "narration_alignment_ambiguity_delta",
        "narration_alignment_min_speech_coverage",
        "audio_content_minimum_coverage_ratio",
        "audio_content_maximum_deletion_ratio",
        "audio_content_maximum_insertion_ratio",
        "audio_content_maximum_repetition_ratio",
        "audio_content_minimum_asr_confidence",
    )
    @classmethod
    def validate_alignment_ratio(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("narration alignment ratios must be between 0 and 1")
        return value

    @field_validator(
        "narration_alignment_max_tokens",
        "narration_alignment_max_cells",
        "audio_content_maximum_missing_tokens",
        "audio_content_maximum_trailing_missing_tokens",
    )
    @classmethod
    def validate_alignment_guard(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("narration alignment guards must be positive")
        return value

    @field_validator(
        "text_normalizer_version",
        "narration_quality_policy_version",
        "visual_director_media_search_strategy_version",
        "visual_director_hybrid_strategy_version",
        "hybrid_visual_fusion_version",
        "production_timeline_hybrid_strategy_version",
        "production_timeline_hybrid_schema_version",
        "production_timeline_hybrid_validator_version",
        "static_image_presentation_policy_version",
    )
    @classmethod
    def validate_version_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("version identities must not be empty")
        return value

    @field_validator("pronunciation_locale")
    @classmethod
    def validate_pronunciation_locale(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value):
            raise ValueError("pronunciation_locale must be a BCP-47-like locale")
        return value

    @field_validator(
        "media_indexer_api_timeout_seconds",
        "media_indexer_http_backoff_seconds",
        "video_composer_http_timeout_seconds",
        "video_composer_http_backoff_seconds",
        "video_composer_poll_interval_seconds",
        "video_composer_render_timeout_seconds",
        "video_composer_timeout_grace_seconds",
        "production_qa_task_retry_seconds",
        "audio_block_max_duration_seconds",
        "audio_block_max_seconds_per_char",
    )
    @classmethod
    def validate_positive_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Media Indexer timeouts/backoff must be positive")
        return value

    @field_validator(
        "visual_director_max_queries",
        "visual_director_search_top_k",
        "visual_director_image_search_top_k",
        "visual_director_search_concurrency",
        "visual_director_image_filter_combinations_max",
        "hybrid_visual_fusion_rrf_k",
        "visual_diversity_minimum_window_count",
        "visual_diversity_maximum_static_reuse_count",
        "production_timeline_max_sources_per_query",
        "production_timeline_hybrid_max_sources_per_query",
    )
    @classmethod
    def validate_visual_director_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Visual Director limits must be positive")
        return value

    @field_validator(
        "production_timeline_min_visual_seconds",
        "static_image_minimum_hold_seconds",
        "static_image_preferred_hold_seconds",
        "static_image_maximum_hold_seconds",
        "static_image_maximum_consecutive_seconds",
        "hybrid_visual_fusion_video_weight",
        "hybrid_visual_fusion_image_weight",
    )
    @classmethod
    def validate_timeline_minimum_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("production timeline minimum duration must be positive")
        return value

    @field_validator(
        "visual_director_max_alternatives",
        "media_indexer_http_max_retries",
        "video_composer_http_max_retries",
        "production_qa_task_max_retries",
        "voice_quality_max_retries",
    )
    @classmethod
    def validate_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry/alternative limits must be non-negative")
        return value

    @field_validator("narration_quality_max_repeated_ngram_occurrences")
    @classmethod
    def validate_repetition_limit(cls, value: int) -> int:
        if value < 2:
            raise ValueError("repeated n-gram occurrence limit must be at least 2")
        return value

    @field_validator("audio_block_duration_grace_seconds")
    @classmethod
    def validate_block_duration_grace(cls, value: float) -> float:
        if value < 0:
            raise ValueError("audio block duration grace must be non-negative")
        return value

    @field_validator("production_qa_deep_max_video_bytes")
    @classmethod
    def validate_qa_deep_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("production QA deep size limit must be positive")
        return value

    @field_validator(
        "visual_director_min_semantic_score",
        "visual_director_image_min_semantic_score",
        "visual_diversity_maximum_reuse_ratio",
        "visual_diversity_maximum_dominant_window_ratio",
        "visual_diversity_maximum_dominant_static_ratio",
    )
    @classmethod
    def validate_visual_director_score(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("Visual Director score threshold must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_static_hold_policy(self) -> "Settings":
        if not (
            self.static_image_minimum_hold_seconds
            <= self.static_image_preferred_hold_seconds
            <= self.static_image_maximum_hold_seconds
            <= self.static_image_maximum_consecutive_seconds
        ):
            raise ValueError("static image hold limits must be monotonically increasing")
        return self


settings = Settings()
