"""Fase 3: Adiciona perfil_producao em workflow_execucoes e cria tabela transcricoes_audio

Revision ID: 003
Revises: 002
Create Date: 2026-08-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Adicionar coluna perfil_producao em workflow_execucoes
    op.add_column(
        "workflow_execucoes",
        sa.Column("perfil_producao", sa.String(50), nullable=True)
    )

    # Criar tabela transcricoes_audio
    op.create_table(
        "transcricoes_audio",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True  # Um-para-um: cada workflow tem no máximo uma transcrição
        ),
        sa.Column("texto_completo", sa.Text, nullable=False),
        sa.Column("palavras_timestamps", postgresql.JSONB, nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_transcricoes_audio_workflow_execucao_id",
        "transcricoes_audio",
        ["workflow_execucao_id"],
        unique=True
    )


def downgrade() -> None:
    op.drop_table("transcricoes_audio")
    op.drop_column("workflow_execucoes", "perfil_producao")
