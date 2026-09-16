from __future__ import annotations

import copy
import hashlib
import io
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.audio_service as audio_service_module
from app.clients.voice_client import (
    GenerationProfileDescriptor,
    VoiceNarrationJob,
    VoiceProfileDescriptor,
    VoiceProfileProvenance,
)
from app.core.config import Settings
from app.services.audio_service import (
    AudioWorkflowService,
    derive_manual_chunk_seed,
    derive_quality_retry_seed,
)
from app.services.production_audio import inspect_wav
from app.services.pronunciation import PronunciationPreprocessor
from app.services.text_normalization import PortugueseTextNormalizer
from app.services.transcription_service import (
    NarrationTranscription,
    NarrationWord,
)


@pytest.fixture(autouse=True)
def _fake_manifest_verifier(monkeypatch):
    async def verify(_session, block):
        path = getattr(block, "audio_path", None)
        identity = getattr(block, "assembly_manifest_identity", None)
        return bool(path and identity and Path(path).is_file())

    monkeypatch.setattr(audio_service_module, "active_block_is_current", verify)


class FakeRepository:
    def __init__(self, workflow) -> None:
        self.workflow = workflow
        self.blocks: dict[int, SimpleNamespace] = {}
        self.transcription = None
        self.physical_chunks: dict[uuid.UUID, list[SimpleNamespace]] = {}

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
                initial_resolved_seed=None,
                generation_attempt=0,
                retry_seed_strategy=None,
                generation_attempt_history=None,
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
                "initial_resolved_seed": None,
                "generation_attempt": 0,
                "retry_seed_strategy": None,
                "generation_attempt_history": None,
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

    async def listar_chunks(self, block_id):
        return self.physical_chunks.get(block_id, [])

    async def persistir_job_chunks(self, block_id, chunks_data, *, promote=False):
        selections = []
        chunks = []
        for values in chunks_data:
            chunk = SimpleNamespace(
                id=uuid.uuid4(),
                audio_bloco_id=block_id,
                chunk_index=values["chunk_index"],
                current_take_id=None,
                pause_after_frames=values["pause_after_frames"],
            )
            take = SimpleNamespace(
                id=uuid.uuid4(),
                audio_chunk_id=chunk.id,
                status="completed",
                audio_path=values["audio_path"],
                audio_sha256=values["audio_sha256"],
                audio_frame_count=values["audio_frame_count"],
                audio_sample_rate=values["sample_rate"],
                audio_channels=values["audio_channels"],
                audio_subtype=values["audio_subtype"],
            )
            if promote:
                chunk.current_take_id = take.id
            chunks.append(chunk)
            selections.append((chunk, take))
        self.physical_chunks[block_id] = chunks
        return selections


class ChunkPersistingRepository(FakeRepository):
    def __init__(self, workflow) -> None:
        super().__init__(workflow)
        self.persisted_chunks: list[tuple[uuid.UUID, list[dict]]] = []

    async def persistir_job_chunks(self, block_id, chunks_data, *, promote=False):
        self.persisted_chunks.append((block_id, chunks_data))
        return await super().persistir_job_chunks(block_id, chunks_data, promote=promote)


