"""Pure, explainable Stage 18 checks over already-produced artifacts."""

from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any, Literal

from app.clients.video_composer_client import ComposerRenderProfile
from app.models.workflow import (
    NarrationAlignment,
    ProductionTimeline,
    TranscricaoAudio,
    VisualPlan,
    WorkflowExecucao,
    WorkflowRender,
)
from app.services.presentation_spec import PresentationInputError, build_presentation_spec
from app.services.production_qa_policy import ProductionQAPolicy
from app.services.visual_director_algorithm import (
    canonical_game_from_session_label,
    extract_games_from_query,
)
from app.services.visual_director_service import resolve_production_profile_config
from app.services.visual_diversity import (
    VisualDiversityPolicy,
    timeline_visual_diversity,
)

QAResult = Literal["passed", "warning", "failed", "blocked", "skipped"]


@dataclass(frozen=True, slots=True)
class ProductionQACheck:
    code: str
    category: str
    result: QAResult
    message: str
    expected: Any | None = None
    actual: Any | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProductionQAInputs:
    workflow: WorkflowExecucao
    transcription: TranscricaoAudio | None
    alignment: NarrationAlignment | None
    visual_plan: VisualPlan | None
    timeline: ProductionTimeline
    render: WorkflowRender
    policy: ProductionQAPolicy
    render_profile: ComposerRenderProfile | None
    visual_diversity_policy: VisualDiversityPolicy = field(default_factory=VisualDiversityPolicy)
    profile_unavailable: bool = False


@dataclass(frozen=True, slots=True)
class QAAggregate:
    status: Literal["passed", "passed_with_warnings", "failed", "blocked"]
    publication_ready: bool
    check_count: int
    passed_count: int
    warning_count: int
    failed_count: int
    blocked_count: int


def aggregate_checks(checks: list[ProductionQACheck]) -> QAAggregate:
    passed = sum(item.result == "passed" for item in checks)
    warnings = sum(item.result == "warning" for item in checks)
    failed = sum(item.result == "failed" for item in checks)
    blocked = sum(item.result == "blocked" for item in checks)
    status: Literal["passed", "passed_with_warnings", "failed", "blocked"]
    if failed:
        status = "failed"
    elif blocked:
        status = "blocked"
    elif warnings:
        status = "passed_with_warnings"
    else:
        status = "passed"
    return QAAggregate(
        status=status,
        publication_ready=status in {"passed", "passed_with_warnings"},
        check_count=len(checks),
        passed_count=passed,
        warning_count=warnings,
        failed_count=failed,
        blocked_count=blocked,
    )


def _query_contracts(workflow: WorkflowExecucao) -> dict[uuid.UUID, dict[str, Any]]:
    result: dict[uuid.UUID, dict[str, Any]] = {}
    blocks = workflow.roteiro_snapshot.get("blocos", [])
    if not isinstance(blocks, list):
        return result
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("visual_queries", []), list):
            continue
        for raw in block.get("visual_queries", []):
            if not isinstance(raw, dict):
                continue
            try:
                query_id = uuid.UUID(str(raw.get("id")))
            except (TypeError, ValueError):
                continue
            excerpt = raw.get("narration_excerpt")
            result[query_id] = {
                "importance": str(raw.get("importance", "preferred")),
                "requires_binding": isinstance(excerpt, str) and bool(excerpt.strip()),
            }
    return result


def check_artifact_chain(inputs: ProductionQAInputs) -> ProductionQACheck:
    workflow = inputs.workflow
    alignment = inputs.alignment
    plan = inputs.visual_plan
    timeline = inputs.timeline
    render = inputs.render
    mismatches: list[str] = []
    if alignment is not None and alignment.final_audio_sha256 != workflow.audio_final_sha256:
        mismatches.append("narration_alignment.final_audio_sha256")
    if plan is not None and (alignment is None or plan.narration_alignment_id != alignment.id):
        mismatches.append("visual_plan.narration_alignment_id")
    if (alignment is not None or timeline.narration_alignment_id is not None) and (
        alignment is None or timeline.narration_alignment_id != alignment.id
    ):
        mismatches.append("production_timeline.narration_alignment_id")
    if (plan is not None or timeline.visual_plan_id is not None) and (
        plan is None or timeline.visual_plan_id != plan.id
    ):
        mismatches.append("production_timeline.visual_plan_id")
    if render.production_timeline_id != timeline.id:
        mismatches.append("workflow_render.production_timeline_id")
    if render.production_timeline_fingerprint != timeline.fingerprint:
        mismatches.append("workflow_render.production_timeline_fingerprint")
    if render.output_sha256 is None:
        mismatches.append("workflow_render.output_sha256")
    if mismatches:
        return ProductionQACheck(
            "artifact_chain_identity",
            "artifact_chain",
            "failed",
            "Production artifact chain contains stale or incompatible identities.",
            actual=mismatches,
        )
    return ProductionQACheck(
        "artifact_chain_identity",
        "artifact_chain",
        "passed",
        "Production artifact chain is internally consistent.",
    )


def check_production_workflow_status(inputs: ProductionQAInputs) -> ProductionQACheck:
    status = inputs.workflow.production_status
    if status == "completed":
        result: QAResult = "passed"
        message = "Stage 17 production lifecycle is completed."
    elif status in {"queued", "running", "blocked", None}:
        result = "blocked"
        message = "Stage 17 production lifecycle has not completed."
    else:
        result = "failed"
        message = "Stage 17 production lifecycle ended unsuccessfully."
    return ProductionQACheck(
        "production_workflow_status",
        "artifact_chain",
        result,
        message,
        expected="completed",
        actual=status,
    )


