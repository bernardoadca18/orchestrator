"""Small profile-driven policy boundary for deterministic Stage 18 QA."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

from app.core.config import (
    STATIC_IMAGE_MAXIMUM_CONSECUTIVE_SECONDS,
    STATIC_IMAGE_MAXIMUM_HOLD_SECONDS,
    STATIC_IMAGE_MINIMUM_HOLD_SECONDS,
)


@dataclass(frozen=True, slots=True)
class ProductionQAPolicy:
    name: str
    version: str
    require_final_audio: bool = True
    require_transcription: bool = False
    require_narration_alignment: bool = False
    require_visual_plan: bool = False
    require_production_timeline: bool = True
    require_completed_render: bool = True
    require_gap_free_timeline: bool = True
    require_required_visual_queries_resolved: bool = False
    validate_mastering: bool = True
    validate_render_profile: bool = True
    validate_video_delivery: bool = True
    expected_audio_sample_rate: int = 48_000
    expected_audio_channels: int = 1
    alignment_coverage_warning_threshold: float = 0.60
    supported_source_types: tuple[str, ...] = ("gameplay",)
    static_image_minimum_hold_seconds: float | None = None
    static_image_maximum_hold_seconds: float | None = None
    static_image_maximum_consecutive_seconds: float | None = None
    identity: str = field(init=False, default="")

    def __post_init__(self) -> None:
        payload = asdict(self)
        payload.pop("identity", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "identity", hashlib.sha256(encoded.encode()).hexdigest())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


GENERIC_POLICY = ProductionQAPolicy(name="generic_v1", version="1")

GENERIC_HYBRID_POLICY = ProductionQAPolicy(
    name="generic_hybrid_v2",
    version="2",
    supported_source_types=("video_segment", "static_image"),
    static_image_minimum_hold_seconds=STATIC_IMAGE_MINIMUM_HOLD_SECONDS,
    static_image_maximum_hold_seconds=STATIC_IMAGE_MAXIMUM_HOLD_SECONDS,
    static_image_maximum_consecutive_seconds=STATIC_IMAGE_MAXIMUM_CONSECUTIVE_SECONDS,
)

GAME_ANALYSIS_POLICY = ProductionQAPolicy(
    name="game_analysis_qa_v1",
    version="1",
    require_transcription=True,
    require_narration_alignment=True,
    require_visual_plan=True,
    require_required_visual_queries_resolved=True,
)

GAME_ANALYSIS_HYBRID_POLICY = ProductionQAPolicy(
    name="game_analysis_qa_v2",
    version="2",
    require_transcription=True,
    require_narration_alignment=True,
    require_visual_plan=True,
    require_required_visual_queries_resolved=True,
    supported_source_types=("video_segment", "static_image"),
    static_image_minimum_hold_seconds=STATIC_IMAGE_MINIMUM_HOLD_SECONDS,
    static_image_maximum_hold_seconds=STATIC_IMAGE_MAXIMUM_HOLD_SECONDS,
    static_image_maximum_consecutive_seconds=STATIC_IMAGE_MAXIMUM_CONSECUTIVE_SECONDS,
)


# These are the actual Ideas Hub production profile identifiers audited in Stage 18.
# Faceless VisualQuery readiness is enforced upstream by Visual Director; these
# profiles still use the technical generic QA checks defined here.
KNOWN_IDEAS_HUB_PROFILES = frozenset(
    {
        "faceless_general",
        "faceless_informational",
        "faceless_pop_culture",
        "faceless_pop_culture_lists",
        "game_analysis",
    }
)


def resolve_qa_policy(
    perfil_producao: str | None,
    timeline_schema_version: str | None = None,
    *,
    static_image_minimum_hold_seconds: float = STATIC_IMAGE_MINIMUM_HOLD_SECONDS,
    static_image_maximum_hold_seconds: float = STATIC_IMAGE_MAXIMUM_HOLD_SECONDS,
    static_image_maximum_consecutive_seconds: float = (STATIC_IMAGE_MAXIMUM_CONSECUTIVE_SECONDS),
) -> ProductionQAPolicy:
    hybrid = timeline_schema_version == "production_timeline_schema_v2"
    if perfil_producao == "game_analysis":
        base = GAME_ANALYSIS_HYBRID_POLICY if hybrid else GAME_ANALYSIS_POLICY
    else:
        base = GENERIC_HYBRID_POLICY if hybrid else GENERIC_POLICY
    if not hybrid:
        return base
    return replace(
        base,
        static_image_minimum_hold_seconds=static_image_minimum_hold_seconds,
        static_image_maximum_hold_seconds=static_image_maximum_hold_seconds,
        static_image_maximum_consecutive_seconds=(static_image_maximum_consecutive_seconds),
    )
