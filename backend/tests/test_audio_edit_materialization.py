"""PostgreSQL integration coverage for batched physical narration edits.

Run with:
    POSTGRES_INTEGRATION=1 pytest tests/test_audio_edit_materialization.py -v
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.workflow import AudioBloco, AudioChunkTake, WorkflowExecucao
from app.repositories.workflow_repository import WorkflowRepository
from app.services.audio_edit_service import (
    AudioEditMaterializationError,
    AudioEditMaterializationService,
)
from app.services.production_audio import (
    assemble_narration,
    inspect_wav,
    publish_source_block,
    reassemble_block_from_chunks,
)
from app.services.transcription_service import NarrationTranscription, NarrationWord

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_INTEGRATION"),
    reason="Integration test requires POSTGRES_INTEGRATION=1 and running PostgreSQL",
)


class FakeTranscriber:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        self.calls.append(audio_path)
        if self.fail:
            raise RuntimeError("forced transcription failure")
        return NarrationTranscription(
            text="fala atualizada",
            words=(NarrationWord("fala", 0.0, 0.02),),
            provider="fake",
            model="fake-v1",
            language="pt",
        )


@pytest.fixture
async def engine():
    value = create_async_engine(settings.database_url, echo=False)
    yield value
    await value.dispose()


@pytest.fixture
async def session(engine):
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as value:
        yield value
        await value.rollback()


def _write_tone(path: Path, *, frames: int, frequency: float) -> Path:
    sample_rate = 24_000
    time = np.arange(frames, dtype=np.float64) / sample_rate
    sf.write(
        path,
        0.08 * np.sin(2 * np.pi * frequency * time),
        sample_rate,
        format="WAV",
        subtype="PCM_16",
    )
    return path


async def _create_workflow(
    session: AsyncSession,
    root: Path,
    *,
    workflow_id: uuid.UUID,
    block_count: int = 3,
) -> tuple[WorkflowExecucao, list[AudioBloco], dict[uuid.UUID, list[AudioChunkTake]]]:
    repo = WorkflowRepository(session)
    workflow = WorkflowExecucao(
        id=workflow_id,
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={"blocos": []},
        status="audio_concluido",
        audio_phase="completed",
        production_status="completed",
        production_phase="completed",
    )
    session.add(workflow)
    await session.flush()
    blocks: list[AudioBloco] = []
    takes_by_block: dict[uuid.UUID, list[AudioChunkTake]] = {}
    root.mkdir(parents=True, exist_ok=True)
    for order in range(block_count):
        block = AudioBloco(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow.id,
            ordem=order,
            tipo="desenvolvimento",
            texto=f"Bloco {order}",
            synthesis_text=f"Bloco {order}",
            status="concluido",
            voice_profile="test",
            voice_profile_identity="v" * 64,
            generation_profile="test",
            generation_profile_identity="g" * 64,
            job_id_voz=uuid.uuid4().hex,
        )
        session.add(block)
        await session.flush()
        chunk_rows: list[dict[str, object]] = []
        chunk_paths: list[Path] = []
        for index in range(2):
            path = _write_tone(
                root / f"block-{order}-chunk-{index}.wav",
                frames=1_200 + (index * 240),
                frequency=180 + (order * 70) + (index * 20),
            )
            metadata = inspect_wav(path)
            chunk_paths.append(path)
            chunk_rows.append(
                {
                    "chunk_index": index,
                    "chunk_id": f"{block.job_id_voz}_{index:04d}",
                    "text": f"Texto {order}.{index}",
                    "synthesis_text": f"Síntese {order}.{index}",
                    "start_char": index * 10,
                    "end_char": index * 10 + 9,
                    "boundary": "sentence" if index == 0 else "end",
                    "pause_after_frames": 120 if index == 0 else 0,
                    "chunking_identity": "c" * 64,
                    "chunking_strategy_version": "qwen3-tts-chunking-v2",
                    "voice_profile_identity": "v" * 64,
                    "generation_profile_identity": "g" * 64,
                    "source_job_id": block.job_id_voz,
                    "seed": 100 + index,
                    "duration_seconds": metadata.duration_seconds,
                    "sample_rate": metadata.sample_rate,
                    "audio_path": str(path),
                    "audio_sha256": metadata.sha256,
                    "audio_byte_size": metadata.byte_size,
                    "audio_frame_count": metadata.frame_count,
                }
            )
        await repo.persistir_chunks(block.id, chunk_rows)
        chunks = await repo.listar_chunks(block.id)
        takes_by_block[block.id] = [chunk.takes[0] for chunk in chunks]
        block_path = root / f"block-{order}.wav"
        block_audio = reassemble_block_from_chunks(
            chunk_paths,
            block_path,
            sample_rate=24_000,
            pause_frames=[120, 0],
        ).metadata
        block.audio_path = str(block_path)
        block.duracao_segundos = block_audio.duration_seconds
        block.audio_sha256 = block_audio.sha256
        block.audio_sample_rate = block_audio.sample_rate
        block.audio_channels = block_audio.channels
        block.audio_byte_size = block_audio.byte_size
        block.audio_frame_count = block_audio.frame_count
        block.audio_subtype = block_audio.subtype
        block.artifact_integrity = block_audio.__dict__
        blocks.append(block)
    await session.commit()

    assembled = assemble_narration(
        [Path(block.audio_path or "") for block in blocks],
        root / "old-raw.wav",
        sample_rate=48_000,
        silence_ms=10,
    )
    old_final = root / "old-final.wav"
    old_metadata = publish_source_block((root / "old-raw.wav").read_bytes(), old_final)
    workflow.audio_final_path = str(old_final)
    workflow.audio_final_sha256 = old_metadata.sha256
    workflow.duracao_audio_segundos = old_metadata.duration_seconds
    workflow.audio_final_sample_rate = old_metadata.sample_rate
    workflow.audio_final_channels = old_metadata.channels
    workflow.audio_final_byte_size = old_metadata.byte_size
    workflow.audio_final_frame_count = old_metadata.frame_count
    workflow.audio_final_subtype = old_metadata.subtype
    workflow.audio_integrity = {
        "status": "passed",
        "expected_frame_count": assembled.expected_frame_count,
    }
    await session.commit()
    return workflow, blocks, takes_by_block


async def _candidate(
    session: AsyncSession,
    root: Path,
    block: AudioBloco,
    chunk_index: int,
    *,
    frequency: float,
) -> AudioChunkTake:
    repo = WorkflowRepository(session)
    chunk = (await repo.listar_chunks(block.id))[chunk_index]
    take = await repo.criar_chunk_take(
        chunk.id,
        seed=900 + chunk_index,
        idempotency_key=f"candidate-{block.id}-{chunk_index}-{frequency}",
    )
    path = _write_tone(
        root / f"candidate-{take.id}.wav",
        frames=1_320 + (chunk_index * 240),
        frequency=frequency,
    )
    metadata = inspect_wav(path)
    await repo.atualizar_chunk_take(
        take.id,
        status="completed",
        audio_path=str(path),
        audio_sha256=metadata.sha256,
        audio_byte_size=metadata.byte_size,
        audio_sample_rate=metadata.sample_rate,
        audio_duration_seconds=metadata.duration_seconds,
        audio_frame_count=metadata.frame_count,
    )
    return take


def _fake_master(source: Path, destination: Path, config) -> SimpleNamespace:
    metadata = publish_source_block(source.read_bytes(), destination)
    return SimpleNamespace(
        metadata=metadata,
        to_dict=lambda: {"status": "pass", "test_fake": True},
    )


async def _cleanup(engine, workflow_id: uuid.UUID) -> None:
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as cleanup:
        workflow = await cleanup.get(WorkflowExecucao, workflow_id)
        if workflow is not None:
            await cleanup.delete(workflow)
            await cleanup.commit()


@pytest.mark.asyncio
async def test_multiple_promotions_materialize_dirty_blocks_once(
    session, engine, tmp_path: Path, monkeypatch
) -> None:
    import app.services.audio_edit_service as module

    workflow_id = uuid.uuid4()
    try:
        workflow, blocks, original_takes = await _create_workflow(
            session, tmp_path, workflow_id=workflow_id
        )
        repo = WorkflowRepository(session)
        old_final_path = workflow.audio_final_path
        old_final_sha = workflow.audio_final_sha256
        original_chunk_hashes = {
            take.id: take.audio_sha256
            for takes in original_takes.values()
            for take in takes
        }
        clean_block_path = blocks[1].audio_path
        clean_block_sha = blocks[1].audio_sha256
        original_block_hashes = {block.ordem: block.audio_sha256 for block in blocks}

        candidates = [
            await _candidate(session, tmp_path, blocks[0], 0, frequency=510),
            await _candidate(session, tmp_path, blocks[0], 1, frequency=540),
            await _candidate(session, tmp_path, blocks[2], 0, frequency=620),
        ]
        for candidate in candidates:
            assert await repo.promover_chunk_take(
                candidate.id, workflow_id=workflow.id
            )
        await session.refresh(workflow)
        assert workflow.audio_final_sha256 == old_final_sha
        assert workflow.audio_final_path == old_final_path
        assert workflow.audio_edit_revision == 3
        assert workflow.audio_materialized_revision == 0

        update, created = await repo.request_audio_edit_update(
            workflow.id, f"apply-{workflow.id}"
        )
        assert update is not None and created
        block_calls: list[list[Path]] = []
        narration_calls: list[list[Path]] = []
        master_calls: list[Path] = []
        real_block_assembler = module.reassemble_block_from_manifest
        real_narration_assembler = assemble_narration

        def count_blocks(manifest, destination):
            block_calls.append([item.audio_path for item in manifest.chunks])
            return real_block_assembler(manifest, destination)

        def count_narration(paths, destination, **kwargs):
            narration_calls.append(list(paths))
            return real_narration_assembler(paths, destination, **kwargs)

        def count_master(source, destination, config):
            master_calls.append(source)
            return _fake_master(source, destination, config)

        monkeypatch.setattr(module, "reassemble_block_from_manifest", count_blocks)
        monkeypatch.setattr(module, "assemble_narration", count_narration)
        monkeypatch.setattr(module, "master_narration", count_master)
        transcriber = FakeTranscriber()
        config = settings.model_copy(
            update={
                "audio_storage_path": str(tmp_path),
                "audio_final_sample_rate": 48_000,
                "silencio_entre_blocos_ms": 10,
            }
        )
        result = await AudioEditMaterializationService(
            session, config=config, transcription_provider=transcriber
        ).apply(update.id)

        assert result.status == "completed" and result.materialized
        assert len(block_calls) == 2  # Block A once despite two promoted chunks; Block C once.
        assert len(narration_calls) == len(master_calls) == len(transcriber.calls) == 1
        session.expire_all()
        current = await repo.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        assert current is not None
        assert current.audio_final_sha256 != old_final_sha
        assert current.audio_final_path != old_final_path
        assert current.audio_edit_revision == current.audio_materialized_revision == 3
        assert current.production_status == "queued"
        assert current.production_phase == "narration_alignment"
        assert current.transcricao is not None
        assert current.transcricao.audio_sha256 == current.audio_final_sha256
        assert current.blocos[1].audio_path == clean_block_path
        assert current.blocos[1].audio_sha256 == clean_block_sha
        assert current.blocos[0].audio_sha256 != original_block_hashes[0]
        assert current.blocos[2].audio_sha256 != original_block_hashes[2]
        for rebuilt_block in (current.blocos[0], current.blocos[2]):
            assert rebuilt_block.current_take_id is not None
            rebuilt_take = next(
                take
                for take in rebuilt_block.takes
                if take.id == rebuilt_block.current_take_id
            )
            assert rebuilt_take.kind == "chunk_edit_reassembly"
            assert rebuilt_take.audio_sha256 == rebuilt_block.audio_sha256
            assert rebuilt_take.audio_path == rebuilt_block.audio_path
        reloaded_hashes = {
            take.id: take.audio_sha256
            for block in current.blocos
            for chunk in await repo.listar_chunks(block.id)
            for take in chunk.takes
            if take.id in original_chunk_hashes
        }
        assert reloaded_hashes == original_chunk_hashes
        assert Path(old_final_path or "").is_file()
        assert inspect_wav(Path(old_final_path or "")).sha256 == old_final_sha
    finally:
        await session.rollback()
        await _cleanup(engine, workflow_id)


@pytest.mark.asyncio
async def test_mastering_failure_preserves_old_final_and_pending_state(
    session, engine, tmp_path: Path, monkeypatch
) -> None:
    import app.services.audio_edit_service as module

    workflow_id = uuid.uuid4()
    try:
        workflow, blocks, _ = await _create_workflow(
            session, tmp_path, workflow_id=workflow_id, block_count=1
        )
        repo = WorkflowRepository(session)
        old_path = workflow.audio_final_path
        old_sha = workflow.audio_final_sha256
        candidate = await _candidate(session, tmp_path, blocks[0], 0, frequency=700)
        assert await repo.promover_chunk_take(candidate.id, workflow_id=workflow.id)
        update, _ = await repo.request_audio_edit_update(
            workflow.id, f"failure-{workflow.id}"
        )
        assert update is not None
        update_id = update.id

        def fail_master(source, destination, config):
            raise RuntimeError("forced mastering failure")

        monkeypatch.setattr(module, "master_narration", fail_master)
        transcriber = FakeTranscriber()
        config = settings.model_copy(update={"audio_storage_path": str(tmp_path)})
        with pytest.raises(RuntimeError, match="forced mastering failure"):
            await AudioEditMaterializationService(
                session, config=config, transcription_provider=transcriber
            ).apply(update_id)

        session.expire_all()
        current = await repo.obter_por_id(workflow_id)
        failed = await repo.get_audio_edit_update(update_id)
        assert current is not None and failed is not None
        assert current.audio_final_path == old_path
        assert current.audio_final_sha256 == old_sha
        assert current.audio_edit_revision > current.audio_materialized_revision
        assert failed.status == "failed"
        assert failed.published_audio_sha256 is None
        assert not transcriber.calls
    finally:
        await session.rollback()
        await _cleanup(engine, workflow_id)


@pytest.mark.asyncio
async def test_downstream_failure_keeps_new_audio_and_is_retryable(
    session, engine, tmp_path: Path, monkeypatch
) -> None:
    import app.services.audio_edit_service as module

    workflow_id = uuid.uuid4()
    try:
        workflow, blocks, _ = await _create_workflow(
            session, tmp_path, workflow_id=workflow_id, block_count=1
        )
        repo = WorkflowRepository(session)
        old_sha = workflow.audio_final_sha256
        candidate = await _candidate(session, tmp_path, blocks[0], 0, frequency=760)
        assert await repo.promover_chunk_take(candidate.id, workflow_id=workflow.id)
        key = f"downstream-failure-{workflow.id}"
        update, _ = await repo.request_audio_edit_update(workflow.id, key)
        assert update is not None
        update_id = update.id
        monkeypatch.setattr(module, "master_narration", _fake_master)
        config = settings.model_copy(update={"audio_storage_path": str(tmp_path)})
        with pytest.raises(RuntimeError, match="forced transcription failure"):
            await AudioEditMaterializationService(
                session,
                config=config,
                transcription_provider=FakeTranscriber(fail=True),
            ).apply(update_id)

        session.expire_all()
        current = await repo.obter_por_id(workflow_id)
        failed = await repo.get_audio_edit_update(update_id)
        assert current is not None and failed is not None
        assert current.audio_final_sha256 != old_sha
        assert current.audio_edit_revision == current.audio_materialized_revision
        assert failed.status == "failed"
        assert failed.error_stage == "downstream_transcription"
        assert failed.published_audio_sha256 == current.audio_final_sha256
        assert current.production_status == "failed"
        replay, created = await repo.request_audio_edit_update(workflow_id, key)
        assert replay is not None and replay.id == update_id and created
        assert replay.status == "pending"
    finally:
        await session.rollback()
        await _cleanup(engine, workflow_id)


@pytest.mark.asyncio
async def test_newer_promotion_during_materialization_is_never_cleared(
    session, engine, tmp_path: Path, monkeypatch
) -> None:
    import app.services.audio_edit_service as module

    workflow_id = uuid.uuid4()
    try:
        workflow, blocks, _ = await _create_workflow(
            session, tmp_path, workflow_id=workflow_id, block_count=1
        )
        repo = WorkflowRepository(session)
        old_sha = workflow.audio_final_sha256
        first = await _candidate(session, tmp_path, blocks[0], 0, frequency=810)
        assert await repo.promover_chunk_take(first.id, workflow_id=workflow.id)
        update, _ = await repo.request_audio_edit_update(
            workflow.id, f"snapshot-race-{workflow.id}"
        )
        assert update is not None
        update_id = update.id
        claimed = await repo.claim_audio_edit_update(update_id)
        assert claimed is not None and claimed.edit_snapshot is not None

        second = await _candidate(session, tmp_path, blocks[0], 0, frequency=860)
        assert await repo.promover_chunk_take(second.id, workflow_id=workflow.id)
        monkeypatch.setattr(module, "master_narration", _fake_master)
        config = settings.model_copy(update={"audio_storage_path": str(tmp_path)})
        with pytest.raises(AudioEditMaterializationError, match="edit_snapshot_stale"):
            await AudioEditMaterializationService(
                session, config=config, transcription_provider=FakeTranscriber()
            ).apply(update_id, recovery=True)

        session.expire_all()
        current = await repo.obter_por_id(workflow_id)
        failed = await repo.get_audio_edit_update(update_id)
        assert current is not None and failed is not None
        assert current.audio_final_sha256 == old_sha
        assert current.audio_edit_revision == 2
        assert current.audio_materialized_revision == 0
        assert failed.status == "failed"
        assert failed.error_stage == "edit_snapshot_stale"
    finally:
        await session.rollback()
        await _cleanup(engine, workflow_id)