def check_presentation_spec(inputs: ProductionQAInputs) -> ProductionQACheck:
    if inputs.transcription is None:
        return ProductionQACheck(
            "presentation_spec",
            "presentation",
            "skipped",
            "Legacy render has no authoritative transcription-backed PresentationSpec.",
        )
    if not hasattr(inputs.transcription, "palavras_timestamps"):
        return ProductionQACheck(
            "presentation_spec",
            "presentation",
            "skipped",
            "PresentationSpec evidence is unavailable in this compatibility fixture.",
        )
    try:
        profile_config = resolve_production_profile_config(inputs.workflow.perfil_producao)
        burned_in_captions = profile_config.burned_in_captions if profile_config else True
        presentation = build_presentation_spec(
            inputs.workflow,
            duration_seconds=inputs.timeline.duration_seconds,
            burned_in_captions=burned_in_captions,
        )
    except (PresentationInputError, ValueError) as exc:
        return ProductionQACheck(
            "presentation_spec",
            "presentation",
            "failed",
            "PresentationSpec is structurally invalid or stale.",
            actual=str(exc),
        )
    if presentation is None:
        return ProductionQACheck(
            "presentation_spec",
            "presentation",
            "skipped",
            "Legacy render intentionally has no PresentationSpec.",
        )
    caption_cue_count = len(presentation.captions.cues) if presentation.captions else 0
    return ProductionQACheck(
        "presentation_spec",
        "presentation",
        "passed",
        "PresentationSpec timestamps, presets and canonical fingerprint are valid.",
        actual=presentation.fingerprint,
        details={
            "caption_cues": caption_cue_count,
            "section_titles": len(presentation.section_titles),
            "transitions": len(presentation.transitions),
            "branding_preset": presentation.branding.preset,
        },
    )


def visual_plan_semantic_metrics(plan: VisualPlan | None) -> dict[str, int | float]:
    """Summarize the plan's deterministic semantic and entity signals."""
    decisions = list(getattr(plan, "decisions", []) or [])
    resolved = [item for item in decisions if item.selected_source]
    scored = [
        item for item in resolved
        if (item.selected_source or {}).get("semantic_score") is not None
    ]
    strong = sum(
        float((item.selected_source or {})["semantic_score"]) >= 0.6
        for item in scored
    )
    violations = 0
    for item in resolved:
        expected = extract_games_from_query(getattr(item, "query", ""))
        if not expected:
            continue
        selected = item.selected_source
        assert selected is not None
        label = selected.get("session_label")
        if label and canonical_game_from_session_label(str(label)) not in expected:
            violations += 1
    return dict(
        semantic_coverage=round(len(resolved) / len(decisions), 6) if decisions else 0.0,
        strong_match_count=strong,
        weak_match_count=len(scored) - strong,
        unscored_match_count=len(resolved) - len(scored),
        unresolved_count=sum(item.source_resolution_status == "unresolved" for item in decisions),
        entity_violation_count=violations,
    )


def check_visual_diversity(inputs: ProductionQAInputs) -> ProductionQACheck:
    evaluation = timeline_visual_diversity(
        inputs.timeline.placements,
        inputs.visual_diversity_policy,
    )
    actual = dict(evaluation.metrics)
    actual.update(visual_plan_semantic_metrics(inputs.visual_plan))
    semantic_failed = bool(
        actual["entity_violation_count"]
        or (inputs.visual_plan and inputs.visual_plan.decisions and not actual["semantic_coverage"])
    )
    reasons = list(evaluation.reasons)
    if actual["entity_violation_count"]:
        reasons.append("selected source conflicts with query game identity")
    if inputs.visual_plan and inputs.visual_plan.decisions and not actual["semantic_coverage"]:
        reasons.append("all visual queries lack selected sources")
    status = "failed" if evaluation.status == "failed" or semantic_failed else evaluation.status
    return ProductionQACheck(
        "visual_diversity",
        "visual",
        "failed"
        if status == "failed"
        else "warning"
        if status == "warning"
        else "passed",
        "Visual source diversity passed the deterministic policy."
        if status == "passed"
        else "Visual source diversity needs review."
        if status == "warning"
        else "Visual source diversity is degenerate.",
        actual=actual,
        details={
            "status": status,
            "reasons": reasons,
            "policy": evaluation.to_dict()["policy"],
        },
    )


def check_final_audio_identity(inputs: ProductionQAInputs) -> ProductionQACheck:
    workflow = inputs.workflow
    actual = {
        "sha256": workflow.audio_final_sha256,
        "byte_size": workflow.audio_final_byte_size,
        "duration_seconds": workflow.duracao_audio_segundos,
    }
    valid = bool(
        workflow.audio_final_sha256
        and workflow.audio_final_byte_size
        and workflow.audio_final_byte_size > 0
        and workflow.duracao_audio_segundos
        and workflow.duracao_audio_segundos > 0
    )
    return ProductionQACheck(
        "final_audio_identity",
        "audio",
        "passed" if valid else "failed",
        "Final narration identity is complete."
        if valid
        else "Final narration identity or size/duration metadata is invalid.",
        actual=actual,
    )


def check_final_audio_format(inputs: ProductionQAInputs) -> ProductionQACheck:
    expected = {
        "sample_rate": inputs.policy.expected_audio_sample_rate,
        "channels": inputs.policy.expected_audio_channels,
    }
    actual = {
        "sample_rate": inputs.workflow.audio_final_sample_rate,
        "channels": inputs.workflow.audio_final_channels,
    }
    valid = actual == expected
    return ProductionQACheck(
        "final_audio_format",
        "audio",
        "passed" if valid else "failed",
        "Final narration format matches the QA policy."
        if valid
        else "Final narration format does not match the QA policy.",
        expected=expected,
        actual=actual,
    )


