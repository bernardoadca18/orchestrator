from __future__ import annotations

import copy
import datetime as dt
import uuid
from itertools import pairwise

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.workflow import (
    AudioBloco,
    NarrationAlignment,
    TranscricaoAudio,
    VisualQueryNarrationBinding,
    WorkflowExecucao,
)
from app.services.content_coverage import ContentCoverageLimitError
from app.services.narration_alignment_service import NarrationAlignmentService


class FakeWorkflowRepository:
    def __init__(self, workflow: WorkflowExecucao) -> None:
        self.workflow = workflow

    async def obter_por_id(self, workflow_id: uuid.UUID, **kwargs):
        return self.workflow if workflow_id == self.workflow.id else None

    async def atualizar_status(self, workflow_id: uuid.UUID, status: str, **values):
        assert workflow_id == self.workflow.id
        self.workflow.status = status
        for key, value in values.items():
            setattr(self.workflow, key, value)


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
        audio_phase="quality_unvalidated",
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


def _long_workflow(
    token_counts: tuple[int, ...], duration_seconds: tuple[float, ...]
) -> WorkflowExecucao:
    workflow = _workflow()
    workflow.roteiro_snapshot["blocos"] = []
    workflow.blocos = []
    all_words: list[str] = []
    timed_words: list[dict[str, object]] = []
    cursor = 0.0
    for order, (token_count, duration) in enumerate(
        zip(token_counts, duration_seconds, strict=True)
    ):
        words = [f"bloco{order}palavra{index}" for index in range(token_count)]
        content = " ".join(words)
        workflow.roteiro_snapshot["blocos"].append(
            {
                "ordem": order,
                "tipo": "desenvolvimento",
                "conteudo": content,
                "visual_queries": [],
            }
        )
        workflow.blocos.append(
            AudioBloco(
                id=uuid.uuid4(),
                workflow_execucao_id=workflow.id,
                ordem=order,
                tipo="desenvolvimento",
                texto=content,
                synthesis_text=content,
                source_text_sha256=f"{order + 1}" * 64,
                synthesis_text_sha256=f"{order + 2}" * 64,
                synthesis_identity=f"{order + 3}" * 64,
                narration_start_seconds=cursor,
                narration_end_seconds=cursor + duration,
                status="concluido",
            )
        )
        word_duration = duration / token_count
        timed_words.extend(
            {
                "palavra": word,
                "inicio": cursor + index * word_duration,
                "fim": cursor + (index + 0.8) * word_duration,
            }
            for index, word in enumerate(words)
        )
        all_words.extend(words)
        cursor += duration
    workflow.roteiro_snapshot["duracao_total_segundos"] = round(cursor)
    workflow.duracao_audio_segundos = cursor
    assert workflow.transcricao is not None
    workflow.transcricao.texto_completo = " ".join(all_words)
    workflow.transcricao.palavras_timestamps = timed_words
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


def test_known_coverage_limit_has_a_safe_operator_diagnostic() -> None:
    assert NarrationAlignmentService._safe_error(
        ContentCoverageLimitError("internal details")
    ) == "narration content coverage exceeded a bounded evaluation window"


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
    assert workflow.audio_phase == "completed"
    assert workflow.roteiro_snapshot == snapshot_before
    assert query["preferred_start_seconds"] == 1200.0
    assert binding.narration_start_seconds != query["preferred_start_seconds"]


@pytest.mark.asyncio
async def test_long_block_visual_queries_bind_to_distributed_narration_spans() -> None:
    excerpts = [
        "Alpha um",
        "Beta dois",
        "Gama três",
        "Delta quatro",
        "Omega cinco",
    ]
    workflow = _workflow(
        queries=[_query(excerpt, order=index) for index, excerpt in enumerate(excerpts)]
    )
    content = "Alpha um. Beta dois. Gama três. Delta quatro. Omega cinco."
    workflow.roteiro_snapshot["duracao_total_segundos"] = 125
    workflow.roteiro_snapshot["blocos"][0]["conteudo"] = content
    workflow.duracao_audio_segundos = 125
    block = workflow.blocos[0]
    block.texto = content
    block.synthesis_text = content
    block.narration_end_seconds = 125
    assert workflow.transcricao is not None
    words = ["Alpha", "um", "Beta", "dois", "Gama", "três", "Delta", "quatro", "Omega", "cinco"]
    workflow.transcricao.texto_completo = " ".join(words)
    workflow.transcricao.palavras_timestamps = [
        {"palavra": word, "inicio": index * 10.0, "fim": index * 10.0 + 2.0}
        for index, word in enumerate(words)
    ]

    service, _ = _service(workflow)
    alignment, _ = await service.ensure(workflow.id)

    assert alignment.status == "completed"
    bindings = alignment.visual_query_bindings
    assert [item.status for item in bindings] == ["resolved"] * 5
    starts = [item.narration_start_seconds for item in bindings]
    assert starts == sorted(starts)
    assert len(set(starts)) == 5


