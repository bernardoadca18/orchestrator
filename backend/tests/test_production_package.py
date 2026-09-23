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
        "media_context": {
            "media_project_id": str(uuid.uuid4()),
            "media_session_ids": [query["preferred_session_id"]],
        },
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
    hub.obter_production_readiness = AsyncMock(
        return_value={
            "status": "ready",
            "profile": "analysis",
            "capabilities": {
                "delivery_mode": "rendered_video",
                "visual_mode": "semantic_match",
                "requires_media_scope": True,
                "caption_policy": "burned_in",
                "render_profile": None,
                "version": "production_capabilities_v1",
            },
            "editorial_status": "aprovado",
            "visual": {
                "status": "ready",
                "policy_version": "visual_requirements_v2",
                "policy_identity": "a" * 64,
                "speakable_content_identity": "b" * 64,
                "block_count": 1,
                "visual_beat_count": 1,
                "textual_coverage_ratio": 1.0,
                "missing_ranges": [],
                "overlap_ranges": [],
                "issues": [],
            },
            "media_scope": {"required": True, "status": "ready"},
            "issues": [],
        }
    )
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.processar_producao_workflow") as task,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())
    assert repository.criar.await_args.kwargs["roteiro_snapshot"] == structure
    assert (
        repository.criar.await_args.kwargs["roteiro_snapshot"]["blocos"][0]["visual_queries"][0]
        == query
    )
    task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_unready_script_is_rejected_before_workflow_or_audio_task() -> None:
    script_id = uuid.uuid4()
    idea_id = uuid.uuid4()
    repository = MagicMock()
    repository.criar = AsyncMock()
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value={
            "id": str(script_id),
            "ideia_id": str(idea_id),
            "status": "aprovado",
            "estrutura": {
                "titulo": "Legacy",
                "duracao_total_segundos": 60,
                "blocos": [{"tipo": "intro", "conteudo": "Texto", "visual_queries": []}],
            },
        }
    )
    hub.obter_production_readiness = AsyncMock(
        return_value={
            "status": "not_ready",
            "profile": "faceless_pop_culture_lists",
            "editorial_status": "aprovado",
            "visual": {
                "status": "not_ready",
                "policy_version": "visual_requirements_v2",
                "policy_identity": "a" * 64,
                "speakable_content_identity": "b" * 64,
                "block_count": 1,
                "visual_beat_count": 0,
                "textual_coverage_ratio": 0.0,
                "missing_ranges": [{"block_order": 0, "start_char": 0, "end_char": 5}],
                "overlap_ranges": [],
                "issues": [{"block_order": 0, "code": "visual_queries_missing"}],
            },
            "media_scope": {"required": True, "status": "missing"},
            "issues": [
                {"code": "visual_readiness_not_ready"},
                {"code": "media_scope_missing"},
            ],
        }
    )
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.processar_producao_workflow") as production_task,
        patch("app.api.routers.workflow.gerar_audio_workflow") as audio_task,
        patch("app.services.audio_service.VoiceClient") as voice_client,
        pytest.raises(Exception) as caught,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())
    assert getattr(caught.value, "status_code", None) == 409
    assert caught.value.detail["category"] == "production_script_not_ready"
    repository.criar.assert_not_awaited()
    production_task.delay.assert_not_called()
    audio_task.delay.assert_not_called()
    voice_client.assert_not_called()