def _mastering_values(inputs: ProductionQAInputs) -> tuple[dict, dict, dict] | None:
    config = inputs.workflow.mastering_config
    metrics = inputs.workflow.mastering_metrics
    if not isinstance(config, dict) or not isinstance(metrics, dict):
        return None
    after = metrics.get("after")
    if not isinstance(after, dict):
        return None
    return config, metrics, after


def check_mastering_true_peak(inputs: ProductionQAInputs) -> ProductionQACheck:
    if not inputs.policy.validate_mastering:
        return ProductionQACheck(
            "mastering_true_peak", "audio", "skipped", "Mastering validation is disabled."
        )
    values = _mastering_values(inputs)
    if values is None:
        return ProductionQACheck(
            "mastering_true_peak",
            "audio",
            "blocked",
            "Persisted mastering measurements are unavailable.",
        )
    config, _, after = values
    try:
        actual = float(after["true_peak_dbtp"])
        ceiling = float(config["max_true_peak_dbtp"])
        tolerance = float(config.get("true_peak_tolerance_db", 0.02))
    except (KeyError, TypeError, ValueError):
        return ProductionQACheck(
            "mastering_true_peak", "audio", "failed", "Mastering True Peak metadata is invalid."
        )
    valid = math.isfinite(actual) and actual <= ceiling + tolerance
    return ProductionQACheck(
        "mastering_true_peak",
        "audio",
        "passed" if valid else "failed",
        "Final narration respects the persisted True Peak ceiling."
        if valid
        else "Final narration exceeds the persisted True Peak safety ceiling.",
        expected={"maximum_dbtp": ceiling, "tolerance_db": tolerance},
        actual=actual,
    )


def check_mastering_loudness(inputs: ProductionQAInputs) -> ProductionQACheck:
    if not inputs.policy.validate_mastering:
        return ProductionQACheck(
            "mastering_loudness", "audio", "skipped", "Mastering validation is disabled."
        )
    values = _mastering_values(inputs)
    if values is None:
        return ProductionQACheck(
            "mastering_loudness",
            "audio",
            "blocked",
            "Persisted mastering measurements are unavailable.",
        )
    config, metrics, after = values
    try:
        actual = float(after["integrated_lufs"])
        target = float(config["target_lufs"])
        minimum = float(config["min_acceptable_lufs"])
        maximum = float(config["max_acceptable_lufs"])
    except (KeyError, TypeError, ValueError):
        return ProductionQACheck(
            "mastering_loudness", "audio", "failed", "Mastering loudness metadata is invalid."
        )
    peak_constrained = bool(metrics.get("peak_constrained"))
    if not math.isfinite(actual) or actual > maximum or (actual < minimum and not peak_constrained):
        result: QAResult = "failed"
        message = "Final narration loudness is outside the persisted mastering policy."
    elif peak_constrained:
        result = "warning"
        message = "True Peak headroom constrained the loudness target as designed."
    else:
        result = "passed"
        message = "Final narration loudness is within the persisted mastering policy."
    return ProductionQACheck(
        "mastering_loudness",
        "audio",
        result,
        message,
        expected={"target_lufs": target, "minimum_lufs": minimum, "maximum_lufs": maximum},
        actual={"integrated_lufs": actual, "peak_constrained": peak_constrained},
    )


def check_transcription_identity(inputs: ProductionQAInputs) -> ProductionQACheck:
    transcription = inputs.transcription
    if transcription is None:
        if inputs.policy.require_transcription:
            return ProductionQACheck(
                "transcription_identity",
                "transcription",
                "blocked",
                "Required narration transcription is not available.",
            )
        return ProductionQACheck(
            "transcription_identity",
            "transcription",
            "skipped",
            "Transcription is not required by this QA policy.",
        )
    valid = transcription.audio_sha256 == inputs.workflow.audio_final_sha256
    return ProductionQACheck(
        "transcription_identity",
        "transcription",
        "passed" if valid else "failed",
        "Transcription belongs to the final narration."
        if valid
        else "Transcription belongs to another narration identity.",
        expected=inputs.workflow.audio_final_sha256,
        actual=transcription.audio_sha256,
    )


def check_narration_alignment_status(inputs: ProductionQAInputs) -> ProductionQACheck:
    alignment = inputs.alignment
    if alignment is None:
        result: QAResult = "blocked" if inputs.policy.require_narration_alignment else "skipped"
        return ProductionQACheck(
            "narration_alignment_status",
            "narration_alignment",
            result,
            "Compatible NarrationAlignment is unavailable."
            if result == "blocked"
            else "NarrationAlignment is not required by this QA policy.",
        )
    if alignment.status == "completed":
        result = "passed"
        message = "NarrationAlignment is completed and compatible."
    elif alignment.status == "blocked":
        result = "blocked"
        message = "NarrationAlignment is blocked."
    else:
        result = "failed"
        message = "NarrationAlignment is not a completed compatible artifact."
    return ProductionQACheck(
        "narration_alignment_status",
        "narration_alignment",
        result,
        message,
        expected="completed",
        actual=alignment.status,
    )