class FakeVoiceClient:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.created: list[dict] = []
        self.jobs: dict[str, VoiceNarrationJob] = {}
        self.chunking_identity = "chunk-v1"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def resolve_profiles(self, voice, generation):
        return VoiceProfileProvenance(
            VoiceProfileDescriptor(
                name=voice,
                version="2",
                identity="v" * 64,
                clone_mode="x_vector_only",
                reference_id="ref_full_processed_raw.wav",
                audio_sha256=("8856924e05f76d1a3a5b8554218f80b5e1a898aebfe211ec1b65b60eabdbc70b"),
            ),
            GenerationProfileDescriptor(name=generation, version="2", identity="g" * 64),
            chunking_identity=self.chunking_identity,
        )

    async def create_narration(self, **payload):
        self.created.append(payload)
        seed = payload.get("seed") or (1000 + len(self.created))
        job = _job(f"job-{len(self.created)}", seed)
        job = job.model_copy(
            update={
                "metadata": {
                    **job.metadata,
                    "chunks": [{"index": 0}],
                    "language": "Portuguese",
                }
            }
        )
        self.jobs[job.id] = job
        return job.model_copy(update={"status": "queued"})

    async def get_narration(self, job_id):
        return self.jobs[job_id]

    async def wait_for_narration(self, job_id, **kwargs):
        return self.jobs[job_id]

    async def download_audio(self, job_id):
        return self.audio

    async def list_chunks(self, job_id):
        with wave.open(io.BytesIO(self.audio), "rb") as wav:
            frame_count = wav.getnframes()
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
        text = self.created[int(job_id.rsplit("-", 1)[1]) - 1]["text"]
        return [
            {
                "chunk_id": f"{job_id}_0000",
                "job_id": job_id,
                "chunk_index": 0,
                "text": text,
                "synthesis_text": text,
                "start_char": 0,
                "end_char": len(text),
                "boundary": "sentence",
                "pause_after_frames": 0,
                "chunking_identity": "chunk-v1",
                "chunking_strategy_version": "qwen3-tts-chunking-v2",
                "voice_identity": "v" * 64,
                "generation_identity": "g" * 64,
                "seed": self.jobs[job_id].resolved_seed,
                "duration_seconds": frame_count / sample_rate,
                "sample_rate": sample_rate,
                "channels": channels,
                "subtype": "PCM_16",
                "byte_size": len(self.audio),
                "sha256": hashlib.sha256(self.audio).hexdigest(),
                "frame_count": frame_count,
            }
        ]

    async def download_chunk_audio(self, job_id, chunk_index):
        assert chunk_index == 0
        return self.audio


class ChunkVoiceClient(FakeVoiceClient):
    def __init__(self, audio: bytes, metadata) -> None:
        super().__init__(audio)
        self.chunk_metadata = metadata

    async def create_narration(self, **payload):
        queued = await super().create_narration(**payload)
        completed = self.jobs[queued.id]
        self.jobs[queued.id] = completed.model_copy(
            update={
                "metadata": {
                    **completed.metadata,
                    "chunks": [{"index": 0}],
                }
            }
        )
        return queued

    async def list_chunks(self, job_id):
        return [
            {
                "chunk_id": f"{job_id}_0000",
                "job_id": job_id,
                "chunk_index": 0,
                "text": "Texto físico.",
                "synthesis_text": "Texto físico.",
                "start_char": 0,
                "end_char": 14,
                "boundary": "sentence",
                "pause_after_frames": 0,
                "chunking_identity": "chunk-v1",
                "chunking_strategy_version": "qwen3-tts-chunking-v2",
                "voice_identity": "v" * 64,
                "generation_identity": "g" * 64,
                "seed": 123,
                "duration_seconds": self.chunk_metadata.duration_seconds,
                "sample_rate": self.chunk_metadata.sample_rate,
                "byte_size": self.chunk_metadata.byte_size,
                "sha256": self.chunk_metadata.sha256,
                "frame_count": self.chunk_metadata.frame_count,
            }
        ]

    async def download_chunk_audio(self, job_id, chunk_index):
        assert chunk_index == 0
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


class ScriptedTranscriptionProvider:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls = 0

    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        words = tuple(
            NarrationWord(word, index * 0.05, index * 0.05 + 0.04)
            for index, word in enumerate(text.split())
        )
        return NarrationTranscription(
            text=text,
            words=words,
            provider="fake",
            model="fake-v1",
            language="pt",
        )


class LowConfidenceTranscriptionProvider:
    def transcribe(self, audio_path: Path) -> NarrationTranscription:
        return NarrationTranscription(
            text="conteúdo possivelmente incompleto",
            words=(
                NarrationWord("conteúdo", 0.0, 0.2, probability=0.12),
                NarrationWord("possivelmente", 0.2, 0.4, probability=0.18),
                NarrationWord("incompleto", 0.4, 0.6, probability=0.15),
            ),
            provider="fake",
            model="fake-low-confidence",
            language="pt",
        )


class FakeAlignmentService:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def ensure(self, workflow_id: uuid.UUID):
        self.calls.append(workflow_id)


class FailingAlignmentService:
    async def ensure(self, workflow_id: uuid.UUID):
        raise RuntimeError("alignment unavailable")


