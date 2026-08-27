from __future__ import annotations

import copy
import datetime as dt
import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.workflow import AudioBloco, NarrationAlignment, TranscricaoAudio, WorkflowExecucao
from app.services.narration_alignment_service import NarrationAlignmentService


class FakeWorkflowRepository:
    def __init__(self, workflow: WorkflowExecucao) -> None:
        self.workflow = workflow

    async def obter_por_id(self, workflow_id: uuid.UUID, **kwargs):
        return self.workflow if workflow_id == self.workflow.id else None


class FakeAlignmentRepository:
    def __init__(self) -> None:
        self.by_fingerprint: dict[str, NarrationAlignment] = {}
        self.output_writes = 0

    async def get_by_fingerprint(self, fingerprint: str, **kwargs):
        return self.by_fingerprint.get(fingerprint)

    async def create_or_get(self, **values):
        existing = self.by_fingerprint.get(values["fingerprint"])
        if existing is not None:
            return existing, False
        alignment = NarrationAlignment(
            id=uuid.uuid4(),
            workflow_execucao_id=values["workflow_id"],
            transcription_id=values["transcription_id"],
            final_audio_sha256=values["final_audio_sha256"],
            script_fingerprint=values["script_fingerprint"],
            transcription_fingerprint=values["transcription_fingerprint"],
            strategy_version=values["strategy_version"],
            fingerprint=values["fingerprint"],
            status="pending",
            block_count=0,
            resolved_visual_queries=0,
            unresolved_visual_queries=0,
            ambiguous_visual_queries=0,
            not_applicable_visual_queries=0,
            criado_em=dt.datetime.now(dt.UTC),
        )
        alignment.block_alignments = []
        alignment.visual_query_bindings = []
        self.by_fingerprint[alignment.fingerprint] = alignment
        return alignment, True

    async def mark_processing(self, alignment):
        alignment.status = "processing"

    async def clear_outputs(self, alignment):
        alignment.block_alignments = []
        alignment.visual_query_bindings = []

    async def mark_blocked(self, alignment, reason):
        alignment.status = "blocked"
        alignment.error_message = reason

    async def mark_failed(self, alignment, reason):
        alignment.status = "failed"
        alignment.error_message = reason

    async def replace_outputs(self, alignment, *, blocks, bindings, metrics):
        self.output_writes += 1
        alignment.block_alignments = list(blocks)
        alignment.visual_query_bindings = list(bindings)
        alignment.block_count = len(blocks)
        alignment.resolved_visual_queries = sum(item.status == "resolved" for item in bindings)
        alignment.unresolved_visual_queries = sum(item.status == "unresolved" for item in bindings)
        alignment.ambiguous_visual_queries = sum(item.status == "ambiguous" for item in bindings)
        alignment.not_applicable_visual_queries = sum(
            item.status == "not_applicable" for item in bindings
        )
        alignment.metrics = metrics
        alignment.status = "completed"

    async def reload(self, alignment):
        return alignment


def _query(excerpt: str | None, *, order: int = 0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "order": order,
        "intent": "gameplay_event",
        "query": "Geralt learning Aard",
        "narration_excerpt": excerpt,
        "importance": "required",
        "source_note_id": str(uuid.uuid4()),
        "preferred_session_id": str(uuid.uuid4()),
        "preferred_start_seconds": 1200.0,
        "preferred_end_seconds": 1240.0,
    }


def _workflow(*, queries: list[dict] | None = None) -> WorkflowExecucao:
    workflow_id = uuid.uuid4()
    workflow = WorkflowExecucao(
        id=workflow_id,
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={
            "titulo": "Aard",
            "duracao_total_segundos": 10,
            "blocos": [
                {
                    "ordem": 0,
                    "tipo": "intro",
                    "conteudo": "Depois da luta, Geralt aprende o sinal Aard pela primeira vez.",
                    "visual_queries": queries or [],
                }
            ],
        },
        status="audio_concluido",
        audio_final_sha256="a" * 64,
        duracao_audio_segundos=8.0,
        audio_final_sample_rate=48_000,
        audio_final_channels=1,
    )
    workflow.blocos = [
        AudioBloco(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow_id,
            ordem=0,
            tipo="intro",
            texto="Depois da luta, Geralt aprende o sinal Aard pela primeira vez.",
            synthesis_text="Depois da luta, Geralt aprende o sinal Árd pela primeira vez.",
            source_text_sha256="b" * 64,
            synthesis_text_sha256="c" * 64,
            synthesis_identity="d" * 64,
            narration_start_seconds=0.0,
            narration_end_seconds=8.0,
            status="concluido",
        )
    ]
    workflow.transcricao = TranscricaoAudio(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        texto_completo="Depois da luta Geralt aprende o sinal Arde pela primeira vez",
        palavras_timestamps=[
            {"palavra": word, "inicio": index * 0.5, "fim": index * 0.5 + 0.4}
            for index, word in enumerate(
                [
                    "Depois",
                    "da",
                    "luta",
                    "Geralt",
                    "aprende",
                    "o",
                    "sinal",
                    "Arde",
                    "pela",
                    "primeira",
                    "vez",
                ]
            )
        ],
        audio_sha256="a" * 64,
        provider="fake",
        model="fake-v1",
        language="pt",
    )
    return workflow


def _service(workflow: WorkflowExecucao, *, strategy: str | None = None):
    service = NarrationAlignmentService(
        object(),  # type: ignore[arg-type]
        config=Settings(_env_file=None),
        strategy_version=strategy,
    )
    workflows = FakeWorkflowRepository(workflow)
    alignments = FakeAlignmentRepository()
    service.workflows = workflows  # type: ignore[assignment]
    service.alignments = alignments  # type: ignore[assignment]
    return service, alignments