def check_required_narration_bindings(inputs: ProductionQAInputs) -> ProductionQACheck:
    if not inputs.policy.require_required_visual_queries_resolved:
        return ProductionQACheck(
            "required_narration_bindings",
            "narration_alignment",
            "skipped",
            "VisualQuery binding requirements are not defined by this QA policy.",
        )
    if inputs.alignment is None:
        return ProductionQACheck(
            "required_narration_bindings",
            "narration_alignment",
            "blocked",
            "Narration bindings are unavailable.",
        )
    queries = _query_contracts(inputs.workflow)
    bindings = {item.visual_query_id: item for item in inputs.alignment.visual_query_bindings}
    required_bad: list[str] = []
    advisory_bad: list[str] = []
    for query_id, contract in queries.items():
        if not contract["requires_binding"]:
            continue
        binding = bindings.get(query_id)
        status = binding.status if binding is not None else "missing"
        if contract["importance"] == "required" and status != "resolved":
            required_bad.append(f"{query_id}:{status}")
        elif contract["importance"] != "required" and status != "resolved":
            advisory_bad.append(f"{query_id}:{status}")
    if required_bad:
        result: QAResult = "failed"
        message = "One or more required VisualQueries lack resolved narration bindings."
    elif advisory_bad:
        result = "warning"
        message = "Preferred or optional VisualQueries lack resolved narration bindings."
    else:
        result = "passed"
        message = "Required narration bindings are resolved."
    return ProductionQACheck(
        "required_narration_bindings",
        "narration_alignment",
        result,
        message,
        actual={"required": required_bad, "advisory": advisory_bad},
    )


def check_narration_alignment_coverage(inputs: ProductionQAInputs) -> ProductionQACheck:
    alignment = inputs.alignment
    if alignment is None:
        return ProductionQACheck(
            "narration_alignment_coverage",
            "narration_alignment",
            "blocked" if inputs.policy.require_narration_alignment else "skipped",
            "Narration alignment coverage is unavailable.",
        )
    blocks = list(alignment.block_alignments)
    average = sum(item.overall_coverage for item in blocks) / len(blocks) if blocks else 0.0
    threshold = inputs.policy.alignment_coverage_warning_threshold
    warning = bool(blocks) and average < threshold
    return ProductionQACheck(
        "narration_alignment_coverage",
        "narration_alignment",
        "warning" if warning else "passed",
        "Alignment coverage is below the preferred QA threshold."
        if warning
        else "Alignment coverage diagnostics are acceptable.",
        expected={"preferred_minimum": threshold},
        actual={"average_overall_coverage": round(average, 6), "block_count": len(blocks)},
    )


def check_visual_plan_status(inputs: ProductionQAInputs) -> ProductionQACheck:
    plan = inputs.visual_plan
    if plan is None:
        result: QAResult = "blocked" if inputs.policy.require_visual_plan else "skipped"
        return ProductionQACheck(
            "visual_plan_status",
            "visual_plan",
            result,
            "Compatible VisualPlan is unavailable."
            if result == "blocked"
            else "VisualPlan is not required by this QA policy.",
        )
    if plan.status in {"completed", "completed_with_gaps"}:
        result = "passed" if plan.status == "completed" else "warning"
        message = (
            "VisualPlan is completed."
            if result == "passed"
            else "VisualPlan completed with declared gaps; downstream checks determine release."
        )
    elif plan.status == "blocked":
        result = "blocked"
        message = "VisualPlan is blocked."
    else:
        result = "failed"
        message = "VisualPlan is not a completed compatible artifact."
    return ProductionQACheck(
        "visual_plan_status", "visual_plan", result, message, actual=plan.status
    )


def check_required_visual_sources(inputs: ProductionQAInputs) -> ProductionQACheck:
    if not inputs.policy.require_required_visual_queries_resolved:
        return ProductionQACheck(
            "required_visual_sources",
            "visual_plan",
            "skipped",
            "Visual source requirements are not defined by this QA policy.",
        )
    if inputs.visual_plan is None:
        return ProductionQACheck(
            "required_visual_sources", "visual_plan", "blocked", "Visual decisions are unavailable."
        )
    required_bad: list[str] = []
    advisory_bad: list[str] = []
    for decision in inputs.visual_plan.decisions:
        resolved = decision.source_resolution_status == "resolved" and bool(
            decision.selected_source
        )
        if resolved:
            continue
        value = f"{decision.visual_query_id}:{decision.source_resolution_status}"
        if decision.importance == "required":
            required_bad.append(value)
        else:
            advisory_bad.append(value)
    if required_bad:
        result: QAResult = "failed"
        message = "One or more required VisualQueries lack a resolved visual source."
    elif advisory_bad:
        result = "warning"
        message = "Preferred or optional VisualQueries lack a resolved visual source."
    else:
        result = "passed"
        message = "Required visual sources are resolved."
    return ProductionQACheck(
        "required_visual_sources",
        "visual_plan",
        result,
        message,
        actual={"required": required_bad, "advisory": advisory_bad},
    )


def check_production_timeline_status(inputs: ProductionQAInputs) -> ProductionQACheck:
    timeline = inputs.timeline
    if timeline.status == "completed":
        result: QAResult = "passed"
        message = "ProductionTimeline is completed."
    elif timeline.status in {"pending", "processing", "blocked"}:
        result = "blocked"
        message = "ProductionTimeline is not ready for final release QA."
    else:
        result = "failed"
        message = "Rendered production references a non-final ProductionTimeline."
    return ProductionQACheck(
        "production_timeline_status",
        "production_timeline",
        result,
        message,
        expected="completed",
        actual=timeline.status,
    )


