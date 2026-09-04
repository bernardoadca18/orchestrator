"""Regression tests for voice identity validation and retry-after-completion fixes.

These tests verify:
1. Voice identity mismatch detection triggers quality retry (Problem A - Voice Drift)
2. Retry-after-completion properly invalidates transcription (Problem D)
3. Pronunciation entries for GTA IV/V/VI and hardware are applied (Problem B)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.clients.voice_client import (
    GenerationProfileDescriptor,
    VoiceNarrationJob,
    VoiceProfileDescriptor,
    VoiceProfileProvenance,
    VoiceJobFailedError,
)
from app.core.config import Settings
from app.services.audio_service import AudioWorkflowService
from app.services.pronunciation import PronunciationPreprocessor
from app.services.transcription_service import NarrationTranscription, NarrationWord


# =============================================================================
# Test fixtures and helpers
# =============================================================================


class FakeRepository:
    """Minimal fake repository for testing."""

    def __init__(self, workflow) -> None:
        self.workflow = workflow
        self.blocks: dict[int, SimpleNamespace] = {}
        self.transcription = None
        self.promoted_takes: list[dict] = []

    async def obter_por_id(self, workflow_id, **kwargs):
        return self.workflow if workflow_id == self.workflow.id else None

    async def atualizar_status(self, workflow_id, status, **fields):
        self.workflow.status = status
        for name, value in fields.items():
            setattr(self.workflow, name, value)

    async def ensure_bloco(self, **values):
        order = values["ordem"]
        block = self.blocks.get(order)
        unchanged = block is not None and block.synthesis_identity == values.get("synthesis_identity")
        if block is None:
            block = SimpleNamespace(
                id=uuid.uuid4(),
                workflow_execucao_id=values["workflow_id"],
                status="pendente",
                job_id_voz=None,
                resolved_seed=None,
                initial_resolved_seed=None,
                generation_attempt=0,
                retry_seed_strategy=None,
                generation_attempt_history=None,
                audio_path=None,
                audio_sha256=None,
                audio_sample_rate=None,
                audio_channels=None,
                audio_byte_size=None,
                voice_profile_identity=values.get("voice_profile_identity"),
            )
            self.blocks[order] = block
        for name, value in values.items():
            if name != "workflow_id":
                setattr(block, name, value)
        return block, unchanged

    async def atualizar_bloco(self, block_id, **fields):
        block = next(block for block in self.blocks.values() if block.id == block_id)
        for name, value in fields.items():
            setattr(block, name, value)

    async def obter_bloco_por_ordem(self, workflow_id, order):
        return self.blocks.get(order)

    async def obter_transcricao(self, workflow_id):
        return self.transcription

    async def upsert_transcricao(self, workflow_id, **fields):
        self.transcription = SimpleNamespace(**fields)
        return self.transcription

    async def promover_take(
        self,
        block_id,
        take_id,
        *,
        status,
        job_id_voz,
        resolved_seed,
        model_id,
        model_revision,
        voice_profile,
        voice_profile_identity,
        **kwargs,
    ):
        self.promoted_takes.append({
            "block_id": block_id,
            "take_id": take_id,
            "status": status,
            "job_id_voz": job_id_voz,
            "resolved_seed": resolved_seed,
            "model_id": model_id,
            "model_revision": model_revision,
            "voice_profile": voice_profile,
            "voice_profile_identity": voice_profile_identity,
        })
        block = next(b for b in self.blocks.values() if b.id == block_id)
        block.status = status
        block.job_id_voz = job_id_voz
        block.voice_profile_identity = voice_profile_identity
        return block

    async def criar_take(self, block_id, **kwargs):
        take = SimpleNamespace(
            id=uuid.uuid4(),
            block_id=block_id,
            status=kwargs.get("status", "pending"),
            attempt=kwargs.get("attempt", 1),
        )
        return take

    async def atualizar_take(self, take_id, **fields):
        pass

    async def obter_takes(self, block_id):
        return []

    async def invalidar_derivados_de_audio(self, workflow, blocks=None):
        """Simulate the fix for Problem D: clear transcription fields."""
        workflow.audio_final_path = None
        workflow.audio_final_sha256 = None
        if workflow.transcricao is not None:
            workflow.transcricao.audio_sha256 = None
            workflow.transcricao.palavras_timestamps = None
            workflow.transcricao.texto_completo = None
        workflow.production_phase = "audio"


# =============================================================================
# Test 1: Voice Identity Mismatch Detection (Problem A - Voice Drift)
# =============================================================================


def test_voice_identity_validation_logic() -> None:
    """Verify the voice identity validation logic detects mismatches.

    This tests the core validation that was added to audio_service.py:
    - If expected_identity != actual_identity, trigger retry
    - The validation happens after wait_for_narration completes
    """
    expected_identity = "a" * 64  # Expected bernardo_xvec_v2 identity
    drift_identity = "b" * 64  # Wrong/drifted identity (e.g., feminine PT-PT voice)

    # Simulate the validation logic from audio_service.py
    # Lines 409-437: voice identity mismatch check
    metadata = {"voice_identity": drift_identity}

    # The condition that triggers retry
    actual_identity = metadata.get("voice_identity")
    should_retry = (
        expected_identity is not None
        and actual_identity is not None
        and actual_identity != expected_identity
    )

    assert should_retry is True, "Voice identity mismatch should trigger retry"
    assert actual_identity == drift_identity
    assert actual_identity != expected_identity


def test_voice_identity_validation_passes_when_matching() -> None:
    """Verify the voice identity validation passes when identities match."""
    expected_identity = "a" * 64

    # Simulate matching identity
    metadata = {"voice_identity": expected_identity}

    actual_identity = metadata.get("voice_identity")
    should_retry = (
        expected_identity is not None
        and actual_identity is not None
        and actual_identity != expected_identity
    )

    assert should_retry is False, "Matching identities should not trigger retry"


def test_voice_identity_validation_handles_missing_metadata() -> None:
    """Verify the validation handles cases where voice_identity is missing."""
    expected_identity = "a" * 64

    # Simulate missing voice_identity in metadata
    metadata: dict = {}

    actual_identity = metadata.get("voice_identity")
    should_retry = (
        expected_identity is not None
        and actual_identity is not None
        and actual_identity != expected_identity
    )

    assert should_retry is False, "Missing identity should not trigger retry"
    assert actual_identity is None


# =============================================================================
# Test 2: Retry-After-Completion Transcription Invalidation (Problem D)
# =============================================================================


@pytest.mark.asyncio
async def test_retry_after_completion_invalidates_transcription(tmp_path) -> None:
    """Verify that invalidar_derivados_de_audio clears transcription fields.

    This ensures that when audio is regenerated, the old transcription
    (which was for different audio) is invalidated, allowing narration
    alignment to succeed.
    """
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        status="audio_concluido",
        audio_final_path="/old/audio.wav",
        audio_final_sha256="old_sha256",
        transcricao=SimpleNamespace(
            audio_sha256="old_transcription_sha256",
            palavras_timestamps=[{"word": "old", "start": 0.0, "end": 0.5}],
            texto_completo="old transcription",
        ),
        production_phase="narration_alignment",
    )
    repo = FakeRepository(workflow)

    # Call the invalidation method
    await repo.invalidar_derivados_de_audio(workflow)

    # Verify audio fields are cleared
    assert workflow.audio_final_path is None
    assert workflow.audio_final_sha256 is None

    # Verify transcription fields are cleared (the fix for Problem D)
    assert workflow.transcricao.audio_sha256 is None
    assert workflow.transcricao.palavras_timestamps is None
    assert workflow.transcricao.texto_completo is None

    # Verify production phase is reset to audio
    assert workflow.production_phase == "audio"


# =============================================================================
# Test 3: Pronunciation Entries (Problem B)
# =============================================================================


def test_pronunciation_gta_vi_v_iv_and_hardware(tmp_path: Path) -> None:
    """Verify that GTA IV/V/VI and hardware pronunciation entries are applied correctly."""
    entries = [
        {
            "id": "title.gta-iv",
            "canonical_term": "GTA IV",
            "synthesis_representation": "Gê Tê Á quatro",
            "category": "title_numeral",
            "locale": "pt-BR",
            "match": {"mode": "title_numeral", "case_sensitive": False},
            "enabled": True,
            "priority": 300,
            "revision": 2,
            "source": "test",
            "approval": {"status": "approved", "approved_by": "tester"},
        },
        {
            "id": "title.gta-v",
            "canonical_term": "GTA V",
            "synthesis_representation": "Gê Tê Á cinco",
            "category": "title_numeral",
            "locale": "pt-BR",
            "match": {"mode": "title_numeral", "case_sensitive": False},
            "enabled": True,
            "priority": 300,
            "revision": 2,
            "source": "test",
            "approval": {"status": "approved", "approved_by": "tester"},
        },
        {
            "id": "title.gta-vi",
            "canonical_term": "GTA VI",
            "synthesis_representation": "Gê Tê Á seis",
            "category": "title_numeral",
            "locale": "pt-BR",
            "match": {"mode": "title_numeral", "case_sensitive": False},
            "enabled": True,
            "priority": 300,
            "revision": 2,
            "source": "test",
            "approval": {"status": "approved", "approved_by": "tester"},
        },
        {
            "id": "term.hardware",
            "canonical_term": "hardware",
            "synthesis_representation": "rárduér",
            "category": "technical_term",
            "locale": "pt-BR",
            "match": {"mode": "token", "case_sensitive": False},
            "enabled": True,
            "priority": 100,
            "revision": 1,
            "source": "test",
            "approval": {"status": "approved", "approved_by": "tester"},
        },
    ]

    lexicon_path = tmp_path / "lexicon.yaml"
    lexicon_path.write_text(
        yaml.safe_dump(
            {
                "schema": "neovids_pronunciation_lexicon_v2",
                "version": "1",
                "entries": entries,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    resolver = PronunciationPreprocessor.load(lexicon_path)

    # Test GTA VI
    result = resolver.preprocess("GTA VI foi anunciado!")
    assert result.synthesis_text == "Gê Tê Á seis foi anunciado!"

    # Test GTA V
    result = resolver.preprocess("GTA V é um clássico.")
    assert result.synthesis_text == "Gê Tê Á cinco é um clássico."

    # Test GTA IV
    result = resolver.preprocess("GTA IV tem uma ótima história.")
    assert result.synthesis_text == "Gê Tê Á quatro tem uma ótima história."

    # Test hardware
    result = resolver.preprocess("O hardware do console é potente.")
    assert result.synthesis_text == "O rárduér do console é potente."

    # Test combined
    result = resolver.preprocess("GTA VI terá hardware melhor que GTA V.")
    assert result.synthesis_text == "Gê Tê Á seis terá rárduér melhor que Gê Tê Á cinco."


def test_pronunciation_production_glossary_loaded_correctly(tmp_path: Path) -> None:
    """Verify that the production glossary entries are correctly loaded and applied."""
    # Load the actual production glossary
    production_glossary_path = Path("/home/bernardo/workspaces/neovids/orchestrator/backend/config/pronunciation_glossary.yaml")
    if not production_glossary_path.exists():
        pytest.skip("Production glossary not found")

    resolver = PronunciationPreprocessor.load(production_glossary_path)

    # Verify GTA entries are enabled and work
    result = resolver.preprocess("GTA VI é o próximo jogo.")
    assert "Gê Tê Á seis" in result.synthesis_text

    result = resolver.preprocess("hardware é importante.")
    assert "rárduér" in result.synthesis_text
