"""schema inicial: workflow_execucoes, audio_blocos

Revision ID: 001
Revises:
Create Date: 2026-08-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    status_workflow = postgresql.ENUM(
        "agendada", "gerando_audio", "audio_concluido", "erro", "cancelada", name="status_workflow"
    )
    status_audio_bloco = postgresql.ENUM("pendente", "gerando", "concluido", "erro", name="status_audio_bloco")

    bind = op.get_bind()
    status_workflow.create(bind, checkfirst=True)
    status_audio_bloco.create(bind, checkfirst=True)

    op.create_table(
        "workflow_execucoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ideia_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roteiro_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roteiro_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("status", status_workflow, nullable=False, server_default="agendada"),
        sa.Column("audio_final_path", sa.String(500), nullable=True),
        sa.Column("duracao_audio_segundos", sa.Float, nullable=True),
        sa.Column("erro_mensagem", sa.Text, nullable=True),
        sa.Column("tentativas", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_tentativas", sa.Integer, nullable=False, server_default="3"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_workflow_execucoes_ideia_id", "workflow_execucoes", ["ideia_id"])
    op.create_index("ix_workflow_execucoes_roteiro_id", "workflow_execucoes", ["roteiro_id"])

    op.create_table(
        "audio_blocos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("ordem", sa.Integer, nullable=False),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("texto", sa.Text, nullable=False),
        sa.Column("job_id_voz", sa.String(64), nullable=True),
        sa.Column("status", status_audio_bloco, nullable=False, server_default="pendente"),
        sa.Column("audio_path", sa.String(500), nullable=True),
        sa.Column("duracao_segundos", sa.Float, nullable=True),
        sa.Column("erro_mensagem", sa.Text, nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workflow_execucao_id", "ordem", name="uq_audio_bloco_ordem"),
    )
    op.create_index("ix_audio_blocos_workflow_execucao_id", "audio_blocos", ["workflow_execucao_id"])


def downgrade() -> None:
    op.drop_table("audio_blocos")
    op.drop_table("workflow_execucoes")
    op.execute("DROP TYPE IF EXISTS status_audio_bloco")
    op.execute("DROP TYPE IF EXISTS status_workflow")
