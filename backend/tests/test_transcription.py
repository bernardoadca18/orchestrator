from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.services.transcription_service import FasterWhisperNarrationProvider


def test_faster_whisper_model_is_lazy_cached_and_words_are_preserved(tmp_path: Path) -> None:
    model = MagicMock()
    model.transcribe.return_value = (
        [
            SimpleNamespace(
                text=" Olá mundo.",
                words=[
                    SimpleNamespace(word=" Olá", start=0.1, end=0.4),
                    SimpleNamespace(word=" mundo", start=0.5, end=0.9),
                ],
            )
        ],
        SimpleNamespace(language="pt"),
    )
    provider = FasterWhisperNarrationProvider(
        Settings(
            _env_file=None,
            transcription_model="tiny",
            transcription_device="cpu",
            transcription_compute_type="int8",
        )
    )
    assert provider._model is None
    with patch("faster_whisper.WhisperModel", return_value=model) as constructor:
        first = provider.transcribe(tmp_path / "first.wav")
        provider.transcribe(tmp_path / "second.wav")
    constructor.assert_called_once_with("tiny", device="cpu", compute_type="int8")
    assert first.text == "Olá mundo."
    assert [word.word for word in first.words] == ["Olá", "mundo"]
    assert first.language == "pt"