class ScriptedAccentDetector:
    def __init__(self, classifications: list[str], mode: str) -> None:
        self.classifications = classifications
        self.mode = mode
        self.calls = 0

    def analyze(self, _path: Path) -> dict[str, object]:
        classification = self.classifications[min(self.calls, len(self.classifications) - 1)]
        self.calls += 1
        likely = classification == "likely_pt_pt"
        return {
            "classification": classification,
            "detector_status": "available",
            "reason_code": "accent_likely_pt_pt" if likely else "accent_pt_br_confident",
            "retry_required": self.mode == "enforce" and likely,
            "blocks_promotion": self.mode == "enforce" and likely,
        }


def _job(job_id: str, seed: int) -> VoiceNarrationJob:
    return VoiceNarrationJob(
        id=job_id,
        status="completed",
        voice_profile="bernardo_xvec_v2",
        generation_profile="narration_v2",
        resolved_seed=seed,
        model_id="Qwen/Qwen3-TTS",
        model_revision="revision",
        metadata={
            "voice_clone_mode": "x_vector_only",
            "voice_reference_id": "ref_full_processed_raw.wav",
            "voice_reference_sha256": (
                "8856924e05f76d1a3a5b8554218f80b5e1a898aebfe211ec1b65b60eabdbc70b"
            ),
            "chunking_identity": "chunk-v1",
        },
    )


def _config(tmp_path: Path, glossary: Path) -> Settings:
    return Settings(
        _env_file=None,
        audio_storage_path=str(tmp_path / "audio"),
        pronunciation_glossary_path=glossary,
        voice_poll_interval_seconds=0,
        audio_take_content_validation_enabled=False,
    )


def test_manual_chunk_seed_is_stable_per_idempotent_operation_and_distinct_between_ops() -> None:
    block_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    values = {
        "block_id": block_id,
        "chunk_id": chunk_id,
        "synthesis_text_snapshot": "Síntese congelada.",
    }
    first = derive_manual_chunk_seed(**values, idempotency_key="operation-a")
    replay = derive_manual_chunk_seed(**values, idempotency_key="operation-a")
    second = derive_manual_chunk_seed(**values, idempotency_key="operation-b")
    assert 1 <= first <= 2**31 - 1
    assert replay == first
    assert second != first


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
    assert repo.blocks[0].text_normalizer_version == "pt_br_text_normalizer_v1"
    assert len(repo.blocks[0].text_normalizer_identity) == 64
    assert repo.blocks[0].resolved_seed == 303
    assert repo.blocks[1].resolved_seed == 303
    assert voice.created[0]["voice_profile"] == "bernardo_xvec_v3"
    assert voice.created[0]["generation_profile"] == "narration_v2"
    assert voice.created[0]["seed"] == 303
    assert repo.blocks[0].voice_profile_identity == "v" * 64
    assert repo.blocks[0].generation_profile_identity == "g" * 64
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
async def test_completed_block_persists_every_physical_chunk_with_plan_metadata(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", sample_rate=24_000, duration=1.0)
    tone_metadata = inspect_wav(tone)
    glossary = _write_glossary(tmp_path)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto físico."}]},
    )
    repo = ChunkPersistingRepository(workflow)
    voice = ChunkVoiceClient(tone.read_bytes(), tone_metadata)
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
    )
    service.repo = repo

    await service.process(workflow.id)

    assert workflow.status == "audio_concluido"
    assert len(repo.persisted_chunks) == 1
    block_id, chunks = repo.persisted_chunks[0]
    assert block_id == repo.blocks[0].id
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["synthesis_text"] == "Texto físico."
    assert (chunk["start_char"], chunk["end_char"], chunk["boundary"]) == (
        0,
        14,
        "sentence",
    )
    assert chunk["chunking_identity"] == "chunk-v1"
    assert chunk["pause_after_frames"] == 0
    assert chunk["voice_profile_identity"] == "v" * 64
    assert chunk["generation_profile_identity"] == "g" * 64
    assert Path(chunk["audio_path"]).read_bytes() == tone.read_bytes()
    assert chunk["audio_sha256"] == tone_metadata.sha256


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