@pytest.mark.asyncio
async def test_contiguous_host_owned_beats_bind_full_monotonic_audio_spans() -> None:
    content = "Alpha um dois três. Beta quatro cinco seis. Gama sete oito nove."
    cuts = [0, content.index("Beta"), content.index("Gama"), len(content)]
    queries = []
    for order, (start, end) in enumerate(pairwise(cuts)):
        query = _query(content[start:end], order=order)
        query.update(
            {
                "visual_beat_id": str(uuid.uuid4()),
                "coverage_start_char": start,
                "coverage_end_char": end,
                "coverage_policy_version": "visual_requirements_v2",
            }
        )
        queries.append(query)
    workflow = _workflow(queries=queries)
    workflow.roteiro_snapshot["blocos"][0]["conteudo"] = content
    workflow.roteiro_snapshot["duracao_total_segundos"] = 12
    workflow.duracao_audio_segundos = 12
    block = workflow.blocos[0]
    block.texto = content
    block.synthesis_text = content
    block.narration_end_seconds = 12
    words = content.replace(".", "").split()
    assert workflow.transcricao is not None
    workflow.transcricao.texto_completo = " ".join(words)
    workflow.transcricao.palavras_timestamps = [
        {"palavra": word, "inicio": index * 0.9, "fim": index * 0.9 + 0.75}
        for index, word in enumerate(words)
    ]

    service, _ = _service(workflow)
    alignment, _ = await service.ensure(workflow.id)

    bindings = alignment.visual_query_bindings
    assert [item.status for item in bindings] == ["resolved"] * 3
    assert [item.excerpt_match_method for item in bindings] == ["host_owned_span"] * 3
    assert [item.canonical_start_char for item in bindings] == cuts[:-1]
    assert [item.canonical_end_char for item in bindings] == cuts[1:]
    assert all(
        left.narration_end_seconds == right.narration_start_seconds
        for left, right in pairwise(bindings)
    )
    assert bindings[0].narration_start_seconds == 0.0
    assert bindings[-1].narration_end_seconds == 12.0
    assert all(
        item.diagnostics["timeline_projection"] == "host_owned_contiguous_v1"
        for item in bindings
    )

    workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0][
        "coverage_policy_version"
    ] = "visual_requirements_v3"
    changed_policy, _ = await service.ensure(workflow.id)
    assert changed_policy.id != alignment.id


def test_host_owned_beats_share_the_real_inter_block_pause() -> None:
    workflow_id = uuid.uuid4()
    blocks = [
        AudioBloco(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow_id,
            ordem=order,
            tipo="narracao",
            texto="texto",
            narration_start_seconds=start,
            narration_end_seconds=end,
        )
        for order, (start, end) in enumerate(((0.0, 10.0), (10.5, 20.0)))
    ]
    bindings = [
        VisualQueryNarrationBinding(
            narration_alignment_id=uuid.UUID(int=0),
            audio_bloco_id=block.id,
            visual_query_id=uuid.uuid4(),
            block_order=block.ordem,
            query_order=0,
            status="resolved",
            narration_start_seconds=block.narration_start_seconds,
            narration_end_seconds=block.narration_end_seconds,
            excerpt_match_method="host_owned_span",
            diagnostics={"timeline_projection": "host_owned_contiguous_v1"},
        )
        for block in blocks
    ]

    NarrationAlignmentService._project_host_owned_block_boundaries(
        blocks=blocks,
        bindings=bindings,
    )

    assert bindings[0].narration_end_seconds == 10.25
    assert bindings[1].narration_start_seconds == 10.25
    assert all(
        item.diagnostics["inter_block_pause_projection"] == "midpoint_v1"
        for item in bindings
    )


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
        strategy_version="narration_alignment_v4",
    )
    versioned.workflows = service.workflows  # type: ignore[assignment]
    versioned.alignments = repository  # type: ignore[assignment]
    changed_strategy, _ = await versioned.ensure(workflow.id)
    assert changed_strategy.id != changed_excerpt.id


