from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routers.workflow import criar_workflow, obter_pacote_producao
from app.schemas.workflow import WorkflowCreateIn


def _visual_query() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "order": 1,
        "intent": "gameplay_event",
        "query": "Geralt aprende Aard",
        "narration_excerpt": "o primeiro sinal",
        "importance": "high",
        "source_note_id": str(uuid.uuid4()),
        "preferred_session_id": str(uuid.uuid4()),
        "preferred_start_seconds": 12.5,
        "preferred_end_seconds": 20.0,
        "desired_duration_seconds": 7.5,
        "notes": "gameplay time, not narration time",
    }


@pytest.mark.asyncio
async def test_workflow_snapshot_preserves_visual_queries_unchanged() -> None:
    query = _visual_query()
    script_id = uuid.uuid4()
    idea_id = uuid.uuid4()
    structure = {
        "titulo": "Witcher",
        "duracao_total_segundos": 60,
        "blocos": [{"tipo": "intro", "conteudo": "Texto", "visual_queries": [query]}],
    }
    workflow = SimpleNamespace(
        id=uuid.uuid4(),
        ideia_id=idea_id,
        roteiro_id=script_id,
        perfil_producao="analysis",
        status="agendada",
        audio_final_path=None,
        duracao_audio_segundos=None,
        audio_phase="waiting",
        audio_final_sha256=None,
        audio_final_sample_rate=None,
        audio_final_channels=None,
        audio_final_byte_size=None,
        mastering_config=None,
        mastering_metrics=None,
        erro_mensagem=None,
        tentativas=0,
        criado_em="2026-01-01T00:00:00Z",
        atualizado_em="2026-01-01T00:00:00Z",
    )
    repository = MagicMock()
    repository.criar = AsyncMock(return_value=workflow)
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value={
            "id": str(script_id),
            "ideia_id": str(idea_id),
            "status": "aprovado",
            "estrutura": structure,
        }
    )
    hub.obter_ideia = AsyncMock(return_value={"perfil_producao": "analysis"})
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.gerar_audio_workflow") as task,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())
    assert repository.criar.await_args.kwargs["roteiro_snapshot"] == structure
    assert (
        repository.criar.await_args.kwargs["roteiro_snapshot"]["blocos"][0]["visual_queries"][0]
        == query
    )
    task.delay.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_queries", "with_alignment"), [(True, True), (True, False), (False, False)]
)
async def test_production_package_contains_audio_identity_and_backward_compatible_queries(
    with_queries: bool,
    with_alignment: bool,
) -> None:
    workflow_id = uuid.uuid4()
    query = _visual_query()
    script_block = {"tipo": "intro", "conteudo": "Texto canônico"}
    if with_queries:
        script_block["visual_queries"] = [query]
    block = SimpleNamespace(
        id=uuid.uuid4(),
        ordem=0,
        tipo="intro",
        audio_path="/private/raw.wav",
        duracao_segundos=1.25,
        audio_sha256="b" * 64,
        audio_sample_rate=24_000,
        audio_channels=1,
        audio_byte_size=100,
        narration_start_seconds=0.0,
        narration_end_seconds=1.25,
        voice_profile="bernardo_v1",
        generation_profile="narration_v1",
        resolved_seed=123,
        generation_attempt=0,
    )
    workflow = SimpleNamespace(
        id=workflow_id,
        ideia_id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        perfil_producao="analysis",
        status="audio_concluido",
        roteiro_snapshot={
            "titulo": "Title",
            "duracao_total_segundos": 60,
            "blocos": [script_block],
        },
        blocos=[block],
        audio_final_path="/private/final.wav",
        duracao_audio_segundos=1.25,
        audio_final_sha256="f" * 64,
        audio_final_sample_rate=48_000,
        audio_final_channels=1,
        audio_final_byte_size=200,
        mastering_config={"version": "transparent_gain_v1"},
        mastering_metrics={"status": "pass"},
        criado_em="2026-01-01T00:00:00Z",
        atualizado_em="2026-01-01T00:00:01Z",
        tentativas=0,
    )
    transcription = SimpleNamespace(
        texto_completo="texto",
        palavras_timestamps=[{"palavra": "texto", "inicio": 0.0, "fim": 0.5}],
        audio_sha256="f" * 64,
        provider="fake",
        model="fake-v1",
        language="pt",
    )
    repository = MagicMock()
    repository.obter_por_id = AsyncMock(return_value=workflow)
    repository.obter_transcricao = AsyncMock(return_value=transcription)
    alignment = None
    if with_alignment:
        alignment = SimpleNamespace(
            id=uuid.uuid4(),
            transcription_id=uuid.uuid4(),
            strategy_version="narration_alignment_v1",
            fingerprint="d" * 64,
            status="completed",
            block_count=1,
            resolved_visual_queries=1,
            unresolved_visual_queries=0,
            ambiguous_visual_queries=0,
            not_applicable_visual_queries=0,
            metrics={"average_block_coverage": 1.0},
            error_message=None,
            started_at=None,
            completed_at=None,
            criado_em="2026-01-01T00:00:00Z",
            block_alignments=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    audio_bloco_id=block.id,
                    block_order=0,
                    status="aligned",
                    narration_start_seconds=0.0,
                    narration_end_seconds=1.25,
                    canonical_token_count=2,
                    synthesis_token_count=2,
                    asr_word_count=2,
                    canonical_synthesis_coverage=1.0,
                    synthesis_asr_coverage=1.0,
                    overall_coverage=1.0,
                    diagnostics=None,
                )
            ],
            visual_query_bindings=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    visual_query_id=uuid.UUID(query["id"]),
                    audio_bloco_id=block.id,
                    block_order=0,
                    query_order=1,
                    status="resolved",
                    narration_start_seconds=0.2,
                    narration_end_seconds=0.8,
                    excerpt_match_method="literal",
                    excerpt_match_score=1.0,
                    speech_alignment_coverage=1.0,
                    binding_confidence=1.0,
                    canonical_start_char=0,
                    canonical_end_char=10,
                    reason=None,
                )
            ],
        )
    alignment_service = MagicMock()
    alignment_service.current = AsyncMock(return_value=alignment)
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch(
            "app.api.routers.workflow.NarrationAlignmentService",
            return_value=alignment_service,
        ),
    ):
        package = await obter_pacote_producao(workflow_id, MagicMock())
    assert package.audio.sample_rate == 48_000
    assert package.audio.sha256 == "f" * 64
    assert package.transcricao is not None
    assert package.transcricao.audio_sha256 == package.audio.sha256
    queries = package.roteiro.blocos[0].visual_queries
    assert len(queries) == int(with_queries)
    if queries:
        assert queries[0].preferred_start_seconds == 12.5
        assert queries[0].narration_excerpt == "o primeiro sinal"
    if with_alignment:
        assert package.narration_alignment is not None
        binding = package.narration_alignment.visual_query_bindings[0]
        assert binding.narration_start_seconds == 0.2
        assert queries[0].preferred_start_seconds == 12.5
    else:
        assert package.narration_alignment is None
    serialized = package.model_dump(mode="json")
    assert "/private/final.wav" not in str(serialized)
    assert "/private/raw.wav" not in str(serialized)
