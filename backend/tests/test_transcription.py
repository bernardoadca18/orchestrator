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
    constructor.assert_called_once_with(
        "tiny", device="cpu", compute_type="int8", local_files_only=False
    )
    assert first.text == "Olá mundo."
    assert [word.word for word in first.words] == ["Olá", "mundo"]
    assert first.language == "pt"


def test_blank_configured_asr_language_uses_autodetection(tmp_path: Path) -> None:
    model = MagicMock()
    model.transcribe.return_value = ([], SimpleNamespace(language=""))
    provider = FasterWhisperNarrationProvider(
        Settings(_env_file=None, transcription_language="  "),
    )
    provider._model = model

    result = provider.transcribe(tmp_path / "blank-language.wav")

    assert provider.config.transcription_language is None
    assert model.transcribe.call_args.kwargs["language"] is None
    assert result.language is None


def test_configured_asr_language_pt_is_passed_to_faster_whisper(tmp_path: Path) -> None:
    model = MagicMock()
    model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
    provider = FasterWhisperNarrationProvider(
        Settings(_env_file=None, transcription_language=None),
        asr_language="pt",
    )
    provider._model = model

    result = provider.transcribe(tmp_path / "portuguese.wav")

    assert model.transcribe.call_args.kwargs["language"] == "pt"
    assert result.language == "pt"


def test_none_asr_language_is_forwarded_for_generic_autodetection(tmp_path: Path) -> None:
    model = MagicMock()
    model.transcribe.return_value = ([], SimpleNamespace(language="en"))
    provider = FasterWhisperNarrationProvider(
        Settings(_env_file=None, transcription_language="pt"),
        asr_language=None,
    )
    provider._model = model

    result = provider.transcribe(tmp_path / "autodetect.wav")

    assert model.transcribe.call_args.kwargs["language"] is None
    assert result.language == "en"


def test_per_job_lifetime_releases_each_model_reference(tmp_path: Path) -> None:
    first_model = MagicMock()
    first_model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
    second_model = MagicMock()
    second_model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
    provider = FasterWhisperNarrationProvider(
        Settings(
            _env_file=None,
            transcription_model="tiny",
            transcription_device="cpu",
            transcription_compute_type="int8",
            transcription_model_lifetime="per_job",
        )
    )

    with patch(
        "faster_whisper.WhisperModel",
        side_effect=[first_model, second_model],
    ) as constructor:
        provider.transcribe(tmp_path / "first.wav")
        provider.transcribe(tmp_path / "second.wav")

    assert constructor.call_count == 2
    assert provider._model is None
    assert provider.model_diagnostics() == {
        "policy": "per_job",
        "model": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        "local_files_only": False,
        "resident_loaded": False,
        "active_transcriptions": 0,
        "model_loads": 2,
        "model_releases": 2,
        "inference_concurrency_limit": 1,
    }