@pytest.mark.asyncio
async def test_chunking_identity_change_does_not_reuse_local_wav(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    service = AudioWorkflowService(
        object(), config=_config(tmp_path, glossary), voice_client_factory=lambda: voice
    )
    service.repo = repo
    await service.process(workflow.id)
    voice.chunking_identity = "chunk-v2"
    workflow.status = "erro"
    await service.process(workflow.id)
    assert len(voice.created) == 2


def _write_glossary(tmp_path: Path) -> Path:
    path = tmp_path / "glossary.yaml"
    path.write_text("version: '1'\nglobal: {}\nprojects: {}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("changed_input", ["voice", "generation", "glossary", "normalizer"])
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
        config.voice_generation_profile = "narration_v1"
    elif changed_input == "glossary":
        glossary.write_text(
            "version: '2'\nglobal: {Texto: Téxto}\nprojects: {}\n", encoding="utf-8"
        )
        service.preprocessor = PronunciationPreprocessor.load(glossary)
    else:
        service.text_normalizer = PortugueseTextNormalizer(version="pt_br_text_normalizer_v2")

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
async def test_quality_failed_remote_job_gets_new_traceable_seed(
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
        update={
            "status": "failed",
            "error_category": "chunk_quality_guard",
            "error_message": "generated speech chunk failed quality validation",
        }
    )
    workflow.status = "erro"
    await service.process(workflow.id)
    assert len(voice.created) == 2
    assert voice.created[-1]["seed"] != original_seed
    assert voice.created[-1]["seed"] == derive_quality_retry_seed(original_seed, 1)
    assert voice.created[-1]["idempotency_key"].endswith(":quality-retry:1")
    assert block.generation_attempt == 1
    assert block.initial_resolved_seed == original_seed
    assert block.retry_seed_strategy == "qwen-orchestrator-quality-retry-v1"
    assert block.generation_attempt_history == [
        {
            "generation_attempt": 0,
            "job_id": "job-1",
            "seed": original_seed,
            "error_category": "chunk_quality_guard",
            "error_message": "generated speech chunk failed quality validation",
        }
    ]
    assert workflow.status == "audio_concluido"


@pytest.mark.asyncio
async def test_permanent_remote_failure_is_not_retried(tmp_path: Path, write_tone) -> None:
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
    Path(block.audio_path).unlink()
    voice.jobs[block.job_id_voz] = voice.jobs[block.job_id_voz].model_copy(
        update={
            "status": "failed",
            "error_category": "profile_configuration",
            "error_message": "profile unavailable",
        }
    )
    workflow.status = "erro"
    await service.process(workflow.id)
    assert len(voice.created) == 1
    assert block.generation_attempt == 0
    assert block.generation_attempt_history is None
    assert workflow.status == "erro"


@pytest.mark.asyncio
async def test_missing_spoken_content_retries_before_take_becomes_active(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    synthesis = "A primeira frase explica todo o conteúdo e confirma a conclusão final."
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": synthesis}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    transcription = ScriptedTranscriptionProvider(
        ["A primeira frase explica", synthesis, synthesis]
    )
    config = _config(tmp_path, glossary)
    config.audio_take_content_validation_enabled = True
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=transcription,
        alignment_service_factory=lambda session: FakeAlignmentService(),  # type: ignore[arg-type]
    )
    service.repo = repo
    await service.process(workflow.id)
    assert workflow.status == "audio_concluido"
    assert len(voice.created) == 2
    assert voice.created[1]["seed"] == derive_quality_retry_seed(303, 1)
    assert repo.blocks[0].generation_attempt_history[0]["error_category"] == (
        "content_quality_guard"
    )


@pytest.mark.asyncio
async def test_local_pronunciation_disagreement_is_accepted_without_quality_retry(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text(
        "version: '1'\nglobal: {GTA: Gê Tê Á}\nprojects: {}\n",
        encoding="utf-8",
    )
    canonical = (
        "Em 2008, GTA IV apresentou o motor atualizado. "
        "Depois os veículos atravessaram a cidade no Xbox 360. "
        "No final a missão continuou normalmente e confirmou todo o objetivo."
    )
    asr = (
        "Em 2008, GTA Aive apresentou o môtór atualizado. "
        "Depois os vaículos atravessaram a cidade no ixbox trezentos e sessenta. "
        "No final a missão continuou normalmente e confirmou todo o objetivo."
    )
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": canonical}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    config = _config(tmp_path, glossary)
    config.audio_take_content_validation_enabled = True
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=ScriptedTranscriptionProvider([asr]),
        alignment_service_factory=lambda session: FakeAlignmentService(),  # type: ignore[arg-type]
    )
    service.repo = repo

    await service.process(workflow.id)

    assert workflow.status == "audio_concluido"
    assert len(voice.created) == 1
    assert repo.blocks[0].generation_attempt == 0
    assert repo.blocks[0].generation_attempt_history is None


@pytest.mark.asyncio
async def test_content_quality_retry_is_bounded(tmp_path: Path, write_tone) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    synthesis = "A frase completa possui muitos detalhes importantes no encerramento."
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": synthesis}]},
    )
    repo = FakeRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    config = _config(tmp_path, glossary)
    config.audio_take_content_validation_enabled = True
    config.voice_quality_max_retries = 0
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=ScriptedTranscriptionProvider(["A frase completa"]),
    )
    service.repo = repo
    await service.process(workflow.id)
    assert workflow.status == "erro"
    assert len(voice.created) == 1
    assert repo.blocks[0].generation_attempt_history[0]["error_category"] == (
        "content_quality_guard"
    )