@pytest.mark.asyncio
async def test_bounded_policy_identity_creates_new_derived_alignment_only() -> None:
    workflow = _workflow()
    original_sha = workflow.audio_final_sha256
    transcription_id = workflow.transcricao.id if workflow.transcricao else None
    repository = FakeAlignmentRepository()
    legacy = NarrationAlignmentService(
        object(),  # type: ignore[arg-type]
        config=Settings(
            _env_file=None,
            narration_alignment_strategy_version="narration_alignment_v1",
            narration_quality_policy_version="narration_speech_quality_v1",
        ),
    )
    legacy.workflows = FakeWorkflowRepository(workflow)  # type: ignore[assignment]
    legacy.alignments = repository  # type: ignore[assignment]
    old_alignment, _ = await legacy.ensure(workflow.id)

    current = NarrationAlignmentService(object(), config=Settings(_env_file=None))  # type: ignore[arg-type]
    current.workflows = legacy.workflows  # type: ignore[assignment]
    current.alignments = repository  # type: ignore[assignment]
    new_alignment, _ = await current.ensure(workflow.id)

    assert new_alignment.id != old_alignment.id
    assert new_alignment.fingerprint != old_alignment.fingerprint
    assert old_alignment.status == "completed"
    assert workflow.audio_final_sha256 == original_sha
    assert (workflow.transcricao.id if workflow.transcricao else None) == transcription_id


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


@pytest.mark.asyncio
async def test_three_real_duration_blocks_complete_with_block_segmented_coverage() -> None:
    workflow = _long_workflow((490, 560, 629), (203.94, 238.78, 269.46))
    service, _ = _service(workflow)

    alignment, _ = await service.ensure(workflow.id)

    assert alignment.status == "completed"
    assert alignment.block_count == 3
    coverage = alignment.metrics["speech_quality"]["metrics"]["content_coverage"]
    assert coverage["classification"] == "content_complete"
    assert coverage["metrics"]["content_coverage_strategy"] == "block_segmented_v1"
    assert coverage["metrics"]["segment_count"] == 3
    assert coverage["metrics"]["maximum_observed_window_cells"] <= 262_144


@pytest.mark.asyncio
async def test_single_block_above_token_limit_uses_internal_bounded_windows() -> None:
    workflow = _long_workflow((2_600,), (900.0,))
    service, _ = _service(workflow)

    alignment, _ = await service.ensure(workflow.id)

    assert alignment.status == "completed"
    assert alignment.block_alignments[0].synthesis_token_count == 2_600
    coverage = alignment.metrics["speech_quality"]["metrics"]["content_coverage"]
    assert coverage["classification"] == "content_complete"
    assert coverage["metrics"]["segments"][0]["window_count"] > 1
    assert coverage["metrics"]["segments"][0]["maximum_window_cells"] <= 2_000_000
    assert coverage["metrics"]["segments"][0]["used_proportional_fallback"] is False


@pytest.mark.asyncio
async def test_inconclusive_content_does_not_claim_audio_quality_validation() -> None:
    workflow = _long_workflow((600,), (240.0,))
    assert workflow.transcricao is not None
    for index, word in enumerate(workflow.transcricao.palavras_timestamps):
        word["palavra"] = f"divergente{index}"
    workflow.transcricao.texto_completo = " ".join(
        str(word["palavra"]) for word in workflow.transcricao.palavras_timestamps
    )
    service, _ = _service(workflow)

    alignment, _ = await service.ensure(workflow.id)

    coverage = alignment.metrics["speech_quality"]["metrics"]["content_coverage"]
    assert alignment.status == "completed"
    assert coverage["classification"] == "content_inconclusive"
    assert workflow.audio_phase == "quality_unvalidated"
