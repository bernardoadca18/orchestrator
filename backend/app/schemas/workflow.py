"""Schemas Pydantic — contratos de entrada/saída da API."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class RoteiroDisponivelOut(BaseModel):
    """Exact immutable Ideas Hub revision available for operator selection."""

    id: uuid.UUID
    ideia_id: uuid.UUID
    versao: int
    titulo: str
    duracao_total_segundos: int
    num_blocos: int
    parent_roteiro_id: uuid.UUID | None = None
    revision_kind: str
    status: str
    created_at: dt.datetime | None = None
    is_latest: bool
    media_bound: bool


class MediaProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None


class MediaSessionOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    label: str
    status: str


class WorkflowMediaBindingIn(BaseModel):
    media_project_id: uuid.UUID
    media_session_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_sessions(self) -> WorkflowMediaBindingIn:
        if len(set(self.media_session_ids)) != len(self.media_session_ids):
            raise ValueError("media_session_ids must be unique")
        return self


class RevisionPreparationOut(BaseModel):
    roteiro_id: uuid.UUID
    parent_roteiro_id: uuid.UUID | None = None
    versao: int
    status: str
    production_readiness: dict[str, Any]


class WorkflowCreateIn(BaseModel):
    roteiro_id: uuid.UUID


class AudioActionIn(BaseModel):
    confirm: bool = False


class AudioBlocoTakeOut(BaseModel):
    """Public take history without internal filesystem paths."""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: uuid.UUID
    attempt_index: int
    kind: str
    status: str
    request_key: str | None
    job_id_voz: str | None
    idempotency_key: str | None
    requested_seed: int | None
    resolved_seed: int | None
    model_id: str | None
    model_revision: str | None
    voice_profile: str | None
    voice_profile_identity: str | None
    generation_profile: str | None
    generation_profile_identity: str | None
    voice_chunking_identity: str | None
    chunking_identity: str | None
    synthesis_identity: str | None
    canonical_text: str | None
    normalized_text: str | None
    synthesis_text: str | None
    pronunciation_glossary_identity: str | None
    pronunciation_replacements: list[dict[str, Any]] | None
    text_normalizer_version: str | None
    text_normalizer_identity: str | None
    error_category: str | None
    error_message: str | None
    error_diagnostics: dict | None
    audio_duration_seconds: float | None
    audio_sha256: str | None
    audio_byte_size: int | None
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_frame_count: int | None
    audio_subtype: str | None
    quality_status: str | None
    artifact_integrity: dict | None
    assembly_manifest_identity: str | None = None
    assembly_manifest: dict | None = None
    provider_metadata: dict | None
    content_quality: dict | None
    accent_quality: dict | None = None
    pause_diagnostics: dict | None
    is_current: bool
    created_at: dt.datetime
    completed_at: dt.datetime | None


class AudioBlocoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ordem: int
    tipo: str
    texto: str
    normalized_text: str | None
    status: str
    synthesis_text: str | None
    text_normalizer_version: str | None
    text_normalizer_identity: str | None
    pronunciation_glossary_identity: str | None
    pronunciation_replacements: list[dict[str, Any]] | None
    voice_profile: str | None
    generation_profile: str | None
    resolved_seed: int | None
    initial_resolved_seed: int | None
    voice_chunking_identity: str | None
    current_take_id: uuid.UUID | None
    requested_take_id: uuid.UUID | None
    generation_attempt: int
    retry_seed_strategy: str | None = None
    generation_attempt_history: list[dict] | None = None
    duracao_segundos: float | None
    audio_sha256: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_byte_size: int | None
    audio_frame_count: int | None
    audio_subtype: str | None
    artifact_integrity: dict | None
    assembly_manifest_identity: str | None = None
    assembly_manifest: dict | None = None
    pause_diagnostics: dict | None
    narration_start_seconds: float | None
    narration_end_seconds: float | None
    erro_mensagem: str | None
    audio_edit_revision: int = 0
    audio_materialized_revision: int = 0
    music_override_asset_id: str | None = None
    music_override_revision: int = 0
    takes: list[AudioBlocoTakeOut] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audio_edit_dirty(self) -> bool:
        return self.audio_edit_revision > self.audio_materialized_revision


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ideia_id: uuid.UUID
    roteiro_id: uuid.UUID
    perfil_producao: str | None
    status: str
    audio_final_path: str | None = Field(default=None, exclude=True)
    duracao_audio_segundos: float | None
    audio_phase: str
    audio_final_sha256: str | None
    audio_final_sample_rate: int | None
    audio_final_channels: int | None
    audio_final_byte_size: int | None
    audio_final_frame_count: int | None
    audio_final_subtype: str | None
    audio_integrity: dict | None
    narration_diagnostics: dict | None = Field(default=None, exclude=True)
    mastering_config: dict | None
    mastering_metrics: dict | None
    erro_mensagem: str | None
    tentativas: int
    production_status: str | None = None
    production_phase: str | None = None
    production_error_category: str | None = None
    production_error_message: str | None = None
    production_error_history: list[dict] | None = None
    production_started_at: dt.datetime | None = None
    production_completed_at: dt.datetime | None = None
    audio_edit_revision: int = 0
    audio_materialized_revision: int = 0
    criado_em: dt.datetime
    atualizado_em: dt.datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audio_available(self) -> bool:
        return bool(
            self.audio_final_path
            and self.audio_final_sha256
            and self.audio_final_byte_size
            and self.audio_final_byte_size > 0
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audio_edits_pending(self) -> bool:
        return self.audio_edit_revision > self.audio_materialized_revision


class AudioEditUpdateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_execucao_id: uuid.UUID
    idempotency_key: str
    target_revision: int
    status: str
    published_audio_sha256: str | None
    error_stage: str | None
    error_message: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    materialized_at: dt.datetime | None
    completed_at: dt.datetime | None
    updated_at: dt.datetime


class AudioEditApplyOut(BaseModel):
    workflow_id: uuid.UUID
    update_id: uuid.UUID | None
    status: str
    created: bool
    audio_edit_revision: int
    audio_materialized_revision: int
    audio_edits_pending: bool
    final_audio_sha256: str | None


class WorkflowDetailOut(WorkflowOut):
    blocos: list[AudioBlocoOut] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_blocos(self) -> int:
        return len(self.blocos)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocos_concluidos(self) -> int:
        return sum(1 for b in self.blocos if b.status == "concluido")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocos_pendentes(self) -> int:
        return sum(1 for b in self.blocos if b.status == "pendente")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocos_gerando(self) -> int:
        return sum(1 for b in self.blocos if b.status == "gerando")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocos_erro(self) -> int:
        return sum(1 for b in self.blocos if b.status == "erro")


# ============================================================================
# Pacote de Produção Visual (Fase 6)
# ============================================================================


class BlocoAudioPacoteOut(BaseModel):
    """Bloco de áudio no pacote de produção."""

    id: uuid.UUID
    ordem: int
    tipo: str
    texto: str | None = None
    normalized_text: str | None = None
    synthesis_text: str | None = None
    audio_url: str | None
    duracao_segundos: float | None
    sha256: str | None
    sample_rate: int | None
    channels: int | None
    byte_size: int | None
    narration_start_seconds: float | None
    narration_end_seconds: float | None
    voice_profile: str | None
    generation_profile: str | None
    resolved_seed: int | None
    initial_resolved_seed: int | None
    voice_chunking_identity: str | None
    generation_attempt: int
    text_normalizer_version: str | None
    text_normalizer_identity: str | None
    pronunciation_glossary_identity: str | None = None
    pronunciation_replacements: list[dict[str, Any]] | None = None
    retry_seed_strategy: str | None
    generation_attempt_history: list[dict] | None


class AudioPacoteOut(BaseModel):
    """Seção de áudio do pacote de produção."""

    url: str
    duracao_segundos: float | None
    sha256: str | None
    sample_rate: int | None
    channels: int | None
    byte_size: int | None
    mastering_config: dict | None
    mastering_metrics: dict | None
    blocos: list[BlocoAudioPacoteOut] = Field(default_factory=list)


class PalavraTimestampPacote(BaseModel):
    """Timestamp de uma palavra na transcrição."""

    palavra: str
    inicio: float
    fim: float


class TranscricaoPacoteOut(BaseModel):
    """Seção de transcrição do pacote de produção."""

    texto_completo: str
    palavras_timestamps: list[PalavraTimestampPacote] = Field(default_factory=list)
    audio_sha256: str | None
    provider: str | None
    model: str | None
    language: str | None


class VisualQueryPacote(BaseModel):
    """Structured gameplay-time request preserved exactly from Ideas Hub."""

    id: uuid.UUID
    order: int
    intent: str
    query: str
    narration_excerpt: str | None = None
    importance: str
    source_note_id: uuid.UUID | None = None
    preferred_session_id: uuid.UUID | None = None
    preferred_start_seconds: float | None = None
    preferred_end_seconds: float | None = None
    desired_duration_seconds: float | None = None
    notes: str | None = None


class BlocoRoteiroPacote(BaseModel):
    """Bloco do roteiro no pacote de produção."""

    ordem: int
    tipo: str
    conteudo: str
    display_title: str | None = Field(default=None, min_length=1, max_length=120)
    visual_queries: list[VisualQueryPacote] = Field(default_factory=list)


class RoteiroPacoteOut(BaseModel):
    """Seção de roteiro do pacote de produção."""

    titulo: str
    duracao_total_segundos: int
    blocos: list[BlocoRoteiroPacote] = Field(default_factory=list)
    media_context: MediaScopeOut | None = None


class StaticImageScopeOut(BaseModel):
    enabled: bool = False
    asset_classes: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    source_owners: list[str] = Field(default_factory=list)
    source_classes: list[str] = Field(default_factory=list)


class MediaScopeOut(BaseModel):
    """Minimal immutable Media Indexer scope preserved by Ideas Hub."""

    media_project_id: uuid.UUID | None = None
    media_session_ids: list[uuid.UUID] | None = None
    static_images: StaticImageScopeOut | None = None


class MetadataPacoteOut(BaseModel):
    """Metadata do pacote de produção."""

    criado_em: dt.datetime
    atualizado_em: dt.datetime
    tentativas: int


class NarrationTimelineAudioOut(BaseModel):
    sha256: str | None
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None


class NarrationBlockAlignmentOut(BaseModel):
    id: uuid.UUID
    audio_block_id: uuid.UUID
    block_order: int
    status: str
    narration_start_seconds: float
    narration_end_seconds: float
    canonical_token_count: int
    synthesis_token_count: int
    asr_word_count: int
    canonical_synthesis_coverage: float
    synthesis_asr_coverage: float
    overall_coverage: float
    diagnostics: dict | None = None


class VisualQueryNarrationBindingOut(BaseModel):
    id: uuid.UUID
    visual_query_id: uuid.UUID
    audio_block_id: uuid.UUID
    block_order: int
    query_order: int
    status: str
    narration_start_seconds: float | None
    narration_end_seconds: float | None
    excerpt_match_method: str | None
    excerpt_match_score: float
    speech_alignment_coverage: float
    binding_confidence: float
    canonical_start_char: int | None
    canonical_end_char: int | None
    reason: str | None


class NarrationTimelineOut(BaseModel):
    workflow_id: uuid.UUID
    narration_alignment_id: uuid.UUID
    transcription_id: uuid.UUID | None
    strategy_version: str
    fingerprint: str
    status: str
    audio: NarrationTimelineAudioOut
    block_count: int
    resolved_visual_queries: int
    unresolved_visual_queries: int
    ambiguous_visual_queries: int
    not_applicable_visual_queries: int
    metrics: dict | None
    error_message: str | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    created_at: dt.datetime
    blocks: list[NarrationBlockAlignmentOut] = Field(default_factory=list)
    visual_query_bindings: list[VisualQueryNarrationBindingOut] = Field(default_factory=list)


class NarrationAlignmentEnsureOut(BaseModel):
    created: bool
    timeline: NarrationTimelineOut


class VisualGameplaySourceSelectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal["visual_source_v1"] = "visual_source_v1"
    source_type: Literal["gameplay"] = "gameplay"
    project_id: uuid.UUID
    session_id: uuid.UUID
    media_id: uuid.UUID
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    source_duration_seconds: float = Field(gt=0)
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    selection_score: float = Field(ge=0, le=1)
    rank: int | None = Field(default=None, ge=1)
    session_label: str
    media_filename: str
    temporal_index_id: uuid.UUID | None = None
    retrieval_candidate_id: uuid.UUID | None = None
    transcript_preview: str | None = None
    matched_note_ids: list[uuid.UUID] = Field(default_factory=list)
    media_search_strategy: str | None = None
    duration_fit_ratio: float | None = Field(default=None, ge=0, le=1)
    provenance: dict = Field(default_factory=dict)


class VisualVideoSegmentSourceSelectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal["visual_source_v2"] = "visual_source_v2"
    source_type: Literal["video_segment"] = "video_segment"
    project_id: uuid.UUID
    session_id: uuid.UUID
    media_id: uuid.UUID
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    source_duration_seconds: float = Field(gt=0)
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    selection_score: float = Field(ge=0, le=1)
    rank: int | None = Field(default=None, ge=1)
    session_label: str
    media_filename: str
    temporal_index_id: uuid.UUID | None = None
    retrieval_candidate_id: uuid.UUID | None = None
    transcript_preview: str | None = None
    matched_note_ids: list[uuid.UUID] = Field(default_factory=list)
    media_search_strategy: str | None = None
    duration_fit_ratio: float | None = Field(default=None, ge=0, le=1)
    provenance: dict = Field(default_factory=dict)
    candidate_start_seconds: float = Field(ge=0)
    candidate_end_seconds: float = Field(gt=0)


class VisualStaticImageSourceSelectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal["visual_source_v2"] = "visual_source_v2"
    source_type: Literal["static_image"] = "static_image"
    project_id: uuid.UUID
    media_id: uuid.UUID
    image_semantic_index_id: uuid.UUID
    semantic_score: float = Field(ge=-1, le=1)
    selection_score: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    media_filename: str
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format_name: str
    semantic_preview: str | None = None
    duration_fit_ratio: None = None
    provenance: dict = Field(default_factory=dict)
    semantic_index: dict = Field(default_factory=dict)


VisualSourceSelectionOut = Annotated[
    VisualGameplaySourceSelectionOut
    | VisualVideoSegmentSourceSelectionOut
    | VisualStaticImageSourceSelectionOut,
    Field(discriminator="source_type"),
]


class VisualDecisionOut(BaseModel):
    id: uuid.UUID
    visual_query_id: uuid.UUID
    audio_block_id: uuid.UUID | None
    block_order: int
    query_order: int
    query: str
    intent: str
    importance: str
    source_note_id: uuid.UUID | None
    desired_duration_seconds: float | None
    narration_binding_status: str
    narration_start_seconds: float | None
    narration_end_seconds: float | None
    target_narration_duration_seconds: float | None
    source_resolution_status: str
    selection_strategy: str | None
    selected_source: VisualSourceSelectionOut | None
    alternatives: list[VisualSourceSelectionOut] = Field(default_factory=list)
    selection_score: float | None
    score_components: dict | None
    reason: str | None
    diagnostics: dict | None


class VisualPlanOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    narration_alignment_id: uuid.UUID | None
    strategy_version: str
    request_fingerprint: str
    fingerprint: str
    status: str
    media_scope: MediaScopeOut | None
    media_index_provenance: dict | None
    total_queries: int
    resolved_queries: int
    unresolved_queries: int
    failed_queries: int
    required_gaps: int
    optional_gaps: int
    direct_provenance_count: int
    media_search_count: int
    reused_source_count: int
    metrics: dict | None
    diagnostics: dict | None
    error_message: str | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    created_at: dt.datetime
    decisions: list[VisualDecisionOut] = Field(default_factory=list)


class VisualPlanEnsureOut(BaseModel):
    created: bool
    visual_plan: VisualPlanOut


class NarrationTrackOut(BaseModel):
    kind: str = "narration"
    timeline_start_seconds: float = Field(ge=0)
    timeline_end_seconds: float = Field(gt=0)
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    audio_url: str
    audio_sha256: str
    sample_rate: int | None
    channels: int | None
    byte_size: int | None


class TimelineGameplaySourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["gameplay"] = "gameplay"
    project_id: uuid.UUID
    session_id: uuid.UUID
    media_id: uuid.UUID
    candidate_start_seconds: float = Field(ge=0)
    candidate_end_seconds: float = Field(gt=0)
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    provenance: dict = Field(default_factory=dict)


class TimelineVideoSegmentSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["video_segment"] = "video_segment"
    project_id: uuid.UUID
    session_id: uuid.UUID
    media_id: uuid.UUID
    candidate_start_seconds: float = Field(ge=0)
    candidate_end_seconds: float = Field(gt=0)
    source_start_seconds: float = Field(ge=0)
    source_end_seconds: float = Field(gt=0)
    provenance: dict = Field(default_factory=dict)


class TimelineStaticImageSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["static_image"] = "static_image"
    project_id: uuid.UUID
    media_id: uuid.UUID
    image_semantic_index_id: uuid.UUID
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_byte_size: int = Field(ge=0)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    provenance: dict = Field(default_factory=dict)


TimelineSourceOut = Annotated[
    TimelineGameplaySourceOut | TimelineVideoSegmentSourceOut | TimelineStaticImageSourceOut,
    Field(discriminator="source_type"),
]


class StaticImagePresentationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: Literal["static_image_presentation_v1"]
    fit: Literal["cover", "contain"]
    background: Literal["black", "blurred_fill"]
    motion: Literal["none", "slow_zoom_in", "slow_zoom_out"]
    zoom_amount: float = Field(ge=0, le=0.1)


class TimelinePlacementOut(BaseModel):
    id: uuid.UUID
    order: int = Field(ge=0)
    visual_decision_id: uuid.UUID | None
    visual_decision_ids: list[uuid.UUID] = Field(default_factory=list)
    visual_query_ids: list[uuid.UUID] = Field(default_factory=list)
    timeline_start_seconds: float = Field(ge=0)
    timeline_end_seconds: float = Field(gt=0)
    source: TimelineSourceOut
    playback_rate: float | None = None
    presentation: StaticImagePresentationOut | None = None
    transition: str
    strategy: str
    importance: str
    diagnostics: dict | None

    @model_validator(mode="after")
    def validate_source_contract(self) -> TimelinePlacementOut:
        if isinstance(self.source, TimelineStaticImageSourceOut):
            if self.playback_rate is not None or self.presentation is None:
                raise ValueError("static images require presentation and forbid playback_rate")
        elif self.playback_rate != 1.0 or self.presentation is not None:
            raise ValueError("video sources require playback_rate=1 and forbid presentation")
        return self


class TimelineGapOut(BaseModel):
    id: uuid.UUID
    order: int = Field(ge=0)
    timeline_start_seconds: float = Field(ge=0)
    timeline_end_seconds: float = Field(gt=0)
    reason: str
    severity: str
    related_visual_query_ids: list[uuid.UUID] = Field(default_factory=list)
    diagnostics: dict | None


class TimelineQueryOutcomeOut(BaseModel):
    visual_decision_id: uuid.UUID
    visual_query_id: uuid.UUID
    importance: str
    requested_start_seconds: float | None
    requested_end_seconds: float | None
    requested_duration_seconds: float = Field(ge=0)
    placed_duration_seconds: float = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    status: str
    reason: str | None


class PrimaryVisualTrackOut(BaseModel):
    kind: str = "primary_visual"
    placements: list[TimelinePlacementOut] = Field(default_factory=list)


class ProductionTimelineTracksOut(BaseModel):
    narration: NarrationTrackOut | None
    primary_visual: PrimaryVisualTrackOut


class ProductionTimelineOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    narration_alignment_id: uuid.UUID | None
    visual_plan_id: uuid.UUID | None
    schema_version: str
    strategy_version: str
    validator_version: str
    request_fingerprint: str
    fingerprint: str
    status: str
    duration_seconds: float = Field(ge=0)
    policy: dict
    tracks: ProductionTimelineTracksOut
    gaps: list[TimelineGapOut] = Field(default_factory=list)
    query_placement_outcomes: list[TimelineQueryOutcomeOut] = Field(default_factory=list)
    visual_coverage_seconds: float = Field(ge=0)
    visual_gap_seconds: float = Field(ge=0)
    visual_coverage_ratio: float = Field(ge=0, le=1)
    placement_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    cut_count: int = Field(ge=0)
    unplaced_query_count: int = Field(ge=0)
    required_gap_seconds: float = Field(ge=0)
    preferred_gap_seconds: float = Field(ge=0)
    optional_gap_seconds: float = Field(ge=0)
    background_gap_seconds: float = Field(ge=0)
    average_shot_duration_seconds: float | None
    metrics: dict | None
    diagnostics: dict | None
    error_message: str | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    created_at: dt.datetime

    @model_validator(mode="after")
    def validate_schema_source_types(self) -> ProductionTimelineOut:
        placements = self.tracks.primary_visual.placements
        if self.schema_version == "production_timeline_schema_v2":
            if any(isinstance(item.source, TimelineGameplaySourceOut) for item in placements):
                raise ValueError("ProductionTimeline V2 forbids legacy gameplay sources")
        elif any(not isinstance(item.source, TimelineGameplaySourceOut) for item in placements):
            raise ValueError("ProductionTimeline V1 accepts only gameplay sources")
        return self


class ProductionTimelineEnsureOut(BaseModel):
    created: bool
    production_timeline: ProductionTimelineOut


class CaptionCueOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=85)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    preset: Literal["subtle_longform_v1"]

    @model_validator(mode="after")
    def validate_range_and_lines(self) -> CaptionCueOut:
        lines = self.text.split("\n")
        if self.end_seconds <= self.start_seconds or len(lines) > 2:
            raise ValueError("caption cue range/line count is invalid")
        if any(not line or len(line) > 42 for line in lines):
            raise ValueError("caption cue line exceeds its preset")
        return self


class CaptionsPresentationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: Literal["caption_policy_v1"]
    preset: Literal["subtle_longform_v1"]
    cues: list[CaptionCueOut] = Field(max_length=5000)

    @model_validator(mode="after")
    def validate_cue_order(self) -> CaptionsPresentationOut:
        pairs = zip(self.cues, self.cues[1:], strict=False)
        if any(right.start_seconds < left.end_seconds for left, right in pairs):
            raise ValueError("caption cues overlap")
        return self


class SectionTitlePresentationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=120)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    preset: Literal["minimal_section_v1"]

    @model_validator(mode="after")
    def validate_range(self) -> SectionTitlePresentationOut:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("section title range is invalid")
        return self


class BrandingDisabledOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["branding_disabled_v1"]
    enabled: Literal[False]


class BrandingTextWatermarkOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["text_watermark_v1"]
    enabled: Literal[True]
    text: str = Field(min_length=1, max_length=40)
    placement: Literal["top_left", "top_right", "bottom_left", "bottom_right"]
    opacity: float = Field(ge=0.2, le=0.8)


BrandingPresentationOut = Annotated[
    BrandingDisabledOut | BrandingTextWatermarkOut,
    Field(discriminator="preset"),
]


class TransitionPresentationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["fade_to_black"]
    boundary_seconds: float = Field(gt=0)
    duration_seconds: float = Field(ge=0.1, le=1.0)
    preset: Literal["section_boundary_fade_v1"]


class PresentationSpecOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["presentation_spec_v1"]
    strategy_version: Literal["longform_presentation_v1"]
    profile_id: Literal["longform_professional_v1"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    captions: CaptionsPresentationOut
    section_titles: list[SectionTitlePresentationOut] = Field(max_length=100)
    branding: BrandingPresentationOut
    transitions: list[TransitionPresentationOut] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_overlay_order(self) -> PresentationSpecOut:
        title_pairs = zip(self.section_titles, self.section_titles[1:], strict=False)
        if any(right.start_seconds < left.end_seconds for left, right in title_pairs):
            raise ValueError("section titles overlap")
        transition_pairs = zip(self.transitions, self.transitions[1:], strict=False)
        for left, right in transition_pairs:
            left_end = left.boundary_seconds + left.duration_seconds / 2
            right_start = right.boundary_seconds - right.duration_seconds / 2
            if right_start < left_end:
                raise ValueError("transitions overlap")
        return self


class ComposerRenderInputPackageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["composer_render_input_v1"]
    production_timeline: ProductionTimelineOut
    presentation: PresentationSpecOut | None


class ProgramNarrationInputOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0)
    sample_rate: Literal[48000]
    channels: Literal[1]


class ProgramMusicAssetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,95}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0)
    content_type: Literal["audio/wav", "audio/flac"]
    format: Literal["wav", "flac"]
    duration_seconds: float = Field(gt=0)
    sample_rate: int = Field(ge=8000, le=192000)
    channels: int = Field(ge=1, le=2)
    integrated_lufs: float = Field(ge=-70, le=0)
    true_peak_dbtp: float = Field(ge=-70, le=3)
    moods: list[str] = Field(max_length=32)
    styles: list[str] = Field(max_length=32)
    energy: float = Field(ge=0, le=1)
    vocals: bool
    loopable: bool
    source_owner: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=500)
    license_type: Literal["owned", "commissioned", "cc0", "royalty_free"]
    monetization_allowed: Literal[True]
    attribution_required: bool
    attribution_text: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_attribution(self) -> ProgramMusicAssetOut:
        if self.attribution_required and not (self.attribution_text or "").strip():
            raise ValueError("attribution text is required")
        return self


class ProgramMusicSegmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    gain_adjustment_db: float = Field(ge=-3, le=3)
    fade_in_seconds: float = Field(ge=0, le=2)
    fade_out_seconds: float = Field(ge=0, le=2)
    transition_preset: Literal["none_v1", "smooth_v1"]

    @model_validator(mode="after")
    def validate_range(self) -> ProgramMusicSegmentOut:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("music segment range is invalid")
        duration = self.end_seconds - self.start_seconds
        if self.fade_in_seconds + self.fade_out_seconds > duration:
            raise ValueError("music fades exceed segment duration")
        return self


class ProgramMusicDisabledOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Literal[False]


class ProgramMusicEnabledOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Literal[True]
    asset: ProgramMusicAssetOut
    source_start_seconds: Literal[0]
    source_end_seconds: float = Field(gt=0)
    loop_policy: Literal["no_loop_v1", "repeat_from_start_v1"]
    normalization_policy: Literal["music_loudnorm_v1"]
    normalization_target_lufs: float = Field(ge=-30, le=-16)
    segments: list[ProgramMusicSegmentOut] = Field(min_length=1, max_length=512)


ProgramMusicOut = Annotated[
    ProgramMusicDisabledOut | ProgramMusicEnabledOut,
    Field(discriminator="enabled"),
]


class SpeechRegionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> SpeechRegionOut:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("speech region range is invalid")
        return self


class ProgramDuckingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: Literal["speech_envelope_v1"]
    base_gain_db: float = Field(ge=-24, le=0)
    ducked_gain_db: float = Field(ge=-36, le=-6)
    attack_seconds: float = Field(gt=0, le=2)
    release_seconds: float = Field(gt=0, le=3)
    merge_gap_seconds: float = Field(ge=0, le=2)
    speech_regions: list[SpeechRegionOut] = Field(max_length=512)

    @model_validator(mode="after")
    def validate_levels_and_order(self) -> ProgramDuckingOut:
        if self.ducked_gain_db >= self.base_gain_db:
            raise ValueError("ducked gain must be below base gain")
        if any(
            right.start_seconds < left.end_seconds
            for left, right in zip(self.speech_regions, self.speech_regions[1:], strict=False)
        ):
            raise ValueError("speech regions overlap")
        return self


class ProgramMasterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: Literal["youtube_program_master_v1_candidate"]
    policy_version: Literal["ffmpeg_loudnorm_two_pass_v1"]
    target_lufs: float = Field(ge=-24, le=-10)
    max_true_peak_dbtp: float = Field(ge=-3, le=-0.1)
    target_lra: float = Field(ge=1, le=20)


class ProgramOutputAudioOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: Literal["youtube_1080p30_program_audio_v1"]
    codec: Literal["aac"]
    bitrate: Literal["192k"]
    sample_rate: Literal[48000]
    channels: Literal[2]


class AudioMixPlanV1Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["audio_mix_plan_v1"]
    strategy_version: Literal["longform_program_audio_v1"]
    profile_id: Literal["music_mix_profile_v1_candidate"]
    duration_seconds: float = Field(gt=0)
    narration: ProgramNarrationInputOut
    music: ProgramMusicOut
    ducking: ProgramDuckingOut
    program_master: ProgramMasterOut
    output: ProgramOutputAudioOut
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComposerRenderInputPackageV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["composer_render_input_v2"]
    production_timeline: ProductionTimelineOut
    presentation: PresentationSpecOut | None
    audio_mix_plan: AudioMixPlanV1Out


class WorkflowRenderOutputOut(BaseModel):
    sha256: str
    byte_size: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    frame_count: int | None = Field(default=None, gt=0)
    video_codec: str
    audio_codec: str
    audio_sample_rate: int = Field(gt=0)


class WorkflowRenderErrorOut(BaseModel):
    category: str
    message: str


class WorkflowRenderOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    production_timeline_id: uuid.UUID
    production_timeline_fingerprint: str
    composer_render_id: str | None
    render_profile: str
    mode: str
    status: str
    phase: str
    progress: float = Field(ge=0, le=1)
    frames_rendered: int = Field(ge=0)
    total_frames: int | None = Field(default=None, gt=0)
    render_profile_identity: str | None
    render_fingerprint: str | None
    manifest_fingerprint: str | None
    output: WorkflowRenderOutputOut | None
    metrics: dict | None
    error: WorkflowRenderErrorOut | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    video_url: str | None


class ProductionQACheckOut(BaseModel):
    code: str
    category: str
    result: str
    message: str
    expected: Any | None = None
    actual: Any | None = None
    details: dict | None = None


class ProductionQAReportOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_render_id: uuid.UUID
    production_timeline_id: uuid.UUID
    visual_plan_id: uuid.UUID | None
    narration_alignment_id: uuid.UUID | None
    final_audio_sha256: str
    render_output_sha256: str
    qa_mode: str
    policy_name: str
    policy_version: str
    policy_identity: str
    strategy_version: str
    request_fingerprint: str
    status: str
    publication_ready: bool
    check_count: int
    passed_count: int
    warning_count: int
    failed_count: int
    blocked_count: int
    checks: list[ProductionQACheckOut] = Field(default_factory=list)
    metrics: dict | None
    error_category: str | None
    error_message: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    updated_at: dt.datetime


class ProductionQAEnsureOut(BaseModel):
    created: bool
    report: ProductionQAReportOut


class PacoteProducaoOut(BaseModel):
    """
    Payload consolidado para handoff ao serviço de produção visual.

    Este endpoint agrega todos os dados necessários para o serviço visual
    iniciar a produção do vídeo: roteiro, áudio, transcrição e metadata.
    """

    workflow_id: uuid.UUID
    ideia_id: uuid.UUID
    roteiro_id: uuid.UUID

    status: str
    duracao_audio_segundos: float | None

    perfil_producao: str | None

    roteiro: RoteiroPacoteOut
    audio: AudioPacoteOut
    transcricao: TranscricaoPacoteOut | None = None
    narration_alignment: NarrationTimelineOut | None = None
    visual_plan: VisualPlanOut | None = None
    production_timeline: ProductionTimelineOut | None = None
    render: WorkflowRenderOut | None = None
    qa_report: ProductionQAReportOut | None = None
    metadata: MetadataPacoteOut
