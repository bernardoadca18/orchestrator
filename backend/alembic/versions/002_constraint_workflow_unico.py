"""constraint parcial: workflow ativo único por roteiro

Revision ID: 002
Revises: 001
Create Date: 2026-08-15

"""
from alembic import op


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Constraint parcial que previne dois workflows ativos simultâneos
    # para o mesmo roteiro_id. Status ativos: agendada, gerando_audio, audio_concluido
    op.execute("""
        CREATE UNIQUE INDEX uq_workflow_roteiro_ativo
        ON workflow_execucoes (roteiro_id)
        WHERE status IN ('agendada', 'gerando_audio', 'audio_concluido')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_workflow_roteiro_ativo")
