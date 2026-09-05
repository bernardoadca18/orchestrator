"""PostgreSQL coverage for durable physical chunks and immutable take history.

Run with:
    POSTGRES_INTEGRATION=1 pytest tests/test_audio_chunks.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_INTEGRATION"),
    reason="Integration test requires POSTGRES_INTEGRATION=1 and running PostgreSQL",
)


@pytest.fixture
async def engine():
    from app.core.config import settings

    value = create_async_engine(settings.database_url, echo=False)
    yield value
    await value.dispose()


@pytest.fixture
async def session(engine):
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as value:
        yield value
        await value.rollback()


@pytest.fixture
async def block(engine, session):
    from app.models.workflow import AudioBloco, WorkflowExecucao

    workflow = WorkflowExecucao(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={"blocos": []},
        status="audio_concluido",
    )
    value = AudioBloco(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow.id,
        ordem=0,
        tipo="introducao",
        texto="Primeiro bloco de teste.",
        synthesis_text="Primeiro bloco de teste.",
        status="concluido",
        voice_profile="test_voice",
        voice_profile_identity="v" * 64,
        generation_profile="test_generation",
        generation_profile_identity="g" * 64,
        job_id_voz="a" * 32,
    )
    session.add_all([workflow, value])
    await session.commit()
    workflow_id = workflow.id
    block_id = value.id
    try:
        yield value
    finally:
        # Use a fresh session so cleanup is not coupled to the transaction state
        # left by an integrity-error test.
        await session.rollback()
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as cleanup:
            await cleanup.execute(
                AudioBloco.__table__.delete().where(AudioBloco.id == block_id)
            )
            await cleanup.execute(
                WorkflowExecucao.__table__.delete().where(
                    WorkflowExecucao.id == workflow_id
                )
            )
            await cleanup.commit()


def _chunk_data(index: int, source_job_id: str) -> dict[str, object]:
    start = index * 10
    return {
        "chunk_index": index,
        "chunk_id": f"{source_job_id}_{index:04d}",
        "text": f"Texto {index}",
        "synthesis_text": f"Síntese congelada {index}",
        "start_char": start,
        "end_char": start + 9,
        "boundary": "sentence" if index % 2 == 0 else "paragraph",
        "pause_after_frames": 0 if index == 3 else 2_400 + index,
        "chunking_identity": "c" * 64,
        "chunking_strategy_version": "qwen3-tts-chunking-v2",
        "voice_profile_identity": "v" * 64,
        "generation_profile_identity": "g" * 64,
        "source_job_id": source_job_id,
        "seed": 100 + index,
        "duration_seconds": 1.0 + index,
        "sample_rate": 24_000,
        "audio_path": f"/tmp/chunk_{index}.wav",
        "audio_sha256": f"{index + 1:x}" * 64,
        "audio_byte_size": 48_044 + index,
        "audio_frame_count": 24_000 * (index + 1),
    }


@pytest.mark.asyncio
async def test_initial_chunks_and_plan_provenance_survive_reload(session, block) -> None:
    from app.models.workflow import AudioChunkTake
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    source_job_id = block.id.hex
    persisted = await repo.persistir_chunks(
        block.id, [_chunk_data(0, source_job_id), _chunk_data(1, source_job_id)]
    )
    assert len(persisted) == 2

    reloaded = await repo.listar_chunks(block.id)
    assert [chunk.chunk_index for chunk in reloaded] == [0, 1]
    assert reloaded[0].synthesis_text_snapshot == "Síntese congelada 0"
    assert (reloaded[0].start_char, reloaded[0].end_char) == (0, 9)
    assert reloaded[0].boundary == "sentence"
    assert reloaded[0].pause_after_frames == 2_400
    assert reloaded[0].chunking_identity == "c" * 64
    assert reloaded[0].chunking_strategy_version == "qwen3-tts-chunking-v2"
    assert reloaded[0].voice_profile_identity == "v" * 64
    assert reloaded[0].generation_profile_identity == "g" * 64
    assert reloaded[0].source_job_id == source_job_id

    takes = (
        await session.execute(
            select(AudioChunkTake).order_by(
                AudioChunkTake.audio_chunk_id, AudioChunkTake.take_number
            )
        )
    ).scalars().all()
    owned = [take for take in takes if take.audio_chunk_id in {c.id for c in reloaded}]
    assert len(owned) == 2
    assert all(take.kind == "initial" and take.status == "completed" for take in owned)
    assert all(chunk.current_take_id == chunk.takes[0].id for chunk in reloaded)


@pytest.mark.asyncio
async def test_candidate_creation_is_idempotent_and_does_not_change_current_take(
    session, block
) -> None:
    from app.models.workflow import AudioChunkTake
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    chunk = (
        await repo.persistir_chunks(block.id, [_chunk_data(0, block.id.hex)])
    )[0]
    original_current = chunk.current_take_id

    candidate = await repo.criar_chunk_take(
        chunk.id,
        seed=900,
        idempotency_key="workflow-chunk-regeneration-1",
    )
    replay = await repo.criar_chunk_take(
        chunk.id,
        seed=900,
        idempotency_key="workflow-chunk-regeneration-1",
    )
    await session.refresh(chunk)
    assert replay.id == candidate.id
    assert candidate.kind == "regeneration"
    assert candidate.status == "queued"
    assert chunk.current_take_id == original_current
    count = len(
        (
            await session.execute(
                select(AudioChunkTake).where(AudioChunkTake.audio_chunk_id == chunk.id)
            )
        ).scalars().all()
    )
    assert count == 2

    with pytest.raises(ValueError, match="different chunk inputs"):
        await repo.criar_chunk_take(
            chunk.id,
            seed=901,
            idempotency_key="workflow-chunk-regeneration-1",
        )


@pytest.mark.asyncio
async def test_one_candidate_leaves_every_original_active_hash_unchanged(
    session, block
) -> None:
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    chunks = await repo.persistir_chunks(
        block.id, [_chunk_data(i, block.id.hex) for i in range(4)]
    )
    chunks = list(await repo.listar_chunks(block.id))
    originals = {
        chunk.chunk_index: (chunk.current_take_id, chunk.takes[0].audio_sha256)
        for chunk in chunks
    }
    target = chunks[2]
    candidate = await repo.criar_chunk_take(
        target.id,
        seed=902,
        idempotency_key="workflow-chunk-regeneration-3",
    )
    await repo.atualizar_chunk_take(
        candidate.id,
        status="completed",
        audio_path="/tmp/candidate.wav",
        audio_sha256="f" * 64,
        provider_candidate_id="b" * 32,
    )

    block_id = block.id
    session.expire_all()
    reloaded = await repo.listar_chunks(block_id)
    assert {
        chunk.chunk_index: (chunk.current_take_id, chunk.takes[0].audio_sha256)
        for chunk in reloaded
    } == originals
    assert reloaded[2].takes[1].audio_sha256 == "f" * 64


@pytest.mark.asyncio
async def test_reject_completed_candidate_retains_history_and_current_take(
    session, block
) -> None:
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    chunk = (
        await repo.persistir_chunks(block.id, [_chunk_data(0, block.id.hex)])
    )[0]
    original_current = chunk.current_take_id
    candidate = await repo.criar_chunk_take(
        chunk.id,
        seed=903,
        idempotency_key="workflow-chunk-rejection",
    )
    await repo.atualizar_chunk_take(candidate.id, status="completed")

    assert await repo.descartar_chunk_take(candidate.id) is True
    loaded = await repo.obter_chunk(chunk.id)
    assert loaded is not None
    rejected = next(take for take in loaded.takes if take.id == candidate.id)
    assert rejected.status == "rejected"
    assert loaded.current_take_id == original_current
    assert len(loaded.takes) == 2


@pytest.mark.asyncio
async def test_current_take_id_has_database_referential_integrity(session, block) -> None:
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    chunk = (
        await repo.persistir_chunks(block.id, [_chunk_data(0, block.id.hex)])
    )[0]
    chunk.current_take_id = uuid.uuid4()
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_promotion_marks_durable_pending_edit_without_invalidating_final(
    session, block
) -> None:
    from app.models.workflow import WorkflowExecucao
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    workflow = await session.get(WorkflowExecucao, block.workflow_execucao_id)
    assert workflow is not None
    workflow.audio_final_path = "/tmp/old-final.wav"
    workflow.audio_final_sha256 = "e" * 64
    await session.commit()
    chunk = (
        await repo.persistir_chunks(block.id, [_chunk_data(3, block.id.hex)])
    )[0]
    original_take_id = chunk.current_take_id
    candidate = await repo.criar_chunk_take(
        chunk.id,
        seed=904,
        idempotency_key=f"promotion-{block.id}",
    )
    await repo.atualizar_chunk_take(candidate.id, status="completed")

    assert await repo.promover_chunk_take(
        candidate.id, workflow_id=workflow.id
    ) is True
    chunk_id = chunk.id
    workflow_id = workflow.id
    block_id = block.id
    session.expire_all()
    reloaded = await repo.obter_chunk(chunk_id)
    durable_workflow = await session.get(WorkflowExecucao, workflow_id)
    durable_block = await repo.obter_bloco_por_id(block_id)
    assert reloaded is not None and durable_workflow is not None and durable_block is not None
    assert reloaded.current_take_id == candidate.id
    assert reloaded.status == "active"
    assert next(t for t in reloaded.takes if t.id == original_take_id).status == "superseded"
    assert durable_workflow.audio_final_sha256 == "e" * 64
    assert durable_workflow.audio_final_path == "/tmp/old-final.wav"
    assert durable_workflow.audio_edit_revision == 1
    assert durable_workflow.audio_materialized_revision == 0
    assert durable_block.audio_edit_revision == 1
    assert durable_block.audio_materialized_revision == 0


@pytest.mark.asyncio
async def test_update_request_is_durable_idempotent_and_single_flight(session, block) -> None:
    from app.models.workflow import WorkflowExecucao
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    workflow = await session.get(WorkflowExecucao, block.workflow_execucao_id)
    assert workflow is not None
    workflow.audio_edit_revision = 2
    block.audio_edit_revision = 2
    await session.commit()

    first, created = await repo.request_audio_edit_update(
        workflow.id, f"apply-{workflow.id}"
    )
    replay, replay_created = await repo.request_audio_edit_update(
        workflow.id, f"apply-{workflow.id}"
    )
    concurrent, concurrent_created = await repo.request_audio_edit_update(
        workflow.id, f"apply-concurrent-{workflow.id}"
    )
    assert first is not None and replay is not None and concurrent is not None
    assert created is True
    assert replay_created is concurrent_created is False
    assert replay.id == concurrent.id == first.id

    update_id = first.id
    session.expire_all()
    durable = await repo.get_audio_edit_update(update_id)
    assert durable is not None
    assert durable.status == "pending"
    assert durable.target_revision == 2


@pytest.mark.asyncio
async def test_repeated_promotions_keep_one_consistent_selected_take(session, block) -> None:
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    chunk = (
        await repo.persistir_chunks(block.id, [_chunk_data(3, block.id.hex)])
    )[0]
    candidates = []
    for index in range(2):
        take = await repo.criar_chunk_take(
            chunk.id,
            seed=920 + index,
            idempotency_key=f"promotion-race-{block.id}-{index}",
        )
        await repo.atualizar_chunk_take(take.id, status="completed")
        candidates.append(take)

    assert await repo.promover_chunk_take(
        candidates[0].id, workflow_id=block.workflow_execucao_id
    )
    assert await repo.promover_chunk_take(
        candidates[1].id, workflow_id=block.workflow_execucao_id
    )
    selected = await repo.obter_chunk(chunk.id)
    assert selected is not None
    assert selected.current_take_id == candidates[1].id
    assert next(t for t in selected.takes if t.id == candidates[0].id).status == "superseded"
