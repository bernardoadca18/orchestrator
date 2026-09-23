from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.clients.video_composer_client import ComposerRenderProfile
from app.services.production_qa_checks import (
    ProductionQACheck,
    ProductionQAInputs,
    aggregate_checks,
    check_hybrid_source_contracts,
    deep_ffprobe_check,
    delivery_checks,
    standard_local_checks,
)
from app.services.production_qa_policy import (
    GAME_ANALYSIS_HYBRID_POLICY,
    GAME_ANALYSIS_POLICY,
    GENERIC_HYBRID_POLICY,
    GENERIC_POLICY,
    resolve_qa_policy,
)


def _inputs() -> ProductionQAInputs:
    query_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    alignment_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    timeline_id = uuid.uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        production_status="completed",
        audio_final_sha256="a" * 64,
        audio_final_byte_size=1000,
        duracao_audio_segundos=10.0,
        audio_final_sample_rate=48_000,
        audio_final_channels=1,
        mastering_config={
            "target_lufs": -16.0,
            "max_true_peak_dbtp": -1.0,
            "true_peak_tolerance_db": 0.02,
            "min_acceptable_lufs": -18.0,
            "max_acceptable_lufs": -15.0,
        },
        mastering_metrics={
            "after": {
                "integrated_lufs": -16.0,
                "sample_peak_dbfs": -2.0,
                "true_peak_dbtp": -1.1,
            },
            "peak_constrained": False,
            "status": "pass",
        },
        roteiro_snapshot={
            "titulo": "Análise genérica",
            "blocos": [
                {
                    "ordem": 0,
                    "visual_queries": [
                        {
                            "id": str(query_id),
                            "importance": "required",
                            "narration_excerpt": "momento importante",
                        }
                    ],
                }
            ],
        },
    )
    transcription = SimpleNamespace(audio_sha256="a" * 64)
    binding = SimpleNamespace(visual_query_id=query_id, status="resolved")
    block = SimpleNamespace(overall_coverage=0.95)
    alignment = SimpleNamespace(
        id=alignment_id,
        final_audio_sha256="a" * 64,
        fingerprint="b" * 64,
        status="completed",
        visual_query_bindings=[binding],
        block_alignments=[block],
    )
    decision = SimpleNamespace(
        visual_query_id=query_id,
        importance="required",
        source_resolution_status="resolved",
        selected_source={"media_id": str(uuid.uuid4())},
    )
    plan = SimpleNamespace(
        id=plan_id,
        narration_alignment_id=alignment_id,
        fingerprint="c" * 64,
        status="completed",
        decisions=[decision],
    )
    timeline = SimpleNamespace(
        id=timeline_id,
        narration_alignment_id=alignment_id,
        visual_plan_id=plan_id,
        fingerprint="d" * 64,
        status="completed",
        duration_seconds=10.0,
        gap_count=0,
        visual_gap_seconds=0.0,
        visual_coverage_ratio=1.0,
        diagnostics={
            "query_placement_outcomes": [
                {"visual_query_id": str(query_id), "status": "fully_placed"}
            ]
        },
        placements=[
            SimpleNamespace(
                source_type="gameplay",
                source_session_id=uuid.UUID(int=1),
                source_media_id=uuid.UUID(int=2),
                candidate_start_seconds=0.0,
                candidate_end_seconds=10.0,
                timeline_start_seconds=0.0,
                timeline_end_seconds=10.0,
                strategy="media_search",
            )
        ],
    )
    profile = ComposerRenderProfile(
        name="youtube_1080p30_v1",
        version="render_profile_v1",
        identity="e" * 64,
        width=1920,
        height=1080,
        fps=30,
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        audio_sample_rate=48_000,
        fit_mode="contain",
        final_gap_policy="reject",
        preview_gap_policy="diagnostic_black",
    )
    render = SimpleNamespace(
        id=uuid.uuid4(),
        production_timeline_id=timeline_id,
        production_timeline_fingerprint="d" * 64,
        render_profile="youtube_1080p30_v1",
        render_profile_identity="e" * 64,
        status="completed",
        output_sha256="f" * 64,
        output_byte_size=500,
        output_duration_seconds=10.0,
        output_width=1920,
        output_height=1080,
        output_fps=30.0,
        output_video_codec="h264",
        output_audio_codec="aac",
        output_audio_sample_rate=48_000,
    )
    return ProductionQAInputs(
        workflow=workflow,
        transcription=transcription,
        alignment=alignment,
        visual_plan=plan,
        timeline=timeline,
        render=render,
        policy=GAME_ANALYSIS_POLICY,
        render_profile=profile,
    )


