"""Tests for audio chunk persistence, regeneration, and take management.

Run with:
    POSTGRES_INTEGRATION=1 pytest tests/test_audio_chunks.py -v
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_INTEGRATION"),
    reason="Integration test requires POSTGRES_INTEGRATION=1 and running PostgreSQL",
)


@pytest.fixture
async def engine():
    """Create async engine for integration test."""
    from app.core.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine):
    """Create async session for each test with cleanup."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def workflow(session):
    """Create a sample workflow for testing."""
    from app.models.workflow import WorkflowExecucao

    wf = WorkflowExecucao(
        id=uuid.uuid4(),
        idea_id="test-idea",
        status="audio_concluido",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(wf)
    await session.commit()
    return wf


@pytest.fixture
async def block(session, workflow):
    """Create a sample audio block for testing."""
    from app.models.workflow import AudioBloco

    bloco = AudioBloco(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        classification="narration",
        sequence_index=0,
        text="Primeiro bloco de teste.",
        status="concluido",
        voice_profile="bernardo_v1",
        generation_profile="narration_v1",
        seed=12345,
        audio_sha256="a" * 64,
        audio_duration_seconds=5.0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(bloco)
    await session.commit()
    return bloco


@pytest.mark.asyncio
async def test_persist_chunks_creates_chunks_and_initial_takes(
    session, block
) -> None:
    """Test that persistir_chunks creates chunks with their initial active takes."""
    from app.models.workflow import AudioChunk, AudioChunkTake
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)

    chunks_data = [
        {
            "chunk_index": 0,
            "chunk_id": "chunk-0-abc123",
            "text": "Primeiro",
            "synthesis_text": "Primeiro",
            "seed": 100,
            "duration_seconds": 1.0,
            "sample_rate": 24000,
            "audio_path": "/tmp/chunk_0.wav",
            "audio_sha256": "b" * 64,
            "audio_byte_size": 48000,
            "audio_frame_count": 24000,
        },
        {
            "chunk_index": 1,
            "chunk_id": "chunk-1-def456",
            "text": "bloco de teste.",
            "synthesis_text": "bloco de teste.",
            "seed": 200,
            "duration_seconds": 2.0,
            "sample_rate": 24000,
            "audio_path": "/tmp/chunk_1.wav",
            "audio_sha256": "c" * 64,
            "audio_byte_size": 96000,
            "audio_frame_count": 48000,
        },
    ]

    chunks = await repo.persistir_chunks(block.id, chunks_data)

    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[0].chunk_id == "chunk-0-abc123"
    assert chunks[0].seed == 100
    assert chunks[1].chunk_index == 1

    # Verify initial takes were created and are active
    for chunk in chunks:
        assert chunk.current_take_id is not None
        from sqlalchemy import select

        stmt = select(AudioChunkTake).where(AudioChunkTake.chunk_id == chunk.id)
        result = await session.execute(stmt)
        takes = result.scalars().all()
        assert len(takes) == 1
        assert takes[0].kind == "initial"
        assert takes[0].status == "active"
        assert takes[0].seed == chunk.seed


@pytest.mark.asyncio
async def test_list_chunks_returns_ordered_by_index(session, block) -> None:
    """Test that listar_chunks returns chunks ordered by chunk_index."""
    from app.models.workflow import AudioChunk
    from app.repositories.workflow_repository import WorkflowRepository

    # Create chunks out of order
    for i in [2, 0, 1]:
        chunk = AudioChunk(
            id=uuid.uuid4(),
            bloco_id=block.id,
            chunk_index=i,
            chunk_id=f"chunk-{i}",
            seed=i * 100,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(chunk)
    await session.commit()

    repo = WorkflowRepository(session)
    chunks = await repo.listar_chunks(block.id)

    assert len(chunks) == 3
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


@pytest.mark.asyncio
async def test_create_regeneration_take_creates_candidate(session, block) -> None:
    """Test that criar_chunk_take creates a candidate take for regeneration."""
    from app.models.workflow import AudioChunk, AudioChunkTake
    from app.repositories.workflow_repository import WorkflowRepository

    # Create a chunk with an initial take
    chunk = AudioChunk(
        id=uuid.uuid4(),
        bloco_id=block.id,
        chunk_index=0,
        chunk_id="chunk-0",
        seed=100,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(chunk)
    await session.commit()

    initial_take = AudioChunkTake(
        id=uuid.uuid4(),
        chunk_id=chunk.id,
        kind="initial",
        status="active",
        seed=100,
        created_at=datetime.utcnow(),
    )
    session.add(initial_take)
    await session.commit()

    # Update chunk's current_take_id
    chunk.current_take_id = initial_take.id
    await session.commit()

    repo = WorkflowRepository(session)
    regen_take = await repo.criar_chunk_take(chunk.id, seed=200, kind="regeneration")

    assert regen_take.kind == "regeneration"
    assert regen_take.status == "candidate"
    assert regen_take.seed == 200

    # Verify both takes exist
    from sqlalchemy import select

    stmt = select(AudioChunkTake).where(AudioChunkTake.chunk_id == chunk.id)
    result = await session.execute(stmt)
    takes = result.scalars().all()
    assert len(takes) == 2
    statuses = {t.kind: t.status for t in takes}
    assert statuses["initial"] == "active"
    assert statuses["regeneration"] == "candidate"


@pytest.mark.asyncio
async def test_promote_take_updates_chunk_and_marks_old_take_superseded(
    session, block
) -> None:
    """Test that promover_chunk_take atomically updates chunk and marks old take."""
    from app.models.workflow import AudioChunk, AudioChunkTake
    from app.repositories.workflow_repository import WorkflowRepository

    # Create a chunk with initial take
    chunk = AudioChunk(
        id=uuid.uuid4(),
        bloco_id=block.id,
        chunk_index=0,
        chunk_id="chunk-0",
        seed=100,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(chunk)
    await session.commit()

    initial_take = AudioChunkTake(
        id=uuid.uuid4(),
        chunk_id=chunk.id,
        kind="initial",
        status="active",
        seed=100,
        created_at=datetime.utcnow(),
    )
    session.add(initial_take)
    await session.commit()

    chunk.current_take_id = initial_take.id
    await session.commit()

    # Create a candidate take
    candidate = AudioChunkTake(
        id=uuid.uuid4(),
        chunk_id=chunk.id,
        kind="regeneration",
        status="candidate",
        seed=200,
        created_at=datetime.utcnow(),
    )
    session.add(candidate)
    await session.commit()

    repo = WorkflowRepository(session)
    result = await repo.promover_chunk_take(candidate.id)

    assert result is True

    # Refresh and verify
    await session.refresh(initial_take)
    await session.refresh(candidate)
    await session.refresh(chunk)

    assert initial_take.status == "superseded"
    assert candidate.status == "active"
    assert chunk.current_take_id == candidate.id


@pytest.mark.asyncio
async def test_discard_take_marks_as_rejected(session, block) -> None:
    """Test that descartar_chunk_take marks a candidate as rejected."""
    from app.models.workflow import AudioChunk, AudioChunkTake
    from app.repositories.workflow_repository import WorkflowRepository

    chunk = AudioChunk(
        id=uuid.uuid4(),
        bloco_id=block.id,
        chunk_index=0,
        chunk_id="chunk-0",
        seed=100,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(chunk)
    await session.commit()

    candidate = AudioChunkTake(
        id=uuid.uuid4(),
        chunk_id=chunk.id,
        kind="regeneration",
        status="candidate",
        seed=200,
        created_at=datetime.utcnow(),
    )
    session.add(candidate)
    await session.commit()

    repo = WorkflowRepository(session)
    result = await repo.descartar_chunk_take(candidate.id)

    assert result is True
    await session.refresh(candidate)
    assert candidate.status == "rejected"


def test_reassemble_from_chunks_combines_audio(tmp_path: Path, write_tone) -> None:
    """Test that reassemble_block_from_chunks combines chunk audio files."""
    from app.services.production_audio import reassemble_block_from_chunks

    # Create two chunk audio files with different frequencies
    chunk1_path = write_tone(tmp_path / "chunk_0.wav", frequency=220.0, duration=1.0)
    chunk2_path = write_tone(tmp_path / "chunk_1.wav", frequency=440.0, duration=1.0)

    output_path = tmp_path / "reassembled.wav"

    result = reassemble_block_from_chunks(
        [chunk1_path, chunk2_path],
        output_path,
        sample_rate=24000,
    )

    assert output_path.exists()
    assert result.sample_rate == 24000
    assert result.duration_seconds == 2.0
    assert result.frame_count == 48000
