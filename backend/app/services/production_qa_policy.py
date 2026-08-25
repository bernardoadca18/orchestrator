"""Small profile-driven policy boundary for deterministic Stage 18 QA."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


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
    identity: str = field(init=False, default="")

    def __post_init__(self) -> None:
        payload = asdict(self)
        payload.pop("identity", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "identity", hashlib.sha256(encoded.encode()).hexdigest())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


GENERIC_POLICY = ProductionQAPolicy(name="generic_v1", version="1")

GAME_ANALYSIS_POLICY = ProductionQAPolicy(
    name="game_analysis_qa_v1",
    version="1",
    require_transcription=True,
    require_narration_alignment=True,
    require_visual_plan=True,
    require_required_visual_queries_resolved=True,
)


# These are the actual Ideas Hub production profile identifiers audited in Stage 18.
# The faceless profiles currently have no separate frozen visual-production contract,
# so they deliberately use the technical generic policy rather than guessed rules.
KNOWN_IDEAS_HUB_PROFILES = frozenset(
    {
        "faceless_general",
        "faceless_informational",
        "faceless_pop_culture",
        "faceless_pop_culture_lists",
        "game_analysis",
    }
)


def resolve_qa_policy(perfil_producao: str | None) -> ProductionQAPolicy:
    if perfil_producao == "game_analysis":
        return GAME_ANALYSIS_POLICY
    return GENERIC_POLICY
