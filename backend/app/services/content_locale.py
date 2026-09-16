"""Resolve narration configuration from the immutable script snapshot."""

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings


def workflow_content_locale(workflow: Any) -> str:
    locale = workflow.roteiro_snapshot.get("content_locale", "pt-BR")
    if locale not in {"pt-BR", "en-US"}:
        raise ValueError(f"Unsupported content locale: {locale}")
    return locale


@dataclass(frozen=True)
class NarrationVoice:
    voice_profile: str
    generation_profile: str
    language: str
    seed: int
    accent_mode: str


def resolve_narration_voice(locale: str, config: Settings) -> NarrationVoice:
    if locale not in {"pt-BR", "en-US"}:
        raise ValueError(f"Unsupported content locale: {locale}")
    english = locale == "en-US"
    return NarrationVoice(
        voice_profile=config.voice_profile_en_us if english else config.voice_profile,
        generation_profile=config.voice_generation_profile,
        language="English" if english else config.voice_language,
        seed=config.voice_seed,
        accent_mode="off" if english else config.audio_accent_qa_mode,
    )