def check_production_timeline_gap_free(inputs: ProductionQAInputs) -> ProductionQACheck:
    timeline = inputs.timeline
    valid = not inputs.policy.require_gap_free_timeline or (
        timeline.gap_count == 0 and abs(timeline.visual_gap_seconds) <= 0.000001
    )
    return ProductionQACheck(
        "production_timeline_gap_free",
        "production_timeline",
        "passed" if valid else "failed",
        "Final visual timeline is gap-free."
        if valid
        else "Final render was derived from a timeline containing visual gaps.",
        expected={"gap_count": 0, "visual_gap_seconds": 0.0},
        actual={
            "gap_count": timeline.gap_count,
            "visual_gap_seconds": timeline.visual_gap_seconds,
        },
    )


def check_production_timeline_visual_coverage(
    inputs: ProductionQAInputs,
) -> ProductionQACheck:
    actual = inputs.timeline.visual_coverage_ratio
    valid = not inputs.policy.require_gap_free_timeline or math.isclose(
        actual, 1.0, abs_tol=0.000001
    )
    return ProductionQACheck(
        "production_timeline_visual_coverage",
        "production_timeline",
        "passed" if valid else "failed",
        "Final visual timeline has full duration coverage."
        if valid
        else "Final visual timeline does not cover the complete narration duration.",
        expected=1.0,
        actual=actual,
    )


def check_required_query_placement(inputs: ProductionQAInputs) -> ProductionQACheck:
    if not inputs.policy.require_required_visual_queries_resolved:
        return ProductionQACheck(
            "required_query_placement",
            "production_timeline",
            "skipped",
            "VisualQuery placement requirements are not defined by this QA policy.",
        )
    outcomes = (
        inputs.timeline.diagnostics.get("query_placement_outcomes", [])
        if isinstance(inputs.timeline.diagnostics, dict)
        else []
    )
    queries = _query_contracts(inputs.workflow)
    outcome_map = {
        str(item.get("visual_query_id")): item
        for item in outcomes
        if isinstance(item, dict) and item.get("visual_query_id")
    }
    required_bad: list[str] = []
    advisory_bad: list[str] = []
    for query_id, contract in queries.items():
        item = outcome_map.get(str(query_id))
        status = str(item.get("status")) if item else "missing"
        valid = status in {"fully_placed", "not_applicable"}
        if valid:
            continue
        value = f"{query_id}:{status}"
        if contract["importance"] == "required":
            required_bad.append(value)
        else:
            advisory_bad.append(value)
    if required_bad:
        result: QAResult = "failed"
        message = "One or more required VisualQueries are not fully placed."
    elif advisory_bad:
        result = "warning"
        message = "Preferred or optional VisualQueries are only partially placed or unplaced."
    else:
        result = "passed"
        message = "Required VisualQueries are fully placed."
    return ProductionQACheck(
        "required_query_placement",
        "production_timeline",
        result,
        message,
        actual={"required": required_bad, "advisory": advisory_bad},
    )


def check_source_types(inputs: ProductionQAInputs) -> ProductionQACheck:
    unsupported = sorted(
        {
            item.source_type
            for item in inputs.timeline.placements
            if item.source_type not in inputs.policy.supported_source_types
        }
    )
    return ProductionQACheck(
        "production_timeline_source_types",
        "production_timeline",
        "failed" if unsupported else "passed",
        "ProductionTimeline contains source types unsupported by the frozen Composer contract."
        if unsupported
        else "ProductionTimeline source types are supported by the Composer contract.",
        expected=list(inputs.policy.supported_source_types),
        actual=unsupported,
    )


def check_hybrid_source_contracts(inputs: ProductionQAInputs) -> ProductionQACheck:
    if getattr(inputs.timeline, "schema_version", None) != "production_timeline_schema_v2":
        return ProductionQACheck(
            "hybrid_source_contracts",
            "production_timeline",
            "skipped",
            "Hybrid source contracts do not apply to this legacy timeline.",
        )
    failures: list[str] = []
    selected_by_decision = {
        item.id: item.selected_source
        for item in (inputs.visual_plan.decisions if inputs.visual_plan else [])
        if getattr(item, "id", None) is not None and item.selected_source
    }
    consecutive = 0.0
    previous_end: float | None = None
    policy = inputs.policy
    for placement in sorted(inputs.timeline.placements, key=lambda item: item.order):
        duration = placement.timeline_end_seconds - placement.timeline_start_seconds
        if placement.source_type == "static_image":
            provenance = placement.source_provenance or {}
            presentation = placement.presentation or {}
            if any(
                value is not None
                for value in (
                    placement.source_session_id,
                    placement.candidate_start_seconds,
                    placement.candidate_end_seconds,
                    placement.source_start_seconds,
                    placement.source_end_seconds,
                    placement.playback_rate,
                )
            ):
                failures.append(f"placement_{placement.order}:temporal_image_fields")
            if not (
                placement.source_image_semantic_index_id
                and len(str(provenance.get("expected_sha256", ""))) == 64
                and int(provenance.get("expected_byte_size", -1)) >= 0
                and provenance.get("content_type") in {"image/jpeg", "image/png", "image/webp"}
                and presentation.get("policy_version") == "static_image_presentation_v1"
                and presentation.get("fit") in {"cover", "contain"}
                and presentation.get("background") in {"black", "blurred_fill"}
                and presentation.get("motion") in {"none", "slow_zoom_in", "slow_zoom_out"}
                and isinstance(presentation.get("zoom_amount"), int | float)
                and 0 <= presentation["zoom_amount"] <= 0.1
            ):
                failures.append(f"placement_{placement.order}:image_contract")
            if (
                policy.static_image_minimum_hold_seconds is not None
                and duration < policy.static_image_minimum_hold_seconds
            ) or (
                policy.static_image_maximum_hold_seconds is not None
                and duration > policy.static_image_maximum_hold_seconds
            ):
                failures.append(f"placement_{placement.order}:image_hold")
            if previous_end != placement.timeline_start_seconds:
                consecutive = 0.0
            consecutive += duration
            if (
                policy.static_image_maximum_consecutive_seconds is not None
                and consecutive > policy.static_image_maximum_consecutive_seconds
            ):
                failures.append("consecutive_static_hold")
            selected = (
                selected_by_decision.get(placement.visual_decision_id)
                if placement.visual_decision_id is not None
                else None
            )
            if selected and (
                selected.get("source_type") != "static_image"
                or str(selected.get("media_id")) != str(placement.source_media_id)
                or str(selected.get("image_semantic_index_id"))
                != str(placement.source_image_semantic_index_id)
                or selected.get("sha256") != provenance.get("expected_sha256")
            ):
                failures.append(f"placement_{placement.order}:visual_plan_identity")
        elif placement.source_type == "video_segment":
            consecutive = 0.0
            if (
                placement.source_session_id is None
                or placement.source_image_semantic_index_id is not None
                or placement.source_start_seconds is None
                or placement.source_end_seconds is None
                or placement.playback_rate != 1.0
                or placement.presentation is not None
            ):
                failures.append(f"placement_{placement.order}:video_contract")
        else:
            failures.append(f"placement_{placement.order}:source_type")
        previous_end = placement.timeline_end_seconds
    return ProductionQACheck(
        "hybrid_source_contracts",
        "production_timeline",
        "failed" if failures else "passed",
        "Hybrid timeline source identities and presentation rules are valid."
        if not failures
        else "Hybrid timeline contains invalid source contracts.",
        actual=sorted(set(failures)),
    )