@pytest.mark.asyncio
async def test_accent_shadow_persists_signal_without_retry(tmp_path: Path, write_tone) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = ChunkPersistingRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    detector = ScriptedAccentDetector(["likely_pt_pt"], "shadow")
    service = AudioWorkflowService(
        object(),
        config=_config(tmp_path, glossary),
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
        accent_detector=detector,  # type: ignore[arg-type]
    )
    service.repo = repo
    await service.process(workflow.id)
    assert workflow.status == "audio_concluido"
    assert len(voice.created) == 1
    assert repo.persisted_chunks[0][1][0]["accent_quality"]["classification"] == ("likely_pt_pt")


@pytest.mark.asyncio
async def test_accent_enforce_retries_with_deterministic_seed_before_promotion(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = ChunkPersistingRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    config = _config(tmp_path, glossary)
    config.audio_accent_qa_mode = "enforce"
    detector = ScriptedAccentDetector(["likely_pt_pt", "pt_br_confident"], "enforce")
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
        accent_detector=detector,  # type: ignore[arg-type]
    )
    service.repo = repo
    await service.process(workflow.id)
    block = repo.blocks[0]
    assert workflow.status == "audio_concluido"
    assert len(voice.created) == 2
    assert voice.created[1]["seed"] == derive_quality_retry_seed(303, 1)
    assert block.generation_attempt_history[0]["error_category"] == "accent_quality_guard"
    assert block.generation_attempt_history[0]["retry_reason"] == "accent_likely_pt_pt"
    assert all(chunk.current_take_id is None for chunk in repo.physical_chunks[block.id])


@pytest.mark.asyncio
async def test_accent_retry_exhaustion_requires_human_review(tmp_path: Path, write_tone) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="agendada",
        roteiro_snapshot={"blocos": [{"tipo": "intro", "conteudo": "Texto."}]},
    )
    repo = ChunkPersistingRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    config = _config(tmp_path, glossary)
    config.audio_accent_qa_mode = "enforce"
    config.voice_quality_max_retries = 1
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
        accent_detector=ScriptedAccentDetector(["likely_pt_pt"], "enforce"),  # type: ignore[arg-type]
    )
    service.repo = repo
    await service.process(workflow.id)
    assert workflow.status == "erro"
    assert len(voice.created) == 2
    assert repo.blocks[0].erro_mensagem.startswith("accent_human_review_required")


def test_low_confidence_asr_is_inconclusive_not_content_rejection(
    tmp_path: Path, write_tone
) -> None:
    tone = write_tone(tmp_path / "voice.wav", duration=1.0)
    glossary = _write_glossary(tmp_path)
    config = _config(tmp_path, glossary)
    config.audio_take_content_validation_enabled = True
    service = AudioWorkflowService(
        object(),
        config=config,
        transcription_provider=LowConfidenceTranscriptionProvider(),
    )

    report = service._validate_take_content(
        tone,
        "O conteúdo completo possui várias palavras que não podem desaparecer.",
    )

    assert report["status"] == "unavailable"
    assert report["reasons"] == ["asr_low_confidence"]
    assert report["asr"]["confidence_status"] == "low_confidence"
    assert report["metrics"]["raw_content_coverage"]["status"] == "rejected"


def test_accent_warning_never_masks_blocking_content_quality() -> None:
    combined = AudioWorkflowService._combined_quality_status(
        {"status": "rejected"},
        {"classification": "uncertain", "blocks_promotion": False},
    )
    assert combined == "rejected"


