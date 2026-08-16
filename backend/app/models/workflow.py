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

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ideia_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    roteiro_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    roteiro_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    perfil_producao: Mapped[str | None] = mapped_column(String(50), nullable=True)

    status: Mapped[str] = mapped_column(
        PgEnum(
            "agendada", "gerando_audio", "audio_concluido", "erro", "cancelada",
            name="status_workflow", create_type=False,
        ),
        nullable=False,
        default="agendada",
    )
    audio_final_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duracao_audio_segundos: Mapped[float | None] = mapped_column(Float, nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    criado_em: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    blocos: Mapped[list["AudioBloco"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="AudioBloco.ordem"
    )
    transcricao: Mapped["TranscricaoAudio | None"] = relationship(
        back_populates="workflow", uselist=False, cascade="all, delete-orphan"
    )


class AudioBloco(Base):
    __tablename__ = "audio_blocos"
    __table_args__ = (UniqueConstraint("workflow_execucao_id", "ordem", name="uq_audio_bloco_ordem"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflow_execucoes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)  # introducao | desenvolvimento | conclusao | ...
    texto: Mapped[str] = mapped_column(Text, nullable=False)

    job_id_voz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        PgEnum("pendente", "gerando", "concluido", "erro", name="status_audio_bloco", create_type=False),
        nullable=False,
        default="pendente",
    )
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duracao_segundos: Mapped[float | None] = mapped_column(Float, nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)

    criado_em: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="blocos")


class TranscricaoAudio(Base):
    __tablename__ = "transcricoes_audio"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflow_execucoes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    texto_completo: Mapped[str] = mapped_column(Text, nullable=False)
    palavras_timestamps: Mapped[dict] = mapped_column(JSONB, nullable=False)

    criado_em: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="transcricao")