def check_render_status(inputs: ProductionQAInputs) -> ProductionQACheck:
    render = inputs.render
    has_output = bool(
        render.output_sha256
        and render.output_byte_size
        and render.output_byte_size > 0
        and render.output_duration_seconds
        and render.output_duration_seconds > 0
    )
    if render.status == "completed" and has_output:
        result: QAResult = "passed"
        message = "Current WorkflowRender is completed with output metadata."
    elif render.status == "failed" or (render.status == "completed" and not has_output):
        result = "failed"
        message = "WorkflowRender failed or is missing mandatory output metadata."
    else:
        result = "blocked"
        message = "WorkflowRender has not completed."
    return ProductionQACheck(
        "render_status",
        "render",
        result,
        message,
        expected="completed with output metadata",
        actual=render.status,
    )


def check_program_audio(inputs: ProductionQAInputs) -> list[ProductionQACheck]:
    is_program = inputs.render.render_profile == "youtube_1080p30_program_audio_v1"
    if not is_program:
        return [
            ProductionQACheck(
                code,
                "program_audio",
                "skipped",
                "Legacy narration-only render intentionally has no Program Audio contract.",
            )
            for code in (
                "audio_mix_plan_identity",
                "music_asset_identity",
                "program_audio_format",
                "program_audio_loudness",
                "program_audio_true_peak",
                "program_master_identity",
            )
        ]
    raw = (getattr(inputs.render, "metrics", None) or {}).get("programAudio")
    metrics = raw if isinstance(raw, dict) else {}
    plan_fingerprint = metrics.get("audioMixPlanFingerprint")
    music_asset_id = metrics.get("musicAssetId")
    music_sha = metrics.get("musicSha256")
    after: dict[str, Any] = metrics["after"] if isinstance(metrics.get("after"), dict) else {}
    loudness = after.get("integratedLufs")
    true_peak = after.get("truePeakDbtp")
    target_lufs = metrics.get("targetLufs")
    max_true_peak = metrics.get("maxTruePeakDbtp")
    checks = [
        ProductionQACheck(
            "audio_mix_plan_identity",
            "program_audio",
            "passed"
            if isinstance(plan_fingerprint, str) and len(plan_fingerprint) == 64
            else "failed",
            "AudioMixPlan canonical identity is persisted with the render.",
            actual=plan_fingerprint,
        ),
        ProductionQACheck(
            "music_asset_identity",
            "program_audio",
            "passed"
            if (
                (music_asset_id is None and music_sha is None)
                or (
                    isinstance(music_asset_id, str)
                    and bool(music_asset_id)
                    and isinstance(music_sha, str)
                    and len(music_sha) == 64
                )
            )
            else "failed",
            "Concrete immutable music identity is present when music is enabled.",
            actual={"asset_id": music_asset_id, "sha256": music_sha},
        ),
        ProductionQACheck(
            "program_audio_format",
            "program_audio",
            "passed"
            if metrics.get("outputChannels") == 2 and metrics.get("outputSampleRate") == 48_000
            else "failed",
            "Program Audio output is explicitly 48 kHz stereo.",
            expected={"channels": 2, "sample_rate": 48_000},
            actual={
                "channels": metrics.get("outputChannels"),
                "sample_rate": metrics.get("outputSampleRate"),
            },
        ),
        ProductionQACheck(
            "program_audio_loudness",
            "program_audio",
            "passed"
            if isinstance(loudness, int | float)
            and isinstance(target_lufs, int | float)
            and math.isclose(loudness, target_lufs, abs_tol=0.6)
            else "failed",
            "Measured full-program loudness satisfies the candidate profile.",
            expected={"target_lufs": target_lufs, "tolerance": 0.6},
            actual=loudness,
        ),
        ProductionQACheck(
            "program_audio_true_peak",
            "program_audio",
            "passed"
            if isinstance(true_peak, int | float)
            and isinstance(max_true_peak, int | float)
            and true_peak <= max_true_peak + 0.1
            else "failed",
            "Measured full-program True Peak satisfies the candidate ceiling.",
            expected={"maximum_dbtp": max_true_peak, "tolerance": 0.1},
            actual=true_peak,
        ),
        ProductionQACheck(
            "program_master_identity",
            "program_audio",
            "passed"
            if metrics.get("programMasterProfile") == "youtube_program_master_v1_candidate"
            else "failed",
            "Program master profile identity is explicit and distinct from narration mastering.",
            actual=metrics.get("programMasterProfile"),
        ),
    ]
    return checks


