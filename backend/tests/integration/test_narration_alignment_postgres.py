from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.workflow import (
    AudioBloco,
    NarrationAlignment,
    NarrationBlockAlignment,
    TranscricaoAudio,
    VisualQueryNarrationBinding,
    WorkflowExecucao,
)
from app.repositories.narration_alignment_repository import NarrationAlignmentRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 for PostgreSQL constraint/cascade validation",
)


@pytest.mark.asyncio
async def test_alignment_uniqueness_persistence_and_workflow_cascade() -> None:
    workflow_id = uuid.uuid4()
    block_id = uuid.uuid4()
    transcription_id = uuid.uuid4()
    fingerprint = uuid.uuid4().hex * 2
    async with AsyncSessionLocal() as session:
        workflow = WorkflowExecucao(
            id=workflow_id,
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={"blocos": []},
            status="audio_concluido",
            audio_final_sha256="a" * 64,
            duracao_audio_segundos=2.0,
        )
        block = AudioBloco(
            id=block_id,
            workflow_execucao_id=workflow_id,
            ordem=0,
            tipo="intro",
            texto="texto",
            status="concluido",
        )
        transcription = TranscricaoAudio(
            id=transcription_id,
            workflow_execucao_id=workflow_id,
            texto_completo="texto",
            palavras_timestamps=[],
            audio_sha256="a" * 64,
        )
        session.add_all([workflow, block, transcription])
        await session.commit()

        repository = NarrationAlignmentRepository(session)
        alignment, created = await repository.create_or_get(
            workflow_id=workflow_id,
            transcription_id=transcription_id,
            final_audio_sha256="a" * 64,
            script_fingerprint="b" * 64,
            transcription_fingerprint="c" * 64,
            strategy_version="narration_alignment_v1",
            fingerprint=fingerprint,
        )
        repeated, repeated_created = await repository.create_or_get(
            workflow_id=workflow_id,
            transcription_id=transcription_id,
            final_audio_sha256="a" * 64,
            script_fingerprint="b" * 64,
            transcription_fingerprint="c" * 64,
            strategy_version="narration_alignment_v1",
            fingerprint=fingerprint,
        )
        assert created is True
        assert repeated_created is False
        assert repeated.id == alignment.id

        await repository.replace_outputs(
            alignment,
            blocks=[
                NarrationBlockAlignment(
                    narration_alignment_id=alignment.id,
                    audio_bloco_id=block_id,
                    block_order=0,
                    status="no_words",
                    narration_start_seconds=0.0,
                    narration_end_seconds=2.0,
                    canonical_token_count=1,
                    synthesis_token_count=1,
                    asr_word_count=0,
                    canonical_synthesis_coverage=1.0,
                    synthesis_asr_coverage=0.0,
                    overall_coverage=0.0,
                )
            ],
            bindings=[
                VisualQueryNarrationBinding(
                    narration_alignment_id=alignment.id,
                    audio_bloco_id=block_id,
                    visual_query_id=uuid.uuid4(),
                    block_order=0,
                    query_order=0,
                    status="not_applicable",
                    excerpt_match_score=0.0,
                    speech_alignment_coverage=0.0,
                    binding_confidence=0.0,
                )
            ],
            metrics={"average_block_coverage": 0.0},
        )
        loaded = await repository.get_by_id(alignment.id, eager=True)
        assert loaded is not None
        assert len(loaded.block_alignments) == 1
        assert len(loaded.visual_query_bindings) == 1

        await session.delete(workflow)
        await session.commit()
        assert (
            await session.execute(
                select(NarrationAlignment).where(NarrationAlignment.id == alignment.id)
            )
        ).scalar_one_or_none() is None
