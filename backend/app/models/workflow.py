"""
Modelos SQLAlchemy do orchestrator.

Design: o orchestrator NÃO tem FK de banco para o ideas-hub (são bancos
de serviços diferentes — FK cross-database não existe fisicamente).
`ideia_id` e `roteiro_id` são apenas UUIDs de referência externa.

`roteiro_snapshot` guarda uma cópia imutável da `estrutura` do roteiro
no momento em que ele entrou na fila — garante reprodutibilidade mesmo
se o roteiro for editado no ideas-hub depois, e evita uma tabela extra
de cache (o snapshot já cumpre esse papel).

`AudioBloco` materializa cada bloco narrativo como linha própria (em vez
de só ler do JSONB) porque cada bloco tem ciclo de vida e status próprios
durante a geração — é um compromisso de denormalização deliberado e
localizado, não um problema de modelagem.
"""

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WorkflowExecucao(Base):
    __tablename__ = "workflow_execucoes"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ideia_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    roteiro_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    roteiro_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    perfil_producao: Mapped[str | None] = mapped_column(String(50), nullable=True)

    status: Mapped[str] = mapped_column(
        PgEnum(
            "agendada",
            "gerando_audio",
            "audio_concluido",
            "erro",
            "cancelada",
            name="status_workflow",
            create_type=False,
        ),
        nullable=False,
        default="agendada",
    )
    audio_final_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duracao_audio_segundos: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_phase: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting")
    audio_final_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_final_sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_final_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_final_byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mastering_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mastering_metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    blocos: Mapped[list["AudioBloco"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="AudioBloco.ordem"
    )
    transcricao: Mapped["TranscricaoAudio | None"] = relationship(
        back_populates="workflow", uselist=False, cascade="all, delete-orphan"
    )
    narration_alignments: Mapped[list["NarrationAlignment"]] = relationship(
        back_populates="workflow", cascade="save-update, merge", passive_deletes="all"
    )
    visual_plans: Mapped[list["VisualPlan"]] = relationship(
        back_populates="workflow", cascade="save-update, merge", passive_deletes="all"
    )


class AudioBloco(Base):
    __tablename__ = "audio_blocos"
    __table_args__ = (
        UniqueConstraint("workflow_execucao_id", "ordem", name="uq_audio_bloco_ordem"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # introducao | desenvolvimento | conclusao | ...
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    synthesis_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synthesis_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synthesis_identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pronunciation_glossary_identity: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job_id_voz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    voice_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voice_profile_identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_profile_identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generation_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        PgEnum(
            "pendente", "gerando", "concluido", "erro", name="status_audio_bloco", create_type=False
        ),
        nullable=False,
        default="pendente",
    )
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duracao_segundos: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    narration_start_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    narration_end_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)

    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="blocos")


class TranscricaoAudio(Base):
    __tablename__ = "transcricoes_audio"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    texto_completo: Mapped[str] = mapped_column(Text, nullable=False)
    palavras_timestamps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    audio_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)

    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="transcricao")


class NarrationAlignment(Base):
    """Versioned derived alignment for one immutable final narration identity."""

    __tablename__ = "narration_alignments"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_narration_alignment_fingerprint"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'blocked')",
            name="ck_narration_alignment_status",
        ),
        CheckConstraint(
            "block_count >= 0 AND resolved_visual_queries >= 0 AND "
            "unresolved_visual_queries >= 0 AND ambiguous_visual_queries >= 0 AND "
            "not_applicable_visual_queries >= 0",
            name="ck_narration_alignment_counts",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transcription_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("transcricoes_audio.id", ondelete="SET NULL"),
        nullable=True,
    )
    final_audio_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    script_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    transcription_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    block_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_visual_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unresolved_visual_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ambiguous_visual_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    not_applicable_visual_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="narration_alignments")
    block_alignments: Mapped[list["NarrationBlockAlignment"]] = relationship(
        back_populates="alignment",
        cascade="save-update, merge",
        passive_deletes="all",
        order_by="NarrationBlockAlignment.block_order",
    )
    visual_query_bindings: Mapped[list["VisualQueryNarrationBinding"]] = relationship(
        back_populates="alignment",
        cascade="save-update, merge",
        passive_deletes="all",
        order_by="VisualQueryNarrationBinding.block_order, "
        "VisualQueryNarrationBinding.query_order",
    )