def check_render_timeline_identity(inputs: ProductionQAInputs) -> ProductionQACheck:
    valid = (
        inputs.render.production_timeline_id == inputs.timeline.id
        and inputs.render.production_timeline_fingerprint == inputs.timeline.fingerprint
    )
    return ProductionQACheck(
        "render_timeline_identity",
        "render",
        "passed" if valid else "failed",
        "Render references the current ProductionTimeline identity."
        if valid
        else "Render references a stale ProductionTimeline identity.",
        expected={"id": str(inputs.timeline.id), "fingerprint": inputs.timeline.fingerprint},
        actual={
            "id": str(inputs.render.production_timeline_id),
            "fingerprint": inputs.render.production_timeline_fingerprint,
        },
    )


def check_render_output_profile(inputs: ProductionQAInputs) -> ProductionQACheck:
    if not inputs.policy.validate_render_profile:
        return ProductionQACheck(
            "render_output_profile", "profile", "skipped", "Render profile validation is disabled."
        )
    profile = inputs.render_profile
    if profile is None:
        return ProductionQACheck(
            "render_output_profile",
            "profile",
            "blocked" if inputs.profile_unavailable else "failed",
            "Video Composer profile contract is temporarily unavailable."
            if inputs.profile_unavailable
            else "Declared render profile is unknown to Video Composer.",
            actual=inputs.render.render_profile,
        )
    render = inputs.render
    expected = {
        "profile_identity": profile.identity,
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "video_codec": profile.video_codec,
        "audio_codec": profile.audio_codec,
        "audio_sample_rate": profile.audio_sample_rate,
        "audio_channels": profile.audio_channels,
    }
    actual = {
        "profile_identity": render.render_profile_identity,
        "width": render.output_width,
        "height": render.output_height,
        "fps": render.output_fps,
        "video_codec": render.output_video_codec,
        "audio_codec": render.output_audio_codec,
        "audio_sample_rate": render.output_audio_sample_rate,
        "audio_channels": (
            ((getattr(render, "metrics", None) or {}).get("programAudio") or {}).get(
                "outputChannels"
            )
            if profile.audio_channels == 2
            else 1
        ),
    }
    valid = (
        actual["profile_identity"] == expected["profile_identity"]
        and actual["width"] == expected["width"]
        and actual["height"] == expected["height"]
        and actual["fps"] is not None
        and math.isclose(float(actual["fps"]), profile.fps, abs_tol=0.001)
        and actual["video_codec"] == expected["video_codec"]
        and actual["audio_codec"] == expected["audio_codec"]
        and actual["audio_sample_rate"] == expected["audio_sample_rate"]
        and actual["audio_channels"] == expected["audio_channels"]
    )
    return ProductionQACheck(
        "render_output_profile",
        "profile",
        "passed" if valid else "failed",
        "Persisted render output matches the declared Composer profile."
        if valid
        else "Persisted render output does not match the declared Composer profile.",
        expected=expected,
        actual=actual,
    )


def check_render_duration(inputs: ProductionQAInputs) -> ProductionQACheck:
    profile = inputs.render_profile
    if profile is None or inputs.render.output_duration_seconds is None:
        return ProductionQACheck(
            "render_duration_consistency",
            "render",
            "blocked" if inputs.profile_unavailable else "failed",
            "Render duration or profile metadata is unavailable.",
        )
    tolerance = 1.0 / profile.fps + 0.005
    difference = abs(inputs.render.output_duration_seconds - inputs.timeline.duration_seconds)
    valid = difference <= tolerance
    return ProductionQACheck(
        "render_duration_consistency",
        "render",
        "passed" if valid else "failed",
        "Render duration matches the ProductionTimeline within one output frame."
        if valid
        else "Render duration differs from the ProductionTimeline beyond one output frame.",
        expected={"duration_seconds": inputs.timeline.duration_seconds, "tolerance": tolerance},
        actual={
            "duration_seconds": inputs.render.output_duration_seconds,
            "difference": difference,
        },
    )


def standard_local_checks(inputs: ProductionQAInputs) -> list[ProductionQACheck]:
    return [
        check_production_workflow_status(inputs),
        check_artifact_chain(inputs),
        check_final_audio_identity(inputs),
        check_final_audio_format(inputs),
        check_mastering_true_peak(inputs),
        check_mastering_loudness(inputs),
        check_transcription_identity(inputs),
        check_presentation_spec(inputs),
        check_narration_alignment_status(inputs),
        check_required_narration_bindings(inputs),
        check_narration_alignment_coverage(inputs),
        check_visual_plan_status(inputs),
        check_required_visual_sources(inputs),
        check_visual_diversity(inputs),
        check_production_timeline_status(inputs),
        check_production_timeline_gap_free(inputs),
        check_production_timeline_visual_coverage(inputs),
        check_required_query_placement(inputs),
        check_source_types(inputs),
        check_hybrid_source_contracts(inputs),
        check_render_status(inputs),
        *check_program_audio(inputs),
        check_render_timeline_identity(inputs),
        check_render_output_profile(inputs),
        check_render_duration(inputs),
    ]


