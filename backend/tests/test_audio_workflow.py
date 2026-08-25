from __future__ import annotations

import copy
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.clients.voice_client import (
    GenerationProfileDescriptor,
    VoiceNarrationJob,
    VoiceProfileDescriptor,
    VoiceProfileProvenance,
)
from app.core.config import Settings
from app.services.audio_service import AudioWorkflowService
from app.services.production_audio import inspect_wav
from app.services.pronunciation import PronunciationPreprocessor
from app.services.transcription_service import (
    NarrationTranscription,
    NarrationWord,
)


class FakeRepository:
    def __init__(self, workflow) -> None:
        self.workflow = workflow
        self.blocks: dict[int, SimpleNamespace] = {}
        self.transcription = None

    async def obter_por_id(self, workflow_id, **kwargs):
        return self.workflow if workflow_id == self.workflow.id else None

    async def atualizar_status(self, workflow_id, status, **fields):
        self.workflow.status = status
        for name, value in fields.items():
            setattr(self.workflow, name, value)

    async def ensure_bloco(self, **values):
        order = values["ordem"]
        block = self.blocks.get(order)
        unchanged = block is not None and block.synthesis_identity == values["synthesis_identity"]
        if block is None:
            block = SimpleNamespace(
                id=uuid.uuid4(),
                workflow_execucao_id=values["workflow_id"],
                status="pendente",
                job_id_voz=None,
                resolved_seed=None,
                generation_attempt=0,
                audio_path=None,
                audio_sha256=None,
                audio_sample_rate=None,
                audio_channels=None,
                audio_byte_size=None,
            )
            self.blocks[order] = block
        elif not unchanged:
            for field, value in {
                "status": "pendente",
                "job_id_voz": None,
                "resolved_seed": None,
                "generation_attempt": 0,
                "audio_path": None,
                "audio_sha256": None,
                "audio_sample_rate": None,
                "audio_channels": None,
                "audio_byte_size": None,
            }.items():
                setattr(block, field, value)
        for name, value in values.items():
            if name != "workflow_id" and (name != "idempotency_key" or not unchanged):
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


class FakeVoiceClient:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.created: list[dict] = []
        self.jobs: dict[str, VoiceNarrationJob] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def resolve_profiles(self, voice, generation):
        return VoiceProfileProvenance(
            VoiceProfileDescriptor(name=voice, version="1", identity="v" * 64),
            GenerationProfileDescriptor(name=generation, version="1", identity="g" * 64),
        )

    async def create_narration(self, **payload):
        self.created.append(payload)
        seed = payload.get("seed") or (1000 + len(self.created))
        job = _job(f"job-{len(self.created)}", seed)
        self.jobs[job.id] = job
        return job.model_copy(update={"status": "queued"})

    async def get_narration(self, job_id):
        return self.jobs[job_id]

    async def wait_for_narration(self, job_id, **kwargs):
        return self.jobs[job_id]

    async def download_audio(self, job_id):
        return self.audio


class FakeTranscriptionProvider:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        self.calls += 1
        return NarrationTranscription(
            text="fala teste",
            words=(NarrationWord("fala", 0.0, 0.4),),
            provider="fake",
            model="fake-v1",
            language="pt",
        )


class FailingTranscriptionProvider:
    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        raise RuntimeError("transcription unavailable")


class FakeAlignmentService:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def ensure(self, workflow_id: uuid.UUID):
        self.calls.append(workflow_id)


def _job(job_id: str, seed: int) -> VoiceNarrationJob:
    return VoiceNarrationJob(
        id=job_id,
        status="completed",
        voice_profile="bernardo_v1",
        generation_profile="narration_v1",
        resolved_seed=seed,
        model_id="Qwen/Qwen3-TTS",
        model_revision="revision",
    )


def _config(tmp_path: Path, glossary: Path) -> Settings:
    return Settings(
        _env_file=None,
        audio_storage_path=str(tmp_path / "audio"),
        pronunciation_glossary_path=glossary,
        voice_poll_interval_seconds=0,
    )


