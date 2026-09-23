from __future__ import annotations

import csv
import io
import json
import wave
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.manual_edit_package import (
    ManualEditPackageError,
    build_package,
    materialize_package,
)
from app.services.production_audio import inspect_wav


def _fixture(tmp_path, locale: str):
    audio_path = tmp_path / "narration.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 32000)
    metadata = inspect_wav(audio_path)
    texts = (
        ["Hoje, criei uma IA.", 'O teste "final" funcionou.']
        if locale == "pt-BR"
        else ["Today, I built an AI.", 'The "final" test worked.']
    )
    blocks = [
        SimpleNamespace(
            id=uuid4(),
            ordem=index,
            audio_path=None,
            audio_sha256=None,
            narration_start_seconds=float(index),
            narration_end_seconds=float(index + 1),
        )
        for index in range(2)
    ]
    workflow = SimpleNamespace(
        id=uuid4(),
        roteiro_id=uuid4(),
        perfil_producao="gamedev_manual",
        roteiro_snapshot={
            "titulo": "Teste de IA",
            "content_locale": locale,
            "blocos": [{"tipo": "desenvolvimento", "conteudo": text} for text in texts],
        },
        blocos=blocks,
        audio_final_path=str(audio_path),
        audio_final_sha256=metadata.sha256,
        audio_final_byte_size=metadata.byte_size,
        audio_final_frame_count=metadata.frame_count,
        audio_final_subtype=metadata.subtype,
        audio_final_sample_rate=metadata.sample_rate,
        audio_final_channels=metadata.channels,
        duracao_audio_segundos=metadata.duration_seconds,
        mastering_config=None,
        mastering_metrics=None,
    )
    words = [
        {"palavra": "Hoje" if locale == "pt-BR" else "Today", "inicio": 0.1, "fim": 0.4},
        {"palavra": "criei" if locale == "pt-BR" else "built", "inicio": 0.45, "fim": 0.8},
        {"palavra": "Teste" if locale == "pt-BR" else "Test", "inicio": 1.1, "fim": 1.4},
    ]
    transcript = SimpleNamespace(audio_sha256=metadata.sha256, palavras_timestamps=words)
    return workflow, transcript, texts


@pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
def test_package_exports_snapshot_and_real_timestamps(tmp_path, locale):
    workflow, transcript, texts = _fixture(tmp_path, locale)
    manifest, files = build_package(workflow, transcript)
    assert manifest["schema_version"] == "manual_edit_package.v1"
    assert manifest["content_locale"] == locale
    assert manifest["files"]["narration"]["sha256"] == workflow.audio_final_sha256
    assert files["script.txt"].decode("utf-8") == "\n\n".join(texts) + "\n"
    structured = json.loads(files["script.json"])
    assert structured["blocks"][1]["start_time"] == 1
    assert "00:00:00,100" in files["captions.srt"].decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(files["markers.csv"].decode("utf-8"))))
    assert rows[1]["text"] == texts[1]
    assert rows[1]["start_seconds"] == "1.000"
    directory = materialize_package(tmp_path, workflow.id, manifest, files)
    assert all((directory / name).is_file() for name in files)
    assert materialize_package(tmp_path, workflow.id, manifest, files) == directory
    again, again_files = build_package(workflow, transcript)
    assert (again, again_files) == (manifest, files)
    transcript.palavras_timestamps[0]["fim"] = 0.5
    changed, _ = build_package(workflow, transcript)
    assert changed["identity"] != manifest["identity"]


def test_stale_transcript_is_rejected(tmp_path):
    workflow, transcript, _ = _fixture(tmp_path, "pt-BR")
    transcript.audio_sha256 = "0" * 64
    with pytest.raises(ManualEditPackageError, match="Matching final transcription"):
        build_package(workflow, transcript)


def test_new_final_audio_changes_package_identity(tmp_path):
    workflow, transcript, _ = _fixture(tmp_path, "en-US")
    old, _ = build_package(workflow, transcript)
    with wave.open(workflow.audio_final_path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 32000)
    metadata = inspect_wav(tmp_path / "narration.wav")
    workflow.audio_final_sha256 = metadata.sha256
    transcript.audio_sha256 = metadata.sha256
    new, _ = build_package(workflow, transcript)
    assert new["identity"] != old["identity"]


@pytest.mark.asyncio
async def test_service_persists_once_and_reuses_artifacts(tmp_path):
    from unittest.mock import AsyncMock

    from app.services.manual_edit_package import ManualEditPackageService

    workflow, transcript, _ = _fixture(tmp_path, "pt-BR")
    workflow.transcricao = transcript
    workflow.manual_edit_package = None
    repo = SimpleNamespace(obter_por_id=AsyncMock(return_value=workflow))
    session = SimpleNamespace(commit=AsyncMock())
    service = ManualEditPackageService(session, repo, str(tmp_path))
    first = await service.ensure(workflow.id)
    second = await service.ensure(workflow.id)
    assert first == second
    session.commit.assert_awaited_once()
    assert (
        tmp_path / str(workflow.id) / "manual_edit_package" / first["identity"] / "manifest.json"
    ).is_file()
