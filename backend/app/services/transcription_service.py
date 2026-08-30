"""Lazy, provider-neutral transcription of the final narration WAV."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from app.core.config import Settings, settings


def normalize_asr_language(value: str | None) -> str | None:
    """Normalize optional Faster-Whisper language codes for autodetection."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


_ASR_LANGUAGE_FROM_CONFIG = object()


@dataclass(frozen=True)
class NarrationWord:
    word: str
    start_seconds: float
    end_seconds: float
    probability: float | None = None

    def to_legacy_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "palavra": self.word,
            "inicio": round(self.start_seconds, 3),
            "fim": round(self.end_seconds, 3),
        }
        if self.probability is not None:
            payload["probabilidade"] = round(self.probability, 6)
        return payload


@dataclass(frozen=True)
class NarrationTranscription:
    text: str
    words: tuple[NarrationWord, ...]
    provider: str
    model: str
    language: str | None


class NarrationTranscriptionProvider(Protocol):
    def transcribe(self, audio_path: Path) -> NarrationTranscription: ...


class FasterWhisperNarrationProvider:
    """Serializes inference and applies the configured model lifetime policy."""

    provider_name = "faster-whisper"

    def __init__(
        self,
        config: Settings = settings,
        *,
        asr_language: str | None | object = _ASR_LANGUAGE_FROM_CONFIG,
    ) -> None:
        self.config = config
        # Omitting the override uses the generic service configuration; an
        # explicit None deliberately requests Faster-Whisper autodetection.
        configured_language = (
            config.transcription_language
            if asr_language is _ASR_LANGUAGE_FROM_CONFIG
            else cast(str | None, asr_language)
        )
        self.asr_language = normalize_asr_language(configured_language)
        self._model: Any | None = None
        self._state_lock = threading.Lock()
        self._use_lock = threading.Lock()
        self._model_loads = 0
        self._model_releases = 0
        self._active_transcriptions = 0

    def _create_model(self) -> Any:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            self.config.transcription_model,
            device=self.config.transcription_device,
            compute_type=self.config.transcription_compute_type,
            local_files_only=self.config.transcription_local_files_only,
        )
        with self._state_lock:
            self._model_loads += 1
        return model

    def _get_resident_model(self) -> Any:
        if self._model is None:
            with self._state_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    self._model = WhisperModel(
                        self.config.transcription_model,
                        device=self.config.transcription_device,
                        compute_type=self.config.transcription_compute_type,
                        local_files_only=self.config.transcription_local_files_only,
                    )
                    self._model_loads += 1
        return self._model

    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        with self._use_lock:
            per_job = self.config.transcription_model_lifetime == "per_job"
            model = self._create_model() if per_job else self._get_resident_model()
            with self._state_lock:
                self._active_transcriptions += 1
            try:
                segments, info = model.transcribe(
                    str(audio_path),
                    language=self.asr_language,
                    word_timestamps=True,
                )
                text_parts: list[str] = []
                words: list[NarrationWord] = []
                for segment in segments:
                    text_parts.append(segment.text)
                    for word in segment.words or ():
                        if word.start is None or word.end is None:
                            continue
                        words.append(
                            NarrationWord(
                                word=word.word.strip(),
                                start_seconds=float(word.start),
                                end_seconds=float(word.end),
                                probability=(
                                    float(word.probability)
                                    if getattr(word, "probability", None) is not None
                                    else None
                                ),
                            )
                        )
                detected_language = normalize_asr_language(getattr(info, "language", None))
            finally:
                with self._state_lock:
                    self._active_transcriptions -= 1
                    if per_job:
                        self._model_releases += 1
                if per_job:
                    del model
        return NarrationTranscription(
            text=" ".join(part.strip() for part in text_parts if part.strip()),
            words=tuple(words),
            provider=self.provider_name,
            model=self.config.transcription_model,
            language=detected_language or self.asr_language,
        )

    def model_diagnostics(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "policy": self.config.transcription_model_lifetime,
                "model": self.config.transcription_model,
                "device": self.config.transcription_device,
                "compute_type": self.config.transcription_compute_type,
                "local_files_only": self.config.transcription_local_files_only,
                "resident_loaded": self._model is not None,
                "active_transcriptions": self._active_transcriptions,
                "model_loads": self._model_loads,
                "model_releases": self._model_releases,
                "inference_concurrency_limit": 1,
            }


_default_provider: FasterWhisperNarrationProvider | None = None
_provider_lock = threading.Lock()


def get_transcription_provider() -> FasterWhisperNarrationProvider:
    global _default_provider
    if _default_provider is None:
        with _provider_lock:
            if _default_provider is None:
                _default_provider = FasterWhisperNarrationProvider()
    return _default_provider