def delivery_checks(
    *,
    status_code: int | None = None,
    content_type: str | None = None,
    content_range: str | None = None,
    accept_ranges: str | None = None,
    byte_count: int | None = None,
    unavailable: bool = False,
) -> list[ProductionQACheck]:
    if unavailable:
        return [
            ProductionQACheck(
                "final_video_accessible",
                "delivery",
                "blocked",
                "Video Composer is temporarily unavailable.",
            ),
            ProductionQACheck(
                "final_video_range_support",
                "delivery",
                "blocked",
                "Video Composer Range support cannot be verified while unavailable.",
            ),
        ]
    accessible = (
        status_code == 206
        and (content_type or "").split(";", 1)[0].strip().lower() == "video/mp4"
        and byte_count == 1
        and bool(content_range)
    )
    range_valid = (
        status_code == 206
        and (accept_ranges or "").lower() == "bytes"
        and bool(content_range and content_range.lower().startswith("bytes 0-0/"))
    )
    details = {
        "status_code": status_code,
        "content_type": content_type,
        "content_range": content_range,
        "accept_ranges": accept_ranges,
        "byte_count": byte_count,
    }
    return [
        ProductionQACheck(
            "final_video_accessible",
            "delivery",
            "passed" if accessible else "failed",
            "Final MP4 is accessible through a one-byte Range request."
            if accessible
            else "Final MP4 failed the bounded accessibility check.",
            actual=details,
        ),
        ProductionQACheck(
            "final_video_range_support",
            "delivery",
            "passed" if range_valid else "failed",
            "Final MP4 delivery implements byte ranges."
            if range_valid
            else "Final MP4 delivery does not satisfy the byte-range contract.",
            actual=details,
        ),
    ]


def deep_sha_check(expected: str | None, actual: str, byte_count: int) -> ProductionQACheck:
    valid = bool(expected and expected == actual and byte_count > 0)
    return ProductionQACheck(
        "full_content_sha",
        "delivery",
        "passed" if valid else "failed",
        "Independently streamed MP4 SHA-256 matches WorkflowRender."
        if valid
        else "Independently streamed MP4 SHA-256 does not match WorkflowRender.",
        expected=expected,
        actual={"sha256": actual, "byte_size": byte_count},
    )


def _rate(value: Any) -> float | None:
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None


def deep_ffprobe_check(probe: dict[str, Any], inputs: ProductionQAInputs) -> ProductionQACheck:
    profile = inputs.render_profile
    if profile is None:
        return ProductionQACheck(
            "deep_ffprobe_validation",
            "render",
            "blocked" if inputs.profile_unavailable else "failed",
            "Render profile is unavailable for independent ffprobe validation.",
        )
    streams = probe.get("streams", [])
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    video = videos[0] if len(videos) == 1 else {}
    audio = audios[0] if len(audios) == 1 else {}
    fps = _rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    try:
        duration = float(probe.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    tolerance = 1.0 / profile.fps + 0.005
    mismatches: list[str] = []
    if len(videos) != 1:
        mismatches.append("video_stream_count")
    if len(audios) != 1:
        mismatches.append("audio_stream_count")
    if video.get("width") != profile.width or video.get("height") != profile.height:
        mismatches.append("resolution")
    if fps is None or not math.isclose(fps, profile.fps, abs_tol=0.001):
        mismatches.append("fps")
    if video.get("codec_name") != profile.video_codec:
        mismatches.append("video_codec")
    if audio.get("codec_name") != profile.audio_codec:
        mismatches.append("audio_codec")
    if str(audio.get("sample_rate")) != str(profile.audio_sample_rate):
        mismatches.append("audio_sample_rate")
    if audio.get("channels") != profile.audio_channels:
        mismatches.append("audio_channels")
    if duration is None or abs(duration - inputs.timeline.duration_seconds) > tolerance:
        mismatches.append("duration")
    persisted = inputs.render
    if (
        video.get("width") != persisted.output_width
        or video.get("height") != persisted.output_height
    ):
        mismatches.append("persisted_resolution")
    if (
        fps is None
        or persisted.output_fps is None
        or not math.isclose(fps, persisted.output_fps, abs_tol=0.001)
    ):
        mismatches.append("persisted_fps")
    if video.get("codec_name") != persisted.output_video_codec:
        mismatches.append("persisted_video_codec")
    if audio.get("codec_name") != persisted.output_audio_codec:
        mismatches.append("persisted_audio_codec")
    if str(audio.get("sample_rate")) != str(persisted.output_audio_sample_rate):
        mismatches.append("persisted_audio_sample_rate")
    if (
        duration is None
        or persisted.output_duration_seconds is None
        or not math.isclose(duration, persisted.output_duration_seconds, abs_tol=tolerance)
    ):
        mismatches.append("persisted_duration")
    return ProductionQACheck(
        "deep_ffprobe_validation",
        "render",
        "failed" if mismatches else "passed",
        "Independent ffprobe output does not match persisted/profile metadata."
        if mismatches
        else "Independent ffprobe output matches persisted/profile metadata.",
        actual={
            "mismatches": mismatches,
            "video_streams": len(videos),
            "audio_streams": len(audios),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": fps,
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "audio_sample_rate": audio.get("sample_rate"),
            "audio_channels": audio.get("channels"),
            "duration_seconds": duration,
        },
    )