@pytest.mark.asyncio
async def test_quality_retry_limit_is_enforced_and_diagnostics_preserved(
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
    config = _config(tmp_path, glossary)
    config.voice_quality_max_retries = 0
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        transcription_provider=FakeTranscriptionProvider(),
    )
    service.repo = repo
    await service.process(workflow.id)
    block = repo.blocks[0]
    Path(block.audio_path).unlink()
    voice.jobs[block.job_id_voz] = voice.jobs[block.job_id_voz].model_copy(
        update={
            "status": "failed",
            "error_category": "chunk_quality_guard",
            "error_message": "bad duration",
        }
    )
    workflow.status = "erro"
    await service.process(workflow.id)
    assert len(voice.created) == 1
    assert block.generation_attempt_history[0]["error_category"] == "chunk_quality_guard"
    assert workflow.status == "erro"


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
    assert workflow.audio_phase == "quality_unvalidated"
    assert Path(workflow.audio_final_path).is_file()
    assert repo.transcription is None


@pytest.mark.asyncio
async def test_alignment_failure_preserves_audio_but_marks_quality_unvalidated(
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
        transcription_provider=FakeTranscriptionProvider(),
        alignment_service_factory=lambda session: FailingAlignmentService(),  # type: ignore[arg-type]
    )
    service.repo = repo

    await service.process(workflow.id)

    assert workflow.status == "audio_concluido"
    assert workflow.audio_phase == "quality_unvalidated"
    assert Path(workflow.audio_final_path).is_file()
    assert repo.transcription is not None


@pytest.mark.asyncio
async def test_english_audio_passthrough_and_explicit_asr(tmp_path, write_tone, monkeypatch):
    from unittest.mock import Mock
    from app.services.audio_service import ensure_transcription

    text = "On 12/25, GTA IV cost $20.00."
    glossary = tmp_path / "glossary.json"
    glossary.write_text('{"version":"v1","rules":[]}')
    tone = write_tone(tmp_path / "tone.wav", duration=1.0)
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=uuid.uuid4(),
        status="pendente",
        roteiro_snapshot={
            "content_locale": "en-US",
            "blocos": [{"tipo": "intro", "conteudo": text}],
        },
    )
    repo = ChunkPersistingRepository(workflow)
    voice = FakeVoiceClient(tone.read_bytes())
    config = _config(tmp_path, glossary)
    config.audio_take_content_validation_enabled = True
    config.audio_accent_qa_mode = "enforce"
    pt_normalizer = Mock()
    pt_normalizer.normalize.side_effect = AssertionError("PT normalizer called")
    pt_pronunciation = Mock()
    pt_pronunciation.preprocess.side_effect = AssertionError("PT glossary called")
    pt_detector = Mock()
    pt_detector.analyze.side_effect = AssertionError("PT accent called")
    provider = FakeTranscriptionProvider()
    provider_factory = Mock(return_value=provider)
    monkeypatch.setattr(audio_service_module, "FasterWhisperNarrationProvider", provider_factory)
    service = AudioWorkflowService(
        object(),
        config=config,
        voice_client_factory=lambda: voice,
        text_normalizer=pt_normalizer,
        preprocessor=pt_pronunciation,
        accent_detector=pt_detector,
    )
    service.repo = repo
    await service.process(workflow.id)
    assert workflow.status == "audio_concluido"
    assert (
        repo.blocks[0].texto
        == repo.blocks[0].normalized_text
        == repo.blocks[0].synthesis_text
        == text
    )
    assert repo.blocks[0].pronunciation_glossary_identity is None
    assert voice.created[0]["voice_profile"] == "bernardo_en_icl_v1"
    assert voice.created[0]["language"] == "English"
    assert voice.created[0]["seed"] == 303
    assert repo.persisted_chunks[0][1][0]["requested_language"] == "English"
    assert provider.calls == 1  # final ASR only; no take content ASR
    provider_factory.assert_called_once_with(asr_language="en")
    report = service._validate_take_content(tone, text, content_locale="en-US")
    assert report["status"] == "unavailable"
    # Same transcription boundary used by audio edits/rematerialization.
    await ensure_transcription(
        object(), workflow.id, tone, "new-audio-sha", repository=repo, content_locale="en-US"
    )
    assert provider_factory.call_count == 2
    pt_normalizer.normalize.assert_not_called()
    pt_pronunciation.preprocess.assert_not_called()
    pt_detector.analyze.assert_not_called()