@pytest.mark.asyncio
async def test_missing_required_media_scope_blocks_before_workflow_and_all_tasks() -> None:
    script_id = uuid.uuid4()
    idea_id = uuid.uuid4()
    repository = MagicMock()
    repository.criar = AsyncMock()
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value={
            "id": str(script_id),
            "ideia_id": str(idea_id),
            "status": "aprovado",
            "estrutura": {"titulo": "Ready visual", "blocos": []},
        }
    )
    hub.obter_production_readiness = AsyncMock(
        return_value={
            "status": "not_ready",
            "profile": "game_analysis",
            "editorial_status": "aprovado",
            "visual": {"status": "ready"},
            "media_scope": {"required": True, "status": "missing"},
            "issues": [{"code": "media_scope_missing"}],
        }
    )
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.processar_producao_workflow") as production_task,
        patch("app.api.routers.workflow.gerar_audio_workflow") as audio_task,
        patch("app.services.audio_service.VoiceClient") as voice_client,
        pytest.raises(Exception) as caught,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())
    assert getattr(caught.value, "status_code", None) == 409
    assert caught.value.detail["category"] == "production_script_not_ready"
    assert caught.value.detail["production_readiness"]["issues"] == [
        {"code": "media_scope_missing"}
    ]
    repository.criar.assert_not_awaited()
    production_task.delay.assert_not_called()
    audio_task.delay.assert_not_called()
    voice_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_queries", "with_alignment", "with_visual_plan", "with_timeline"),
    [
        (True, True, True, True),
        (True, True, False, False),
        (True, False, False, False),
        (False, False, False, False),
    ],
)
async def test_production_package_contains_audio_identity_and_backward_compatible_queries(
    with_queries: bool,
    with_alignment: bool,
    with_visual_plan: bool,
    with_timeline: bool,
) -> None:
    workflow_id = uuid.uuid4()
    query = _visual_query()
    media_project_id = uuid.uuid4()
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
            "media_context": {
                "media_project_id": str(media_project_id),
                "media_session_ids": [query["preferred_session_id"]],
            },
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
    visual_plan = None
    if with_visual_plan:
        source = {
            "format_version": "visual_source_v1",
            "source_type": "gameplay",
            "project_id": str(uuid.uuid4()),
            "session_id": query["preferred_session_id"],
            "media_id": str(uuid.uuid4()),
            "source_start_seconds": 12.5,
            "source_end_seconds": 20.0,
            "source_duration_seconds": 7.5,
            "semantic_score": None,
            "selection_score": 1.0,
            "rank": None,
            "session_label": "001",
            "media_filename": "001.mp4",
            "matched_note_ids": [],
            "provenance": {"source": "production_script"},
        }
        visual_plan = SimpleNamespace(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow_id,
            narration_alignment_id=alignment.id,
            strategy_version="visual_director_v1",
            request_fingerprint="e" * 64,
            fingerprint="f" * 64,
            status="completed",
            media_scope=None,
            media_index_provenance={"temporal_index_ids": []},
            total_queries=1,
            resolved_queries=1,
            unresolved_queries=0,
            failed_queries=0,
            required_gaps=0,
            optional_gaps=0,
            direct_provenance_count=1,
            media_search_count=0,
            reused_source_count=0,
            metrics={"processing_seconds": 0.001},
            diagnostics=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            criado_em="2026-01-01T00:00:02Z",
            decisions=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    visual_query_id=uuid.UUID(query["id"]),
                    audio_bloco_id=block.id,
                    block_order=0,
                    query_order=1,
                    query=query["query"],
                    intent=query["intent"],
                    importance=query["importance"],
                    source_note_id=uuid.UUID(query["source_note_id"]),
                    desired_duration_seconds=7.5,
                    narration_binding_status="resolved",
                    narration_start_seconds=0.2,
                    narration_end_seconds=0.8,
                    target_narration_duration_seconds=0.6,
                    source_resolution_status="resolved",
                    selection_strategy="preferred_provenance",
                    selected_source=source,
                    alternatives=[],
                    selection_score=1.0,
                    score_components={"trusted_preferred_provenance": 1.0},
                    reason=None,
                    diagnostics=None,
                )
            ],
        )
    visual_director_service = MagicMock()
    visual_director_service.current = AsyncMock(return_value=visual_plan)
    production_timeline = None
    if with_timeline:
        production_timeline = SimpleNamespace(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow_id,
            narration_alignment_id=alignment.id,
            visual_plan_id=visual_plan.id,
            final_audio_sha256=workflow.audio_final_sha256,
            narration_sample_rate=48_000,
            narration_channels=1,
            narration_byte_size=200,
            schema_version="production_timeline_schema_v1",
            strategy_version="production_timeline_v1",
            validator_version="production_timeline_validator_v1",
            policy={"default_transition": "cut"},
            request_fingerprint="3" * 64,
            fingerprint="4" * 64,
            status="completed_with_gaps",
            duration_seconds=1.25,
            visual_coverage_seconds=0.6,
            visual_gap_seconds=0.65,
            visual_coverage_ratio=0.48,
            placement_count=1,
            gap_count=2,
            cut_count=0,
            unplaced_query_count=0,
            required_gap_seconds=0,
            preferred_gap_seconds=0,
            optional_gap_seconds=0,
            background_gap_seconds=0.65,
            average_shot_duration_seconds=0.6,
            metrics={"planning_time_ms": 0.5},
            diagnostics={"query_placement_outcomes": []},
            error_message=None,
            started_at=None,
            completed_at=None,
            criado_em="2026-01-01T00:00:03Z",
            placements=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    order=0,
                    visual_decision_id=visual_plan.decisions[0].id,
                    visual_decision_ids=[str(visual_plan.decisions[0].id)],
                    visual_query_ids=[query["id"]],
                    source_project_id=uuid.uuid4(),
                    source_session_id=uuid.UUID(query["preferred_session_id"]),
                    source_media_id=uuid.uuid4(),
                    timeline_start_seconds=0.2,
                    timeline_end_seconds=0.8,
                    candidate_start_seconds=12.5,
                    candidate_end_seconds=20.0,
                    source_start_seconds=12.5,
                    source_end_seconds=13.1,
                    playback_rate=1.0,
                    transition="cut",
                    strategy="preferred_start_trim",
                    importance="required",
                    source_provenance={"source": "production_script"},
                    diagnostics=None,
                )
            ],
            gaps=[
                SimpleNamespace(
                    id=uuid.uuid4(),
                    order=0,
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=0.2,
                    reason="no_visual_query",
                    severity="background",
                    related_visual_query_ids=[],
                    diagnostics=None,
                ),
                SimpleNamespace(
                    id=uuid.uuid4(),
                    order=1,
                    timeline_start_seconds=0.8,
                    timeline_end_seconds=1.25,
                    reason="no_visual_query",
                    severity="background",
                    related_visual_query_ids=[],
                    diagnostics=None,
                ),
            ],
        )
    production_timeline_service = MagicMock()
    production_timeline_service.current = AsyncMock(return_value=production_timeline)
    render = (
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow_id,
            production_timeline_id=production_timeline.id,
            production_timeline_fingerprint=production_timeline.fingerprint,
            composer_render_id=str(uuid.uuid4()),
            render_profile="youtube_1080p30_v1",
            mode="final",
            status="completed",
            phase="completed",
            progress=1.0,
            frames_rendered=38,
            total_frames=38,
            render_profile_identity="1" * 64,
            render_fingerprint="2" * 64,
            manifest_fingerprint="3" * 64,
            output_sha256="4" * 64,
            output_byte_size=100,
            output_duration_seconds=1.25,
            output_width=1920,
            output_height=1080,
            output_fps=30,
            output_frame_count=38,
            output_video_codec="h264",
            output_audio_codec="aac",
            output_audio_sample_rate=48000,
            metrics={},
            error_category=None,
            error_message=None,
            started_at=workflow.criado_em,
            completed_at=workflow.atualizado_em,
            created_at=workflow.criado_em,
            updated_at=workflow.atualizado_em,
        )
        if with_timeline
        else None
    )
    production_orchestration_service = MagicMock()
    production_orchestration_service.current_render = AsyncMock(return_value=render)
    qa_report = (
        SimpleNamespace(
            id=uuid.uuid4(),
            workflow_execucao_id=workflow_id,
            workflow_render_id=render.id,
            production_timeline_id=production_timeline.id,
            visual_plan_id=visual_plan.id,
            narration_alignment_id=alignment.id,
            final_audio_sha256="f" * 64,
            render_output_sha256="4" * 64,
            qa_mode="standard",
            policy_name="game_analysis_qa_v1",
            policy_version="1",
            policy_identity="5" * 64,
            strategy_version="production_qa_v1",
            request_fingerprint="6" * 64,
            status="passed",
            publication_ready=True,
            check_count=1,
            passed_count=1,
            warning_count=0,
            failed_count=0,
            blocked_count=0,
            checks=[],
            metrics={},
            error_category=None,
            error_message=None,
            created_at=workflow.criado_em,
            started_at=workflow.criado_em,
            completed_at=workflow.atualizado_em,
            updated_at=workflow.atualizado_em,
        )
        if with_timeline and with_visual_plan and with_alignment
        else None
    )
    production_qa_service = MagicMock()
    production_qa_service.current = AsyncMock(return_value=qa_report)
    with (
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch(
            "app.api.routers.workflow.NarrationAlignmentService",
            return_value=alignment_service,
        ),
        patch(
            "app.api.routers.workflow.VisualDirectorService",
            return_value=visual_director_service,
        ),
        patch(
            "app.api.routers.workflow.ProductionTimelineService",
            return_value=production_timeline_service,
        ),
        patch(
            "app.api.routers.workflow.ProductionOrchestrationService",
            return_value=production_orchestration_service,
        ),
        patch(
            "app.api.routers.workflow.ProductionQAService",
            return_value=production_qa_service,
        ),
    ):
        package = await obter_pacote_producao(workflow_id, MagicMock())
    assert package.audio.sample_rate == 48_000
    assert package.audio.sha256 == "f" * 64
    assert package.transcricao is not None
    assert package.transcricao.audio_sha256 == package.audio.sha256
    queries = package.roteiro.blocos[0].visual_queries
    assert package.roteiro.media_context is not None
    assert package.roteiro.media_context.media_project_id == media_project_id
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
    if with_visual_plan:
        assert package.visual_plan is not None
        decision = package.visual_plan.decisions[0]
        assert decision.narration_start_seconds == 0.2
        assert decision.selected_source.source_start_seconds == 12.5
        assert queries[0].preferred_start_seconds == 12.5
    else:
        assert package.visual_plan is None
    if qa_report:
        assert package.qa_report is not None
        assert package.qa_report.publication_ready is True
    else:
        assert package.qa_report is None
    if with_timeline:
        assert package.production_timeline is not None
        assert package.render is not None
        assert package.render.video_url == f"/workflow/{workflow_id}/video"
        placement = package.production_timeline.tracks.primary_visual.placements[0]
        assert placement.timeline_start_seconds == 0.2
        assert placement.source.source_start_seconds == 12.5
    else:
        assert package.production_timeline is None
        assert package.render is None
    serialized = package.model_dump(mode="json")
    assert "/private/final.wav" not in str(serialized)
    assert "/private/raw.wav" not in str(serialized)
