from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routers.workflow import (
    aplicar_edicoes_pendentes,
    promover_chunk_take,
    regenerar_chunk,
)
from app.models.workflow import AudioChunkTake
from app.services.production_audio import inspect_wav


@pytest.mark.asyncio
async def test_regeneration_persists_exact_candidate_without_changing_current_take(
    tmp_path: Path, write_tone
) -> None:
    candidate_wav = write_tone(tmp_path / "remote-candidate.wav", frequency=330.0)
    candidate_bytes = candidate_wav.read_bytes()
    metadata = inspect_wav(candidate_wav)
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    current_take_id = uuid.uuid4()
    candidate_take_id = uuid.uuid4()
    block = SimpleNamespace(
        id=block_id,
        workflow_execucao_id=workflow_id,
        status="concluido",
    )
    chunk = SimpleNamespace(
        id=chunk_id,
        audio_bloco_id=block_id,
        chunk_index=3,
        synthesis_text_snapshot="Síntese física congelada.",
        source_job_id="a" * 32,
        current_take_id=current_take_id,
        takes=[SimpleNamespace(id=current_take_id)],
    )
    take = SimpleNamespace(
        id=candidate_take_id,
        audio_chunk_id=chunk_id,
        take_number=2,
        seed=901,
        status="queued",
        provider_candidate_id=None,
        audio_duration_seconds=None,
    )
    repository = MagicMock()
    repository.obter_bloco_por_id = AsyncMock(return_value=block)
    repository.obter_chunk_por_indice = AsyncMock(return_value=chunk)
    repository.obter_chunk = AsyncMock(return_value=chunk)
    repository.obter_chunk_take_por_idempotency = AsyncMock(return_value=None)

    async def create_take(_chunk_id, seed, **_values):
        take.seed = seed
        return take

    repository.criar_chunk_take = AsyncMock(side_effect=create_take)

    async def update_take(_take_id, **values):
        for name, value in values.items():
            setattr(take, name, value)

    repository.atualizar_chunk_take = AsyncMock(side_effect=update_take)
    session = MagicMock()
    session.refresh = AsyncMock()

    class FakeVoiceClient:
        regenerated: ClassVar[list[tuple[str, int, int, str]]] = []
        downloaded: ClassVar[list[tuple[str, str]]] = []

        def __init__(self, _settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def regenerate_chunk(
            self, job_id, chunk_index, seed, *, idempotency_key
        ):
            self.regenerated.append((job_id, chunk_index, seed, idempotency_key))
            return {
                "candidate_id": "b" * 32,
                "job_id": job_id,
                "chunk_index": chunk_index,
                "status": "completed",
                "synthesis_text": chunk.synthesis_text_snapshot,
                "seed": seed,
                "sha256": metadata.sha256,
                "byte_size": metadata.byte_size,
                "sample_rate": metadata.sample_rate,
                "duration_seconds": metadata.duration_seconds,
                "frame_count": metadata.frame_count,
                "language": "Portuguese",
                "speaker_similarity": 0.991,
                "speaker_similarity_reference_identity": "r" * 64,
                "speaker_similarity_policy_version": "qwen_ecapa_warning_v1",
                "speaker_identity_status": "passed",
            }

        async def download_chunk_candidate_audio(self, job_id, candidate_id):
            self.downloaded.append((job_id, candidate_id))
            return candidate_bytes

    config = SimpleNamespace(
        audio_storage_path=str(tmp_path / "audio"), voice_language="Portuguese"
    )
    with (
        patch(
            "app.api.routers.workflow.WorkflowRepository",
            return_value=repository,
        ),
        patch("app.clients.voice_client.VoiceClient", FakeVoiceClient),
        patch("app.core.config.settings", config),
    ):
        response = await regenerar_chunk(
            workflow_id=workflow_id,
            bloco_id=block_id,
            chunk_index=3,
            session=session,
            idempotency_key="workflow-chunk-key",
        )

    assert FakeVoiceClient.regenerated == [
        ("a" * 32, 3, take.seed, "workflow-chunk-key")
    ]
    assert FakeVoiceClient.downloaded == [("a" * 32, "b" * 32)]
    assert chunk.current_take_id == current_take_id
    assert take.status == "completed"
    assert take.provider_candidate_id == "b" * 32
    assert take.speaker_similarity == pytest.approx(0.991)
    assert take.speaker_identity_status == "passed"
    stored = Path(take.audio_path)
    assert stored.read_bytes() == candidate_bytes
    assert stored.name == f"chunk_0003_take_{candidate_take_id}.wav"
    assert response["take_id"] == str(candidate_take_id)
    assert response["status"] == "candidate"
    assert not hasattr(repository, "invalidar_derivados_de_audio") or not (
        repository.invalidar_derivados_de_audio.called
    )


@pytest.mark.asyncio
async def test_promotion_only_selects_take_and_marks_pending_revision() -> None:
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    take_id = uuid.uuid4()
    take = SimpleNamespace(
        id=take_id,
        audio_chunk_id=chunk_id,
        status="completed",
    )
    chunk = SimpleNamespace(id=chunk_id, audio_bloco_id=block_id)
    block = SimpleNamespace(id=block_id, workflow_execucao_id=workflow_id)
    workflow = SimpleNamespace(
        id=workflow_id,
        audio_final_sha256="a" * 64,
        audio_edit_revision=4,
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=take)
    repository = MagicMock()
    repository.obter_chunk = AsyncMock(return_value=chunk)
    repository.obter_bloco_por_id = AsyncMock(return_value=block)
    repository.obter_por_id = AsyncMock(return_value=workflow)
    repository.promover_chunk_take = AsyncMock(return_value=True)

    with patch(
        "app.api.routers.workflow.WorkflowRepository", return_value=repository
    ):
        result = await promover_chunk_take(workflow_id, take_id, session)

    repository.promover_chunk_take.assert_awaited_once_with(
        take_id, workflow_id=workflow_id
    )
    assert result["audio_edits_pending"] is True
    assert result["final_audio_sha256"] == "a" * 64
    assert not hasattr(repository, "invalidar_derivados_de_audio") or not (
        repository.invalidar_derivados_de_audio.called
    )
    session.get.assert_awaited_once_with(AudioChunkTake, take_id)


@pytest.mark.asyncio
async def test_update_pipeline_request_is_enqueued_only_when_created() -> None:
    workflow_id = uuid.uuid4()
    update = SimpleNamespace(id=uuid.uuid4(), status="pending")
    workflow = SimpleNamespace(
        id=workflow_id,
        audio_edit_revision=3,
        audio_materialized_revision=1,
        audio_final_sha256="b" * 64,
    )
    repository = MagicMock()
    repository.request_audio_edit_update = AsyncMock(
        side_effect=[(update, True), (update, False)]
    )
    repository.obter_por_id = AsyncMock(return_value=workflow)
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.aplicar_edicoes_audio.delay") as enqueue,
    ):
        response = await aplicar_edicoes_pendentes(
            workflow_id, MagicMock(), idempotency_key="apply-batch-1"
        )
        replay = await aplicar_edicoes_pendentes(
            workflow_id, MagicMock(), idempotency_key="apply-batch-1"
        )

    enqueue.assert_called_once_with(str(update.id))
    assert response.update_id == update.id
    assert response.status == "pending"
    assert response.audio_edits_pending is True
    assert replay.update_id == update.id and replay.created is False
