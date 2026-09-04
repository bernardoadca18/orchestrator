"""PostgreSQL integration test for audio retry after workflow completion.

This test uses a real PostgreSQL database to verify:
1. Retry after completion does NOT write NULL to NOT NULL transcription fields
2. No IntegrityError when invalidating audio derivatives
3. No MissingGreenlet from async lazy loading issues
4. SHA-based re-transcription trigger works correctly
5. Full retranscription flow with faked ASR
6. NarrationAlignment precondition detection works correctly

Run with:
    POSTGRES_INTEGRATION=1 pytest tests/integration/test_audio_retry_postgres.py -v
"""

from __future__ import annotations

import os
import uuid
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
        # Cleanup: rollback any uncommitted changes
        await session.rollback()


@pytest.fixture
async def workflow_with_transcription(session):
    """Create a completed workflow with transcription for retry test."""
    from app.models.workflow import (
        AudioBloco,
        AudioBlocoTake,
        TranscricaoAudio,
        WorkflowExecucao,
    )

    workflow_id = uuid.uuid4()
    bloco_id = uuid.uuid4()
    take_id = uuid.uuid4()
    transcription_id = uuid.uuid4()

    workflow = WorkflowExecucao(
        id=workflow_id,
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={"sections": []},
        status="audio_concluido",
        audio_final_path="/audio/final.wav",
        audio_final_sha256="a" * 64,
        audio_final_frame_count=24000,
        audio_final_subtype="PCM_16",
        audio_integrity={"sha256": "a" * 64},
        duracao_audio_segundos=1.0,
        production_status="completed",
        production_phase="completed",
    )

    bloco = AudioBloco(
        id=bloco_id,
        workflow_execucao_id=workflow_id,
        ordem=0,
        tipo="intro",
        texto="Teste de áudio para retry.",
        normalized_text="teste de audio para retry",
        synthesis_text="teste de audio para retry",
        status="concluido",
        audio_sha256="a" * 64,
        audio_path="/audio/bloco_0.wav",
        narration_start_seconds=0.0,
        narration_end_seconds=1.0,
    )

    take = AudioBlocoTake(
        id=take_id,
        audio_bloco_id=bloco_id,
        attempt_index=0,
        kind="initial",
        status="completed",
        is_current=True,
        audio_path="/audio/take_0.wav",
        audio_sha256="a" * 64,
    )

    transcription = TranscricaoAudio(
        id=transcription_id,
        workflow_execucao_id=workflow_id,
        texto_completo="teste de audio para retry",
        palavras_timestamps=[
            {"word": "teste", "start": 0.0, "end": 0.3},
            {"word": "de", "start": 0.3, "end": 0.4},
            {"word": "audio", "start": 0.4, "end": 0.7},
            {"word": "para", "start": 0.7, "end": 0.9},
            {"word": "retry", "start": 0.9, "end": 1.2},
        ],
        audio_sha256="a" * 64,
        provider="whisper",
        model="tiny",
        language="pt",
    )

    session.add(workflow)
    session.add(bloco)
    session.add(take)
    session.add(transcription)
    await session.commit()

    yield {
        "workflow_id": workflow_id,
        "bloco_id": bloco_id,
        "take_id": take_id,
        "transcription_id": transcription_id,
        "workflow": workflow,
        "bloco": bloco,
        "take": take,
        "transcription": transcription,
    }

    # Cleanup
    await session.execute(
        AudioBlocoTake.__table__.delete().where(AudioBlocoTake.id == take_id)
    )
    await session.execute(
        AudioBloco.__table__.delete().where(AudioBloco.id == bloco_id)
    )
    await session.execute(
        TranscricaoAudio.__table__.delete().where(TranscricaoAudio.id == transcription_id)
    )
    await session.execute(
        WorkflowExecucao.__table__.delete().where(WorkflowExecucao.id == workflow_id)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_retry_preserves_transcription_not_null_fields(
    session, workflow_with_transcription
):
    """Verify that retry does NOT write NULL to NOT NULL transcription fields.

    This test validates:
    1. invalidar_derivados_de_audio clears audio_final_sha256 (nullable)
    2. Transcription texto_completo is preserved (NOT NULL)
    3. Transcription palavras_timestamps is preserved (NOT NULL)
    4. No IntegrityError from PostgreSQL
    """
    from app.repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    workflow = workflow_with_transcription["workflow"]
    transcription = workflow_with_transcription["transcription"]

    original_texto = transcription.texto_completo
    original_palavras = transcription.palavras_timestamps

    # Call invalidation (simulates retry preparation)
    await repo.invalidar_derivados_de_audio(
        workflow, [workflow_with_transcription["bloco"]]
    )
    await session.commit()
    await session.refresh(workflow)
    await session.refresh(transcription)

    # Verify audio_final_sha256 is cleared (this is nullable)
    assert (
        workflow.audio_final_sha256 is None
    ), "audio_final_sha256 should be None after invalidation"

    # Verify transcription fields are PRESERVED (NOT NULL constraint)
    assert (
        transcription.texto_completo == original_texto
    ), "texto_completo must be preserved"
    assert (
        transcription.palavras_timestamps == original_palavras
    ), "palavras_timestamps must be preserved"

    # Verify no NULL written to NOT NULL fields
    assert transcription.texto_completo is not None, "texto_completo must NOT be NULL"
    assert (
        transcription.palavras_timestamps is not None
    ), "palavras_timestamps must NOT be NULL"


@pytest.mark.asyncio
async def test_retranscription_flow_with_faked_asr(session, workflow_with_transcription):
    """Verify the full retranscription flow with faked ASR provider.

    This test proves:
    1. Old audio SHA A → transcription SHA A
    2. New audio SHA B detected by _ensure_transcription
    3. Transcriber invoked (faked)
    4. Transcription updated to SHA B
    5. texto_completo and palavras_timestamps are valid (NOT NULL)
    """
    from unittest.mock import MagicMock

    from app.services.audio_service import AudioWorkflowService
    from app.services.transcription_service import (
        NarrationTranscription,
        NarrationWord,
    )

    workflow = workflow_with_transcription["workflow"]
    transcription = workflow_with_transcription["transcription"]
    new_sha = "b" * 64  # Different SHA (new audio)

    # Simulate new audio being generated
    workflow.audio_final_sha256 = new_sha
    workflow.audio_final_path = "/audio/final_new.wav"
    await session.commit()

    # Verify old transcription SHA != new audio SHA
    assert transcription.audio_sha256 != new_sha, "SHA mismatch should exist"

    # Create fake transcription result
    fake_result = NarrationTranscription(
        text="novo audio transcrito",
        words=(
            NarrationWord(word="novo", start_seconds=0.0, end_seconds=0.2),
            NarrationWord(word="audio", start_seconds=0.2, end_seconds=0.5),
            NarrationWord(word="transcrito", start_seconds=0.5, end_seconds=1.0),
        ),
        provider="fake",
        model="test",
        language="pt",
    )

    # Mock the transcription provider
    fake_provider = MagicMock()
    fake_provider.transcribe.return_value = fake_result

    # Create service with faked provider
    service = AudioWorkflowService(
        session,
        transcription_provider=fake_provider,
    )

    # Call _ensure_transcription with new SHA
    await service._ensure_transcription(
        workflow.id, Path("/audio/final_new.wav"), new_sha
    )
    await session.commit()
    await session.refresh(transcription)

    # Verify transcriber was called
    fake_provider.transcribe.assert_called_once()

    # Verify transcription updated to new SHA
    assert transcription.audio_sha256 == new_sha, "Transcription SHA should be updated"

    # Verify NOT NULL fields are valid
    assert transcription.texto_completo is not None, "texto_completo must NOT be NULL"
    assert transcription.texto_completo == "novo audio transcrito"
    assert (
        transcription.palavras_timestamps is not None
    ), "palavras_timestamps must NOT be NULL"
    assert len(transcription.palavras_timestamps) == 3


@pytest.mark.asyncio
async def test_narration_alignment_detects_stale_transcription(
    session, workflow_with_transcription
):
    """Verify narration alignment detects transcription SHA mismatch.

    Validates:
    1. NarrationAlignmentService._precondition_error detects SHA mismatch
    2. Error message is clear about transcription belonging to different audio
    """
    from app.services.narration_alignment_service import NarrationAlignmentService

    workflow = workflow_with_transcription["workflow"]
    transcription = workflow_with_transcription["transcription"]

    # Simulate new audio SHA
    new_sha = "b" * 64
    workflow.audio_final_sha256 = new_sha

    service = NarrationAlignmentService.__new__(NarrationAlignmentService)
    error = service._precondition_error(workflow, transcription)

    assert error == "transcription belongs to a different final narration"
    assert transcription.audio_sha256 != workflow.audio_final_sha256


@pytest.fixture
async def workflow_with_alignment(session):
    """Create a completed workflow with transcription and alignment for recovery test."""
    from app.models.workflow import (
        AudioBloco,
        AudioBlocoTake,
        NarrationAlignment,
        TranscricaoAudio,
        WorkflowExecucao,
    )

    workflow_id = uuid.uuid4()
    bloco_id = uuid.uuid4()
    take_id = uuid.uuid4()
    transcription_id = uuid.uuid4()
    alignment_a_id = uuid.uuid4()

    sha_a = "a" * 64

    workflow = WorkflowExecucao(
        id=workflow_id,
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={"blocos": [{"ordem": 0, "conteudo": "teste de audio"}]},
        status="audio_concluido",
        audio_final_path="/audio/final_a.wav",
        audio_final_sha256=sha_a,
        audio_final_frame_count=24000,
        audio_final_subtype="PCM_16",
        audio_integrity={"sha256": sha_a},
        duracao_audio_segundos=1.0,
        production_status="completed",
        production_phase="completed",
    )

    bloco = AudioBloco(
        id=bloco_id,
        workflow_execucao_id=workflow_id,
        ordem=0,
        tipo="intro",
        texto="teste de audio",
        normalized_text="teste de audio",
        synthesis_text="teste de audio",
        source_text_sha256="src" + "0" * 61,
        synthesis_text_sha256="syn" + "0" * 61,
        synthesis_identity="ident" + "0" * 58,
        status="concluido",
        audio_sha256=sha_a,
        audio_path="/audio/bloco_0.wav",
        narration_start_seconds=0.0,
        narration_end_seconds=1.0,
    )

    take = AudioBlocoTake(
        id=take_id,
        audio_bloco_id=bloco_id,
        attempt_index=0,
        kind="initial",
        status="completed",
        is_current=True,
        audio_path="/audio/take_0.wav",
        audio_sha256=sha_a,
    )

    transcription = TranscricaoAudio(
        id=transcription_id,
        workflow_execucao_id=workflow_id,
        texto_completo="teste de audio",
        palavras_timestamps=[
            {"palavra": "teste", "inicio": 0.0, "fim": 0.4},
            {"palavra": "de", "inicio": 0.4, "fim": 0.5},
            {"palavra": "audio", "inicio": 0.5, "fim": 1.0},
        ],
        audio_sha256=sha_a,
        provider="whisper",
        model="tiny",
        language="pt",
    )

    alignment_a = NarrationAlignment(
        id=alignment_a_id,
        workflow_execucao_id=workflow_id,
        transcription_id=transcription_id,
        final_audio_sha256=sha_a,
        script_fingerprint="script" + "a" * 58,
        transcription_fingerprint="trans" + "a" * 59,
        strategy_version="narration_alignment_v1",
        fingerprint="finger" + "a" * 58,
        status="completed",
        block_count=1,
        resolved_visual_queries=0,
        unresolved_visual_queries=0,
        ambiguous_visual_queries=0,
        not_applicable_visual_queries=0,
    )

    session.add(workflow)
    await session.flush()

    session.add(bloco)
    await session.flush()

    session.add(take)
    session.add(transcription)
    await session.flush()

    session.add(alignment_a)
    await session.commit()

    yield {
        "workflow_id": workflow_id,
        "bloco_id": bloco_id,
        "take_id": take_id,
        "transcription_id": transcription_id,
        "alignment_a_id": alignment_a_id,
        "workflow": workflow,
        "bloco": bloco,
        "take": take,
        "transcription": transcription,
        "alignment_a": alignment_a,
        "sha_a": sha_a,
    }

    # Cleanup
    await session.execute(
        NarrationAlignment.__table__.delete().where(NarrationAlignment.id == alignment_a_id)
    )
    await session.execute(
        AudioBlocoTake.__table__.delete().where(AudioBlocoTake.id == take_id)
    )
    await session.execute(
        AudioBloco.__table__.delete().where(AudioBloco.id == bloco_id)
    )
    await session.execute(
        TranscricaoAudio.__table__.delete().where(TranscricaoAudio.id == transcription_id)
    )
    await session.execute(
        WorkflowExecucao.__table__.delete().where(WorkflowExecucao.id == workflow_id)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_narration_alignment_recovery_after_retranscription(
    session, workflow_with_alignment
):
    """Verify full NarrationAlignment recovery after retranscription.

    Flow:
    1. Audio A → Transcription A → Alignment A (completed)
    2. Retry/New audio B → _ensure_transcription() → Transcription B
    3. NarrationAlignmentService.ensure() → Alignment B (completed)
    4. Alignment A remains as historical record
    5. Alignment B has fingerprint compatible with new inputs
    """
    from unittest.mock import MagicMock

    from app.services.audio_service import AudioWorkflowService
    from app.services.narration_alignment_service import NarrationAlignmentService
    from app.services.transcription_service import (
        NarrationTranscription,
        NarrationWord,
    )

    workflow = workflow_with_alignment["workflow"]
    bloco = workflow_with_alignment["bloco"]
    transcription = workflow_with_alignment["transcription"]
    alignment_a = workflow_with_alignment["alignment_a"]
    sha_a = workflow_with_alignment["sha_a"]
    sha_b = "b" * 64  # New audio SHA

    # Step 1: Simulate new audio B being generated
    workflow.audio_final_sha256 = sha_b
    workflow.audio_final_path = "/audio/final_b.wav"
    bloco.audio_sha256 = sha_b
    await session.commit()

    # Step 2: Re-transcription with faked ASR
    fake_result = NarrationTranscription(
        text="novo audio transcrito",
        words=(
            NarrationWord(word="novo", start_seconds=0.0, end_seconds=0.3),
            NarrationWord(word="audio", start_seconds=0.3, end_seconds=0.6),
            NarrationWord(word="transcrito", start_seconds=0.6, end_seconds=1.0),
        ),
        provider="fake",
        model="test",
        language="pt",
    )

    fake_provider = MagicMock()
    fake_provider.transcribe.return_value = fake_result

    audio_service = AudioWorkflowService(session, transcription_provider=fake_provider)
    await audio_service._ensure_transcription(workflow.id, Path("/audio/final_b.wav"), sha_b)
    await session.commit()
    await session.refresh(transcription)

    # Verify transcription updated to SHA B
    assert transcription.audio_sha256 == sha_b, "Transcription SHA should be updated to B"
    assert transcription.texto_completo == "novo audio transcrito"

    # Step 3: NarrationAlignment recovery
    alignment_service = NarrationAlignmentService(session)

    alignment_b, created = await alignment_service.ensure(workflow.id)
    await session.commit()

    # Debug: Check why it might be blocked
    if alignment_b.status == "blocked":
        # Re-load to get error message
        await session.refresh(alignment_b)
        print(f"DEBUG: alignment_b blocked with error: {alignment_b.error_message}")

    # Step 4: Verify Alignment B completed
    assert alignment_b.status == "completed", "Alignment B should be completed"
    assert alignment_b.final_audio_sha256 == sha_b, "Alignment B audio SHA should be B"
    assert (
        alignment_b.transcription_id == transcription.id
    ), "Alignment B should link to current transcription"

    # Step 5: Verify Alignment A remains as historical record
    await session.refresh(alignment_a)
    assert alignment_a.status == "completed", "Alignment A should remain completed"
    assert alignment_a.final_audio_sha256 == sha_a, "Alignment A should still reference SHA A"

    # Step 6: Verify Alignment B fingerprint is different (new inputs)
    assert (
        alignment_b.fingerprint != alignment_a.fingerprint
    ), "Alignment B should have different fingerprint"
    assert (
        alignment_b.script_fingerprint != alignment_a.script_fingerprint
    ), "Script fingerprint should differ"

    # Step 7: Verify both alignments exist for the same workflow
    from app.repositories.narration_alignment_repository import NarrationAlignmentRepository

    alignment_repo = NarrationAlignmentRepository(session)
    all_alignments = await alignment_repo.list_for_workflow(workflow.id)
    assert len(all_alignments) == 2, "Should have 2 alignments (A and B)"
