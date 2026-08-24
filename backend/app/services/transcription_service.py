"""Lazy, provider-neutral transcription of the final narration WAV."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings, settings


@dataclass(frozen=True)
class NarrationWord:
    word: str
    start_seconds: float
    end_seconds: float

    def to_legacy_dict(self) -> dict[str, object]:
        return {
            "palavra": self.word,
            "inicio": round(self.start_seconds, 3),
            "fim": round(self.end_seconds, 3),
        }


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
    """Loads one Faster-Whisper model lazily per worker process."""

    provider_name = "faster-whisper"

    def __init__(self, config: Settings = settings) -> None:
        self.config = config
        self._model: Any | None = None
        self._lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    self._model = WhisperModel(
                        self.config.transcription_model,
                        device=self.config.transcription_device,
                        compute_type=self.config.transcription_compute_type,
                    )
        return self._model

    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        segments, info = self._get_model().transcribe(
            str(audio_path),
            language=self.config.transcription_language,
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
                    )
                )
        language = getattr(info, "language", None)
        return NarrationTranscription(
            text=" ".join(part.strip() for part in text_parts if part.strip()),
            words=tuple(words),
            provider=self.provider_name,
            model=self.config.transcription_model,
            language=str(language) if language else self.config.transcription_language,
        )


_default_provider: FasterWhisperNarrationProvider | None = None
_provider_lock = threading.Lock()


def get_transcription_provider() -> FasterWhisperNarrationProvider:
    global _default_provider
    if _default_provider is None:
        with _provider_lock:
            if _default_provider is None:
                _default_provider = FasterWhisperNarrationProvider()
    return _default_provider