def test_alignment_configuration_rejects_unsafe_thresholds_and_guards() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, narration_alignment_fuzzy_min_score=1.1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, narration_alignment_max_cells=0)


@pytest.mark.asyncio
async def test_realistic_binding_uses_asr_time_and_leaves_gameplay_provenance_untouched() -> None:
    query = _query("Geralt aprende o sinal Aard")
    workflow = _workflow(queries=[query])
    snapshot_before = copy.deepcopy(workflow.roteiro_snapshot)
    service, _ = _service(workflow)

    alignment, created = await service.ensure(workflow.id)

    assert created is True
    assert alignment.status == "completed"
    binding = alignment.visual_query_bindings[0]
    assert binding.status == "resolved"
    assert binding.narration_start_seconds == 1.5
    assert binding.narration_end_seconds == 3.9
    assert binding.speech_alignment_coverage == 1.0
    assert workflow.roteiro_snapshot == snapshot_before
    assert query["preferred_start_seconds"] == 1200.0
    assert binding.narration_start_seconds != query["preferred_start_seconds"]


@pytest.mark.asyncio
async def test_same_identity_reuses_run_while_changed_inputs_create_new_runs() -> None:
    workflow = _workflow(queries=[_query("Geralt aprende o sinal Aard")])
    service, repository = _service(workflow)
    first, _ = await service.ensure(workflow.id)
    repeated, created = await service.ensure(workflow.id)
    assert repeated.id == first.id
    assert created is False
    assert repository.output_writes == 1
    rebuilt, _ = await service.ensure(workflow.id, force=True)
    assert rebuilt.id == first.id
    assert repository.output_writes == 2

    workflow.audio_final_sha256 = "f" * 64
    assert workflow.transcricao is not None
    workflow.transcricao.audio_sha256 = "f" * 64
    changed_audio, _ = await service.ensure(workflow.id)
    assert changed_audio.id != first.id

    workflow.roteiro_snapshot["blocos"][0]["conteudo"] += " Extra."
    changed_script, _ = await service.ensure(workflow.id)
    assert changed_script.id not in {first.id, changed_audio.id}
    assert changed_script.status == "blocked"

    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]["narration_excerpt"] = "Aard"
    changed_excerpt, _ = await service.ensure(workflow.id)
    assert changed_excerpt.id != changed_script.id

    versioned = NarrationAlignmentService(
        object(),  # type: ignore[arg-type]
        config=Settings(_env_file=None),
        strategy_version="narration_alignment_v2",
    )
    versioned.workflows = service.workflows  # type: ignore[assignment]
    versioned.alignments = repository  # type: ignore[assignment]
    changed_strategy, _ = await versioned.ensure(workflow.id)
    assert changed_strategy.id != changed_excerpt.id


@pytest.mark.asyncio
async def test_missing_or_stale_transcription_is_blocked_without_touching_audio() -> None:
    workflow = _workflow()
    final_sha = workflow.audio_final_sha256
    workflow.transcricao = None
    service, _ = _service(workflow)
    missing, _ = await service.ensure(workflow.id)
    assert missing.status == "blocked"
    assert "unavailable" in (missing.error_message or "")
    assert workflow.status == "audio_concluido"
    assert workflow.audio_final_sha256 == final_sha

    workflow.transcricao = TranscricaoAudio(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow.id,
        texto_completo="stale",
        palavras_timestamps=[],
        audio_sha256="0" * 64,
    )
    stale, _ = await service.ensure(workflow.id)
    assert stale.status == "blocked"
    assert "different final narration" in (stale.error_message or "")


@pytest.mark.asyncio
async def test_corrupt_timestamps_or_invalid_query_id_fail_only_alignment() -> None:
    workflow = _workflow(queries=[_query("Geralt")])
    assert workflow.transcricao is not None
    workflow.transcricao.palavras_timestamps[0]["inicio"] = 99.0
    service, _ = _service(workflow)
    corrupt, _ = await service.ensure(workflow.id)
    assert corrupt.status == "failed"
    assert workflow.status == "audio_concluido"

    workflow = _workflow(queries=[_query("Geralt")])
    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]["id"] = "invalid"
    service, _ = _service(workflow)
    invalid, _ = await service.ensure(workflow.id)
    assert invalid.status == "failed"
    assert invalid.error_message == "visual query ID is invalid"


@pytest.mark.asyncio
async def test_zero_word_block_never_fabricates_timing_and_null_excerpt_is_not_applicable() -> None:
    workflow = _workflow(queries=[_query("Geralt"), _query(None, order=1)])
    assert workflow.transcricao is not None
    workflow.transcricao.palavras_timestamps = []
    service, _ = _service(workflow)
    alignment, _ = await service.ensure(workflow.id)
    assert alignment.status == "blocked"
    assert alignment.metrics["speech_quality"]["status"] == "invalid"
    assert alignment.block_alignments[0].status == "no_words"
    assert alignment.visual_query_bindings[0].status == "unresolved"
    assert alignment.visual_query_bindings[0].narration_start_seconds is None
    assert alignment.visual_query_bindings[1].status == "not_applicable"


@pytest.mark.asyncio
async def test_failed_force_rebuild_clears_only_previous_derived_outputs(monkeypatch) -> None:
    workflow = _workflow(queries=[_query("Geralt")])
    service, _ = _service(workflow)
    completed, _ = await service.ensure(workflow.id)
    assert completed.visual_query_bindings

    def fail(*args, **kwargs):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(service, "_build_outputs", fail)
    failed, _ = await service.ensure(workflow.id, force=True)
    assert failed.status == "failed"
    assert failed.error_message == "unexpected narration alignment failure"
    assert failed.block_alignments == []
    assert failed.visual_query_bindings == []
    assert workflow.status == "audio_concluido"