def _checks(inputs: ProductionQAInputs) -> dict[str, ProductionQACheck]:
    return {item.code: item for item in standard_local_checks(inputs)}


def _hybrid_placements() -> list[SimpleNamespace]:
    project_id = uuid.uuid4()
    return [
        SimpleNamespace(
            order=0,
            source_type="video_segment",
            source_project_id=project_id,
            source_session_id=uuid.uuid4(),
            source_media_id=uuid.uuid4(),
            source_image_semantic_index_id=None,
            candidate_start_seconds=10.0,
            candidate_end_seconds=20.0,
            source_start_seconds=10.0,
            source_end_seconds=15.0,
            timeline_start_seconds=0.0,
            timeline_end_seconds=5.0,
            playback_rate=1.0,
            presentation=None,
            source_provenance={},
            visual_decision_id=None,
            strategy="media_search",
        ),
        SimpleNamespace(
            order=1,
            source_type="static_image",
            source_project_id=project_id,
            source_session_id=None,
            source_media_id=uuid.uuid4(),
            source_image_semantic_index_id=uuid.uuid4(),
            candidate_start_seconds=None,
            candidate_end_seconds=None,
            source_start_seconds=None,
            source_end_seconds=None,
            timeline_start_seconds=5.0,
            timeline_end_seconds=10.0,
            playback_rate=None,
            presentation={
                "policy_version": "static_image_presentation_v1",
                "fit": "contain",
                "background": "blurred_fill",
                "motion": "slow_zoom_in",
                "zoom_amount": 0.04,
            },
            source_provenance={
                "expected_sha256": "9" * 64,
                "expected_byte_size": 321,
                "content_type": "image/png",
            },
            visual_decision_id=None,
            strategy="static_image_hold",
        ),
    ]


def test_healthy_artifact_chain_passes_every_mandatory_local_check() -> None:
    checks = standard_local_checks(_inputs())
    assert all(item.result in {"passed", "skipped"} for item in checks)
    aggregate = aggregate_checks(checks)
    assert aggregate.status == "passed"
    assert aggregate.publication_ready is True


def test_program_audio_checks_are_distinct_from_narration_stem_checks() -> None:
    inputs = _inputs()
    inputs.render.render_profile = "youtube_1080p30_program_audio_v1"
    inputs.render.metrics = {
        "programAudio": {
            "audioMixPlanFingerprint": "9" * 64,
            "musicAssetId": "owned-track-01",
            "musicSha256": "8" * 64,
            "outputChannels": 2,
            "outputSampleRate": 48_000,
            "targetLufs": -14.0,
            "maxTruePeakDbtp": -1.0,
            "targetLra": 11.0,
            "after": {"integratedLufs": -14.1, "truePeakDbtp": -1.1},
            "programMasterProfile": "youtube_program_master_v1_candidate",
        }
    }
    object.__setattr__(
        inputs,
        "render_profile",
        ComposerRenderProfile(
            name="youtube_1080p30_program_audio_v1",
            version="render_profile_program_audio_v1",
            identity="e" * 64,
            width=1920,
            height=1080,
            fps=30,
            container="mp4",
            video_codec="h264",
            audio_codec="aac",
            audio_sample_rate=48_000,
            audio_channels=2,
            fit_mode="contain",
            final_gap_policy="reject",
            preview_gap_policy="diagnostic_black",
        ),
    )
    checks = _checks(inputs)
    for code in (
        "audio_mix_plan_identity",
        "music_asset_identity",
        "program_audio_format",
        "program_audio_loudness",
        "program_audio_true_peak",
        "program_master_identity",
    ):
        assert checks[code].result == "passed"
    assert checks["mastering_loudness"].result == "passed"