class NarrationBlockAlignment(Base):
    """Compact quality metrics for one editorial block; no ASR word duplication."""

    __tablename__ = "narration_block_alignments"
    __table_args__ = (
        UniqueConstraint("narration_alignment_id", "block_order", name="uq_block_alignment_order"),
        CheckConstraint(
            "narration_start_seconds >= 0 AND " "narration_end_seconds >= narration_start_seconds",
            name="ck_block_alignment_range",
        ),
        CheckConstraint(
            "canonical_synthesis_coverage >= 0 AND canonical_synthesis_coverage <= 1",
            name="ck_block_canonical_synthesis_coverage",
        ),
        CheckConstraint(
            "synthesis_asr_coverage >= 0 AND synthesis_asr_coverage <= 1",
            name="ck_block_synthesis_asr_coverage",
        ),
        CheckConstraint(
            "overall_coverage >= 0 AND overall_coverage <= 1",
            name="ck_block_overall_coverage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    narration_alignment_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("narration_alignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    audio_bloco_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("audio_blocos.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    narration_start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    narration_end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    canonical_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    synthesis_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    asr_word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_synthesis_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    synthesis_asr_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    overall_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    alignment: Mapped["NarrationAlignment"] = relationship(back_populates="block_alignments")


class VisualQueryNarrationBinding(Base):
    """Derived narration-time binding; gameplay provenance remains in the script."""

    __tablename__ = "visual_query_narration_bindings"
    __table_args__ = (
        UniqueConstraint(
            "narration_alignment_id", "visual_query_id", name="uq_alignment_visual_query"
        ),
        CheckConstraint(
            "status IN ('resolved', 'unresolved', 'ambiguous', 'not_applicable')",
            name="ck_visual_query_binding_status",
        ),
        CheckConstraint(
            "(narration_start_seconds IS NULL AND narration_end_seconds IS NULL) OR "
            "(narration_start_seconds >= 0 AND narration_end_seconds >= narration_start_seconds)",
            name="ck_visual_query_binding_range",
        ),
        CheckConstraint(
            "excerpt_match_score BETWEEN 0 AND 1 AND "
            "speech_alignment_coverage BETWEEN 0 AND 1 AND "
            "binding_confidence BETWEEN 0 AND 1",
            name="ck_visual_query_binding_scores",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    narration_alignment_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("narration_alignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    audio_bloco_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("audio_blocos.id", ondelete="CASCADE"), nullable=False
    )
    visual_query_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    query_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    narration_start_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    narration_end_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    excerpt_match_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    excerpt_match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    speech_alignment_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    binding_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    canonical_start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canonical_end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    alignment: Mapped["NarrationAlignment"] = relationship(back_populates="visual_query_bindings")


class VisualPlan(Base):
    """Versioned Stage 14 source-selection artifact; never a final edit timeline."""

    __tablename__ = "visual_plans"
    __table_args__ = (
        UniqueConstraint("request_fingerprint", name="uq_visual_plan_request_fingerprint"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', "
            "'completed_with_gaps', 'blocked', 'failed')",
            name="ck_visual_plan_status",
        ),
        CheckConstraint(
            "total_queries >= 0 AND resolved_queries >= 0 AND unresolved_queries >= 0 "
            "AND failed_queries >= 0 AND required_gaps >= 0 AND optional_gaps >= 0 "
            "AND direct_provenance_count >= 0 AND media_search_count >= 0 "
            "AND reused_source_count >= 0",
            name="ck_visual_plan_counts",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    narration_alignment_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("narration_alignments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    script_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    media_scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    media_scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    media_index_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    total_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unresolved_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_gaps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    optional_gaps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    direct_provenance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    media_search_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reused_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="visual_plans")
    decisions: Mapped[list["VisualDecision"]] = relationship(
        back_populates="visual_plan",
        cascade="save-update, merge",
        passive_deletes="all",
        order_by="VisualDecision.block_order, VisualDecision.query_order",
    )


class VisualDecision(Base):
    """One immutable VisualQuery interpreted as a source-media decision."""

    __tablename__ = "visual_decisions"
    __table_args__ = (
        UniqueConstraint("visual_plan_id", "visual_query_id", name="uq_plan_visual_query"),
        UniqueConstraint(
            "visual_plan_id", "block_order", "query_order", name="uq_plan_decision_order"
        ),
        CheckConstraint(
            "narration_binding_status IN "
            "('resolved', 'unresolved', 'ambiguous', 'not_applicable')",
            name="ck_visual_decision_binding_status",
        ),
        CheckConstraint(
            "source_resolution_status IN "
            "('resolved', 'unresolved', 'partial', 'unsupported', 'not_required', 'failed')",
            name="ck_visual_decision_source_status",
        ),
        CheckConstraint(
            "(narration_start_seconds IS NULL AND narration_end_seconds IS NULL) OR "
            "(narration_start_seconds >= 0 AND narration_end_seconds >= narration_start_seconds)",
            name="ck_visual_decision_narration_range",
        ),
        CheckConstraint(
            "selection_score IS NULL OR (selection_score >= 0 AND selection_score <= 1)",
            name="ck_visual_decision_score",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    visual_plan_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("visual_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visual_query_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    audio_bloco_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("audio_blocos.id", ondelete="SET NULL"),
        nullable=True,
    )
    block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    query_order: Mapped[int] = mapped_column(Integer, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    importance: Mapped[str] = mapped_column(String(24), nullable=False)
    source_note_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    desired_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    narration_binding_status: Mapped[str] = mapped_column(String(24), nullable=False)
    narration_start_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    narration_end_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_narration_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_resolution_status: Mapped[str] = mapped_column(String(24), nullable=False)
    selection_strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    selected_source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    alternatives: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    selection_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_components: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    visual_plan: Mapped["VisualPlan"] = relationship(back_populates="decisions")
