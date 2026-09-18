# Versioned wire contract mirrored by thumbnail-director; no runtime cross-service import.
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRATEGY = "thumbnail_director_v2"
PROMPT = "thumbnail_concepts_v2"
TEMPLATES = "thumbnail_templates_v1"
RENDERER = "libvips_thumbnail_v1"
Template = Literal["split_compare_v1", "hero_focus_v1", "curiosity_focus_v1"]
FAMILIES = dict(A="split_compare_v1", B="hero_focus_v1", C="curiosity_focus_v1")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StaticScope(Strict):
    enabled: bool = True
    asset_classes: list[str] = Field(default_factory=list, max_length=8)
    collections: list[str] = Field(default_factory=list, max_length=8)
    source_owners: list[str] = Field(default_factory=list, max_length=8)
    source_classes: list[str] = Field(default_factory=list, max_length=8)


class MediaScope(Strict):
    project_id: UUID
    session_ids: list[UUID] = Field(default_factory=list, max_length=100)
    static_images: StaticScope = Field(default_factory=StaticScope)


class Block(Strict):
    order: int = Field(ge=0)
    type: str = Field(max_length=100)
    content: str = Field(min_length=1, max_length=50000)


class Script(Strict):
    title: str = Field(min_length=1, max_length=500)
    blocks: list[Block] = Field(min_length=1, max_length=200)


class ThumbnailRequest(Strict):
    schema_version: Literal["thumbnail_request_v1"] = "thumbnail_request_v1"
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    workflow_id: UUID
    project_id: UUID
    script_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_locale: Literal["pt-BR", "en-US"]
    channel_profile: Literal["seis_estrelas_pt_v1", "six_stars_en_v1"]
    video_title: str = Field(min_length=1, max_length=500)
    script: Script
    media_scope: MediaScope
    candidate_count: Literal[3] = 3
    creative_revision: int = Field(ge=0)
    strategy_version: Literal["thumbnail_director_v1", "thumbnail_director_v2"] = STRATEGY
    concept_prompt_version: Literal["thumbnail_concepts_v1", "thumbnail_concepts_v2"] = PROMPT
    template_set_version: Literal["thumbnail_templates_v1"] = TEMPLATES
    renderer_version: Literal["libvips_thumbnail_v1"] = RENDERER

    @model_validator(mode="after")
    def consistent(self):
        from app.services.thumbnail_identity import digest

        expected = "seis_estrelas_pt_v1" if self.content_locale == "pt-BR" else "six_stars_en_v1"
        if self.channel_profile != expected or self.project_id != self.media_scope.project_id:
            raise ValueError("Inconsistent profile or media project")
        if (
            digest(self.model_dump(mode="json", exclude={"request_fingerprint"}))
            != self.request_fingerprint
        ):
            raise ValueError("Request fingerprint does not match payload")
        return self


class Concept(Strict):
    candidate_id: Literal["A", "B", "C"]
    template_id: Template
    intent: str = Field(min_length=1, max_length=160)
    headline: str = Field(max_length=100)
    primary_asset_query: str = Field(min_length=3, max_length=300)
    secondary_asset_query: str = Field(max_length=300)
    primary_subject: str = Field(min_length=1, max_length=200)
    secondary_subject: str = Field(max_length=200)

    @model_validator(mode="after")
    def editorial(self):
        if len(self.headline.split()) > 5 or "\n" in self.headline:
            raise ValueError("Headline must have at most five words on one input line")
        if FAMILIES[self.candidate_id] != self.template_id:
            raise ValueError("Each candidate must use its assigned family")
        if not self.primary_asset_query.strip() or not self.primary_subject.strip():
            raise ValueError("Empty query or subject")
        if self.candidate_id == "A" and not self.secondary_asset_query.strip():
            raise ValueError("Comparison needs a secondary query")
        return self


class Concepts(Strict):
    concepts: list[Concept] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def unique(self):
        import re

        for c in self.concepts:
            subjects = [c.primary_subject] + (
                [c.secondary_subject] if c.candidate_id == "A" else []
            )
            if any(
                not text.strip()
                or len(text.split()) > 4
                or re.search(r"asset_|session_|media_|[a-f0-9]{8}-[a-f0-9]{4}", text, re.I)
                for text in subjects
            ):
                raise ValueError("Use concrete visible subject names, never asset IDs")
        if {c.candidate_id for c in self.concepts} != set(FAMILIES):
            raise ValueError("Exactly A, B, C required")
        if len({(c.primary_subject.casefold(), c.intent.casefold()) for c in self.concepts}) != 3:
            raise ValueError("Concepts must be different")
        return self


class Artifact(Strict):
    artifact_id: str = Field(pattern=r"^thumb_[a-f0-9]{64}_[ABC]$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: Literal[1280] = 1280
    height: Literal[720] = 720
    format: Literal["png"] = "png"


class Source(Strict):
    media_id: UUID
    media_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    materialized_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_type: Literal["image", "video_frame"]
    filename: str
    session_id: UUID | None = None
    session_label: str | None = None
    game: str | None = None
    timestamp_seconds: float | None = None
    search_query: str
    rank: int
    score: float
    crop_box: list[int] = Field(default_factory=list)
    transforms: dict = Field(default_factory=dict)


class Candidate(Strict):
    candidate_id: Literal["A", "B", "C"]
    candidate_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    template_id: Template
    headline: str
    status: Literal["available", "unavailable"]
    error_category: str | None = None
    artifact: Artifact | None = None
    concept: Concept
    sources: list[Source]
    render_parameters: dict


class Package(Strict):
    schema_version: Literal["thumbnail_package_v1"] = "thumbnail_package_v1"
    request_fingerprint: str
    result_fingerprint: str
    strategy_version: Literal["thumbnail_director_v1", "thumbnail_director_v2"] = STRATEGY
    candidates: list[Candidate] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def consistent(self):
        from app.services.thumbnail_identity import digest

        if {c.candidate_id for c in self.candidates} != set(FAMILIES):
            raise ValueError("Exactly three candidate slots required")
        for c in self.candidates:
            if (
                c.template_id != FAMILIES[c.candidate_id]
                or c.concept.candidate_id != c.candidate_id
                or c.concept.template_id != c.template_id
                or c.headline != c.concept.headline
            ):
                raise ValueError("Candidate concept mismatch")
            if (c.status == "available") != (c.artifact is not None):
                raise ValueError("Candidate availability mismatch")
            if c.artifact and (
                c.error_category
                or not c.sources
                or c.artifact.artifact_id != f"thumb_{self.request_fingerprint}_{c.candidate_id}"
            ):
                raise ValueError("Artifact identity mismatch")
            if (
                digest(c.model_dump(mode="json", exclude={"candidate_identity"}))
                != c.candidate_identity
            ):
                raise ValueError("Candidate identity mismatch")
        if (
            self.result_fingerprint
            and digest(self.model_dump(mode="json", exclude={"result_fingerprint"}))
            != self.result_fingerprint
        ):
            raise ValueError("Package identity mismatch")
        return self