def test_stale_timeline_fingerprint_fails_chain_and_render_identity() -> None:
    inputs = _inputs()
    inputs.render.production_timeline_fingerprint = "0" * 64
    checks = _checks(inputs)
    assert checks["artifact_chain_identity"].result == "failed"
    assert checks["render_timeline_identity"].result == "failed"


def test_visual_diversity_check_reports_degenerate_reuse() -> None:
    inputs = _inputs()
    source = inputs.timeline.placements[0]
    inputs.timeline.placements = [
        SimpleNamespace(
            **{
                **vars(source),
                "timeline_start_seconds": float(index),
                "timeline_end_seconds": float(index + 1),
            }
        )
        for index in range(22)
    ]
    check = _checks(inputs)["visual_diversity"]
    assert check.result == "failed"
    assert check.actual["unique_source_window_count"] == 1
    assert check.actual["dominant_window_use_ratio"] == 1.0
    assert check.actual["semantic_coverage"] == 1.0
    assert check.actual["strong_match_count"] == 0
    assert check.actual["weak_match_count"] == 0
    assert check.actual["unscored_match_count"] == 1
    assert check.actual["unresolved_count"] == 0
    assert check.actual["entity_violation_count"] == 0


def test_visual_qa_blocks_entity_mismatch_before_render() -> None:
    inputs = _inputs()
    decision = inputs.visual_plan.decisions[0]
    decision.query = "GTA IV street scene"
    decision.selected_source["session_label"] = "gta_v_gameplay_ep001"
    check = _checks(inputs)["visual_diversity"]
    assert check.result == "failed"
    assert check.actual["entity_violation_count"] == 1


def test_visual_qa_blocks_plan_with_no_selected_sources() -> None:
    inputs = _inputs()
    decision = inputs.visual_plan.decisions[0]
    decision.selected_source = None
    decision.source_resolution_status = "unresolved"
    check = _checks(inputs)["visual_diversity"]
    assert check.result == "failed"
    assert check.actual["semantic_coverage"] == 0.0
    assert check.actual["unresolved_count"] == 1


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("audio_final_sha256", None, "final_audio_identity"),
        ("audio_final_byte_size", 0, "final_audio_identity"),
        ("audio_final_sample_rate", 44_100, "final_audio_format"),
        ("audio_final_channels", 2, "final_audio_format"),
    ],
)
def test_audio_identity_and_format_fail_closed(field: str, value: object, code: str) -> None:
    inputs = _inputs()
    setattr(inputs.workflow, field, value)
    assert _checks(inputs)[code].result == "failed"


def test_mastering_true_peak_violation_fails() -> None:
    inputs = _inputs()
    inputs.workflow.mastering_metrics["after"]["true_peak_dbtp"] = -0.8
    assert _checks(inputs)["mastering_true_peak"].result == "failed"


def test_peak_constrained_quiet_loudness_is_warning_and_still_releasable() -> None:
    inputs = _inputs()
    inputs.workflow.mastering_metrics["after"]["integrated_lufs"] = -19.0
    inputs.workflow.mastering_metrics["peak_constrained"] = True
    checks = standard_local_checks(inputs)
    assert _checks(inputs)["mastering_loudness"].result == "warning"
    aggregate = aggregate_checks(checks)
    assert aggregate.status == "passed_with_warnings"
    assert aggregate.publication_ready is True


def test_transcription_missing_blocks_and_stale_transcription_fails() -> None:
    inputs = _inputs()
    object.__setattr__(inputs, "transcription", None)
    assert _checks(inputs)["transcription_identity"].result == "blocked"
    inputs = _inputs()
    inputs.transcription.audio_sha256 = "0" * 64
    assert _checks(inputs)["transcription_identity"].result == "failed"


@pytest.mark.parametrize("status", ["unresolved", "ambiguous"])
def test_required_narration_binding_must_be_resolved(status: str) -> None:
    inputs = _inputs()
    inputs.alignment.visual_query_bindings[0].status = status
    assert _checks(inputs)["required_narration_bindings"].result == "failed"


def test_not_applicable_binding_is_valid_when_query_has_no_excerpt() -> None:
    inputs = _inputs()
    query = inputs.workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]
    query["narration_excerpt"] = None
    inputs.alignment.visual_query_bindings[0].status = "not_applicable"
    assert _checks(inputs)["required_narration_bindings"].result == "passed"