@pytest.mark.asyncio
async def test_end_to_end_reuses_valid_blocks_and_transcription_by_audio_hash(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", sample_rate=24_000, duration=1.0, amplitude=0.08)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {Aard: Árd}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={
            "blocos": [
                {"tipo": "intro", "conteudo": "Geralt usa Aard."},
                {"tipo": "body", "conteudo": "O combate começa."},
            ]
        },
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    transcription = FakeTranscriptionProvider()
    alignment = FakeAlignmentService()
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: voice,
        preprocessor=PronunciationPreprocessor.load(glossary),
        transcription_provider=transcription,
        alignment_service_factory=lambda session: alignment,  # type: ignore[arg-type]
    )
    service.repo = repo

    await service.process(workflow.id)
    assert workflow.status == "audio_concluido"
    assert workflow.audio_phase == "completed"
    assert len(voice.created) == 2
    assert repo.blocks[0].texto == "Geralt usa Aard."
    assert repo.blocks[0].synthesis_text == "Geralt usa Árd."
    assert repo.blocks[0].resolved_seed == 1001
    assert repo.blocks[1].resolved_seed == 1002
    assert repo.blocks[0].narration_start_seconds == 0
    assert repo.blocks[1].narration_start_seconds == pytest.approx(1.5, abs=1 / 48_000)
    final_metadata = inspect_wav(Path(workflow.audio_final_path))
    assert final_metadata.sample_rate == 48_000
    assert final_metadata.channels == 1
    assert workflow.audio_final_sha256 == final_metadata.sha256
    assert repo.transcription.audio_sha256 == final_metadata.sha256
    assert transcription.calls == 1
    assert alignment.calls == [workflow.id]

    await service.process(workflow.id)
    assert len(voice.created) == 2
    assert transcription.calls == 1
    assert alignment.calls == [workflow.id, workflow.id]


@pytest.mark.asyncio
async def test_changed_text_regenerates_only_stale_block(tmp_path: Path, write_tone) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={
            "blocos": [
                {"tipo": "intro", "conteudo": "Primeiro."},
                {"tipo": "body", "conteudo": "Segundo."},
            ]
        },
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
    )
    service.repo = repo
    await service.process(workflow.id)
    first_identity = repo.blocks[0].synthesis_identity
    second_identity = repo.blocks[1].synthesis_identity
    workflow.roteiro_snapshot = copy.deepcopy(workflow.roteiro_snapshot)
    workflow.roteiro_snapshot["blocos"][1]["conteudo"] = "Segundo alterado."
    await service.process(workflow.id)
    assert len(voice.created) == 3
    assert repo.blocks[0].synthesis_identity == first_identity
    assert repo.blocks[1].synthesis_identity != second_identity


@pytest.mark.parametrize("changed_input", ["voice", "generation", "glossary"])
@pytest.mark.asyncio
async def test_profile_or_glossary_change_invalidates_completed_block(
    changed_input: str, tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    config = _config(tmp_path, glossary)
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
    )
    service.repo = repo
    await service.process(workflow.id)

    if changed_input == "voice":
        config.voice_profile = "bernardo_v2"
    elif changed_input == "generation":
        config.voice_generation_profile = "narration_v2"
    else:
        glossary.write_text(
            "version: '2'\nglobal: {Texto: Téxto}\nprojects: {}\n", encoding="utf-8"
        )
        service.preprocessor = PronunciationPreprocessor.load(glossary)

    await service.process(workflow.id)
    assert len(voice.created) == 2


@pytest.mark.asyncio
async def test_missing_local_file_is_recovered_from_same_remote_job(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
    )
    service.repo = repo
    await service.process(workflow.id)
    original_job = repo.blocks[0].job_id_voz
    Path(repo.blocks[0].audio_path).unlink()
    await service.process(workflow.id)
    assert len(voice.created) == 1
    assert repo.blocks[0].job_id_voz == original_job
    assert Path(repo.blocks[0].audio_path).exists()


@pytest.mark.asyncio
async def test_failed_remote_job_gets_new_attempt_with_same_resolved_seed(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
    )
    service.repo = repo
    await service.process(workflow.id)
    block = repo.blocks[0]
    original_seed = block.resolved_seed
    Path(block.audio_path).unlink()
    voice.jobs[block.job_id_voz] = voice.jobs[block.job_id_voz].model_copy(
        update={"status": "failed", "error_message": "provider failure"}
    )
    workflow.status = "erro"
    await service.process(workflow.id)
    assert len(voice.created) == 2
    assert voice.created[-1]["seed"] == original_seed
    assert voice.created[-1]["idempotency_key"].endswith(":retry:1")
    assert block.generation_attempt == 1
    assert workflow.status == "audio_concluido"


@pytest.mark.asyncio
async def test_cancelled_workflow_does_no_work(tmp_path: Path) -> None:
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="cancelada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(b"")
    service = AudioWorkflowService(
        object(), config=_config(tmp_path, glossary), voice_client_factory=lambda: voice
    )
    service.repo = repo
    await service.process(workflow.id)
    assert voice.created == []


@pytest.mark.asyncio
async def test_transcription_failure_does_not_invalidate_final_audio(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = FakeRepository(workflow)
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: FakeVoiceClient(tone.read_bytes()),
        transcription_provider=FailingTranscriptionProvider(),
    )
    service.repo = repo

    await service.process(workflow.id)

    assert workflow.status == "audio_concluido"
    assert workflow.audio_phase == "completed"
    assert Path(workflow.audio_final_path).is_file()
    assert repo.transcription is None
