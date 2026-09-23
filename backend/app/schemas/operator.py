"""Safe, compact contracts owned by Operator Console V1."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.workflow import PresentationSpecOut, ProductionTimelineOut


class ActionCapabilityOut(BaseModel):
    allowed: bool
    reason: str | None = None
    invalidates: list[str] = Field(default_factory=list)


class AttentionReasonOut(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    message: str


class RenderIdentityOut(BaseModel):
    id: uuid.UUID
    status: str
    phase: str
    progress: float = Field(ge=0, le=1)
    frames_rendered: int = Field(ge=0)
    total_frames: int | None = Field(default=None, gt=0)
    render_fingerprint: str | None
    output_sha256: str | None
    updated_at: dt.datetime


class QAIdentityOut(BaseModel):
    id: uuid.UUID
    status: str
    publication_ready: bool
    qa_mode: str
    request_fingerprint: str
    warning_count: int
    failed_count: int
    blocked_count: int


class HumanReviewRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_execucao_id: uuid.UUID
    workflow_render_id: uuid.UUID
    render_fingerprint: str
    output_sha256: str
    qa_report_id: uuid.UUID | None
    qa_request_fingerprint: str | None
    status: Literal["approved", "changes_requested", "rejected"]
    note: str | None
    operator_label: str | None
    created_at: dt.datetime


class HumanReviewStateOut(BaseModel):
    state: Literal[
        "unavailable",
        "pending",
        "approved",
        "changes_requested",
        "rejected",
        "stale",
    ]
    current: HumanReviewRecordOut | None = None
    latest_historical: HumanReviewRecordOut | None = None
    current_render: RenderIdentityOut | None = None


class OperatorWorkflowSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    script_duration_seconds: float | None
    audio_duration_seconds: float | None
    production_profile: str | None
    manual_edit_package_url: str | None = None
    visual_mode: str = "semantic_match"
    legacy_status: str
    production_status: str | None
    production_phase: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    current_error_category: str | None
    current_error_message: str | None
    attention: list[AttentionReasonOut] = Field(default_factory=list)
    capabilities: dict[str, ActionCapabilityOut]
    render: RenderIdentityOut | None
    qa: QAIdentityOut | None
    human_review: HumanReviewStateOut
    music_override_asset_id: str | None
    music_override_revision: int


class ArtifactIdentityOut(BaseModel):
    id: uuid.UUID
    fingerprint: str
    status: str


class OperatorArtifactIdentitiesOut(BaseModel):
    narration_alignment: ArtifactIdentityOut | None = None
    visual_plan: ArtifactIdentityOut | None = None
    production_timeline: ArtifactIdentityOut | None = None
    presentation_fingerprint: str | None = None
    audio_mix_plan_fingerprint: str | None = None


class OperatorWorkflowSnapshotOut(BaseModel):
    schema_version: Literal["operator_console_v1"] = "operator_console_v1"
    workflow: OperatorWorkflowSummaryOut
    artifacts: OperatorArtifactIdentitiesOut


class HumanReviewCreateIn(BaseModel):
    workflow_render_id: uuid.UUID
    render_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    qa_report_id: uuid.UUID | None = None
    qa_request_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["approved", "changes_requested", "rejected"]
    note: str | None = Field(default=None, max_length=4000)
    operator_label: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_note(self) -> HumanReviewCreateIn:
        self.note = self.note.strip() if self.note else None
        if self.status in {"changes_requested", "rejected"} and not self.note:
            raise ValueError("note is required for changes_requested and rejected")
        if self.operator_label is not None:
            self.operator_label = self.operator_label.strip() or None
        return self


class HumanReviewCreateOut(BaseModel):
    created: bool
    review: HumanReviewRecordOut


class MusicAssetCatalogOut(BaseModel):
    asset_id: str
    sha256: str
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str
    integrated_lufs: float
    true_peak_dbtp: float
    moods: list[str]
    styles: list[str]
    energy: float
    loopable: bool
    source_owner: str
    license_type: str
    monetization_allowed: bool
    attribution_required: bool
    attribution_text: str | None


class MusicCatalogOut(BaseModel):
    schema_version: Literal["operator_music_catalog_v1"] = "operator_music_catalog_v1"
    assets: list[MusicAssetCatalogOut]


class MusicOverrideIn(BaseModel):
    asset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,95}$")
    expected_revision: int = Field(ge=0)


class MusicOverrideClearIn(BaseModel):
    expected_revision: int = Field(ge=0)


class MusicOverrideOut(BaseModel):
    workflow_id: uuid.UUID
    asset_id: str | None
    revision: int
    changed: bool
    production_status: str | None
    production_phase: str | None


class ReviewQueueItemOut(BaseModel):
    workflow: OperatorWorkflowSummaryOut
    category: Literal[
        "final_video_review", "qa_warning_review", "changes_requested", "audio_attention"
    ]
    priority: int = Field(ge=1, le=4)


class WorkflowRenderHistoryOut(BaseModel):
    id: uuid.UUID
    status: str
    phase: str
    render_profile: str
    request_fingerprint: str
    render_fingerprint: str | None
    manifest_fingerprint: str | None
    output_sha256: str | None
    output_duration_seconds: float | None
    error_category: str | None
    error_message: str | None
    created_at: dt.datetime
    completed_at: dt.datetime | None


class QAHistoryOut(BaseModel):
    id: uuid.UUID
    workflow_render_id: uuid.UUID
    qa_mode: str
    status: str
    publication_ready: bool
    request_fingerprint: str
    warning_count: int
    failed_count: int
    blocked_count: int
    error_category: str | None
    error_message: str | None
    created_at: dt.datetime
    completed_at: dt.datetime | None


class OperatorWorkflowHistoryOut(BaseModel):
    workflow_id: uuid.UUID
    created_at: dt.datetime
    production_error_history: list[dict] = Field(default_factory=list)
    audio_edit_revision: int
    audio_materialized_revision: int
    renders: list[WorkflowRenderHistoryOut]
    qa_reports: list[QAHistoryOut]
    human_reviews: list[HumanReviewRecordOut]


class OperatorComposerInspectionOut(BaseModel):
    schema_version: Literal["operator_composer_inspection_v1"] = "operator_composer_inspection_v1"
    production_timeline: ProductionTimelineOut
    presentation: PresentationSpecOut | None
    audio_mix_plan: dict | None


class VoiceProfileOut(BaseModel):
    name: str
    clone_mode: str
    reference_id: str | None
    version: str
    identity: str


class VoiceProfilesOut(BaseModel):
    profiles: list[VoiceProfileOut]
    defaults: dict[str, str]


class VoicePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_profile: str = Field(min_length=1, max_length=100)
    language: Literal["Portuguese", "English"]
    text: str = Field(min_length=1, max_length=800)

    @field_validator("text")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Informe um texto para o preview")
        return value