def test_low_alignment_coverage_warns_without_failing_required_binding() -> None:
    inputs = _inputs()
    inputs.alignment.block_alignments[0].overall_coverage = 0.59
    assert _checks(inputs)["narration_alignment_coverage"].result == "warning"


def test_required_visual_source_and_placement_fail_closed() -> None:
    inputs = _inputs()
    inputs.visual_plan.decisions[0].source_resolution_status = "unresolved"
    inputs.visual_plan.decisions[0].selected_source = None
    outcomes = inputs.timeline.diagnostics["query_placement_outcomes"]
    outcomes[0]["status"] = "partially_placed"
    checks = _checks(inputs)
    assert checks["required_visual_sources"].result == "failed"
    assert checks["required_query_placement"].result == "failed"


def test_optional_unresolved_visual_source_is_warning() -> None:
    inputs = _inputs()
    query = inputs.workflow.roteiro_snapshot["blocos"][0]["visual_queries"][0]
    query["importance"] = "optional"
    decision = inputs.visual_plan.decisions[0]
    decision.importance = "optional"
    decision.source_resolution_status = "unresolved"
    decision.selected_source = None
    assert _checks(inputs)["required_visual_sources"].result == "warning"


def test_timeline_gaps_and_partial_coverage_fail_release_gate() -> None:
    inputs = _inputs()
    inputs.timeline.status = "completed_with_gaps"
    inputs.timeline.gap_count = 1
    inputs.timeline.visual_gap_seconds = 1.0
    inputs.timeline.visual_coverage_ratio = 0.9
    checks = _checks(inputs)
    assert checks["production_timeline_status"].result == "failed"
    assert checks["production_timeline_gap_free"].result == "failed"
    assert checks["production_timeline_visual_coverage"].result == "failed"


def test_processing_and_failed_render_have_distinct_results() -> None:
    inputs = _inputs()
    inputs.render.status = "rendering"
    assert _checks(inputs)["render_status"].result == "blocked"
    inputs.render.status = "failed"
    assert _checks(inputs)["render_status"].result == "failed"


def test_wrong_render_profile_and_duration_fail() -> None:
    inputs = _inputs()
    inputs.render.output_audio_sample_rate = 44_100
    inputs.render.output_duration_seconds = 10.1
    checks = _checks(inputs)
    assert checks["render_output_profile"].result == "failed"
    assert checks["render_duration_consistency"].result == "failed"


@pytest.mark.parametrize(
    "profile",
    [
        None,
        "future_unknown_profile",
        "faceless_general",
        "faceless_informational",
        "faceless_pop_culture",
        "faceless_pop_culture_lists",
    ],
)
def test_generic_profile_timeline_policy_matrix(profile: str | None) -> None:
    assert resolve_qa_policy(profile, "production_timeline_schema_v1") == GENERIC_POLICY
    hybrid = resolve_qa_policy(profile, "production_timeline_schema_v2")
    assert hybrid == GENERIC_HYBRID_POLICY
    assert hybrid.supported_source_types == ("video_segment", "static_image")
    assert hybrid.require_narration_alignment is GENERIC_POLICY.require_narration_alignment
    assert (
        hybrid.require_required_visual_queries_resolved
        is GENERIC_POLICY.require_required_visual_queries_resolved
    )


def test_game_analysis_timeline_policy_matrix_preserves_editorial_requirements() -> None:
    assert (
        resolve_qa_policy("game_analysis", "production_timeline_schema_v1") == GAME_ANALYSIS_POLICY
    )
    hybrid = resolve_qa_policy("game_analysis", "production_timeline_schema_v2")
    assert hybrid == GAME_ANALYSIS_HYBRID_POLICY
    assert hybrid.supported_source_types == ("video_segment", "static_image")
    assert hybrid.require_transcription
    assert hybrid.require_narration_alignment
    assert hybrid.require_visual_plan
    assert hybrid.require_required_visual_queries_resolved


def test_hybrid_policy_identity_tracks_material_hard_limit_changes() -> None:
    original = resolve_qa_policy(None, "production_timeline_schema_v2")
    changed = resolve_qa_policy(
        None,
        "production_timeline_schema_v2",
        static_image_maximum_hold_seconds=7.5,
    )
    assert original.identity != changed.identity


def test_valid_generic_hybrid_timeline_can_be_publication_ready() -> None:
    inputs = replace(_inputs(), policy=GENERIC_HYBRID_POLICY)
    inputs.timeline.schema_version = "production_timeline_schema_v2"
    inputs.timeline.placements = _hybrid_placements()
    checks = standard_local_checks(inputs)
    assert all(item.result not in {"failed", "blocked"} for item in checks)
    assert aggregate_checks(checks).publication_ready is True


def test_hybrid_qa_accepts_non_temporal_image_identity_and_rejects_fake_time() -> None:
    inputs = replace(_inputs(), policy=GAME_ANALYSIS_HYBRID_POLICY)
    inputs.timeline.schema_version = "production_timeline_schema_v2"
    placement = SimpleNamespace(
        order=0,
        source_type="static_image",
        source_project_id=uuid.uuid4(),
        source_session_id=None,
        source_media_id=uuid.uuid4(),
        source_image_semantic_index_id=uuid.uuid4(),
        timeline_start_seconds=0.0,
        timeline_end_seconds=5.0,
        candidate_start_seconds=None,
        candidate_end_seconds=None,
        source_start_seconds=None,
        source_end_seconds=None,
        playback_rate=None,
        presentation={
            "policy_version": "static_image_presentation_v1",
            "fit": "contain",
            "background": "blurred_fill",
            "motion": "none",
            "zoom_amount": 0.0,
        },
        source_provenance={
            "expected_sha256": "a" * 64,
            "expected_byte_size": 10,
            "content_type": "image/png",
        },
        visual_decision_id=None,
    )
    inputs.timeline.placements = [placement]
    assert check_hybrid_source_contracts(inputs).result == "passed"
    placement.source_start_seconds = 0
    assert check_hybrid_source_contracts(inputs).result == "failed"


@pytest.mark.parametrize("source_type", ["gameplay", "unknown_source"])
def test_v2_rejects_legacy_and_unknown_source_types(source_type: str) -> None:
    inputs = replace(_inputs(), policy=GENERIC_HYBRID_POLICY)
    inputs.timeline.schema_version = "production_timeline_schema_v2"
    inputs.timeline.placements[0].source_type = source_type
    inputs.timeline.placements[0].order = 0
    assert _checks(inputs)["production_timeline_source_types"].result == "failed"
    assert check_hybrid_source_contracts(inputs).result == "failed"


def test_v2_rejects_video_without_temporal_range() -> None:
    inputs = replace(_inputs(), policy=GENERIC_HYBRID_POLICY)
    inputs.timeline.schema_version = "production_timeline_schema_v2"
    inputs.timeline.placements = _hybrid_placements()
    inputs.timeline.placements[0].source_end_seconds = None
    assert check_hybrid_source_contracts(inputs).result == "failed"


def test_v1_remains_gameplay_only_and_skips_hybrid_contract() -> None:
    inputs = replace(_inputs(), policy=GENERIC_POLICY)
    inputs.timeline.schema_version = "production_timeline_schema_v1"
    assert _checks(inputs)["production_timeline_source_types"].result == "passed"
    inputs.timeline.placements[0].source_type = "static_image"
    checks = _checks(inputs)
    assert checks["production_timeline_source_types"].result == "failed"
    assert checks["hybrid_source_contracts"].result == "skipped"


def test_standard_delivery_requires_exact_one_byte_range_contract() -> None:
    checks = delivery_checks(
        status_code=206,
        content_type="video/mp4",
        content_range="bytes 0-0/1000",
        accept_ranges="bytes",
        byte_count=1,
    )
    assert all(item.result == "passed" for item in checks)
    assert all(
        item.result == "failed" for item in delivery_checks(status_code=200, byte_count=1000)
    )
    assert all(item.result == "blocked" for item in delivery_checks(unavailable=True))


def test_deep_ffprobe_validates_independently() -> None:
    inputs = _inputs()
    probe = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 1,
            },
        ],
        "format": {"duration": "10.0"},
    }
    assert deep_ffprobe_check(probe, inputs).result == "passed"
    probe["streams"][1]["sample_rate"] = "44100"
    assert deep_ffprobe_check(probe, inputs).result == "failed"
