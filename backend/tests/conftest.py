"""
Fixtures centrais para testes do orchestrator.

- Mocka ideas_hub_client e voice_client com respx (sem precisar dos serviços reais).
- Fornece sessão de banco em memória para testes de unidade.
- Setup de banco Postgres real para testes de integração.
"""
import asyncio
import uuid
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.repositories.workflow_repository import WorkflowRepository


# ============================================================================
# Fixtures de banco (unitário vs integração)
# ============================================================================

@pytest.fixture
async def db_session_memory() -> AsyncGenerator[AsyncSession, None]:
    """
    Sessão SQLite em memória para testes de unidade.
    Mais rápida que Postgres, mas não testa SQL específico.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )

    # Criar tabelas
    async with engine.begin() as conn:
        # Ler schema das migrations ou models
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS workflow_execucao (
                id TEXT PRIMARY KEY,
                ideia_id TEXT NOT NULL,
                roteiro_id TEXT NOT NULL UNIQUE,
                roteiro_snapshot JSON NOT NULL,
                perfil_producao TEXT,
                status TEXT NOT NULL DEFAULT 'agendada',
                audio_final_path TEXT,
                duracao_audio_segundos REAL,
                erro_mensagem TEXT,
                tentativas INTEGER DEFAULT 0,
                max_tentativas INTEGER DEFAULT 3,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audio_bloco_execucao (
                id TEXT PRIMARY KEY,
                workflow_execucao_id TEXT NOT NULL,
                ordem INTEGER NOT NULL,
                tipo TEXT,
                texto TEXT NOT NULL,
                job_id_voz TEXT,
                status TEXT NOT NULL DEFAULT 'pendente',
                audio_path TEXT,
                duracao_segundos REAL,
                erro_mensagem TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workflow_execucao_id) REFERENCES workflow_execucao(id)
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transcricao (
                id TEXT PRIMARY KEY,
                workflow_execucao_id TEXT NOT NULL UNIQUE,
                texto_completo TEXT NOT NULL,
                palavras JSON NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workflow_execucao_id) REFERENCES workflow_execucao(id)
            )
        """))

    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as session:
        yield session


@pytest.fixture
def repo(db_session_memory: AsyncSession) -> WorkflowRepository:
    """Repository com sessão de teste."""
    return WorkflowRepository(db_session_memory)


# ============================================================================
# Fixtures de mock HTTP (respx)
# ============================================================================

@pytest.fixture
def respx_mock():
    """Fixture para mockar chamadas HTTP com respx."""
    with respx.mock(base_url=settings.ideas_hub_api_url) as mock:
        yield mock


@pytest.fixture
def mock_ideas_hub(respx_mock):
    """
    Mock do ideas_hub_client com respx.
    Retorna um objeto para configurar respostas específicas.
    """
    class MockIdeasHub:
        def __init__(self):
            self._roteiros = {}
            self._ideias = {}

        def add_roteiro_aprovado(self, roteiro_id: uuid.UUID, ideia_id: uuid.UUID,
                                  titulo: str = "Roteiro Teste", blocos: list = None):
            blocos = blocos or [{"tipo": "intro", "conteudo": "Texto do bloco"}]
            self._roteiros[str(roteiro_id)] = {
                "id": str(roteiro_id),
                "ideia_id": str(ideia_id),
                "versao": 1,
                "status": "aprovado",
                "estrutura": {
                    "titulo": titulo,
                    "duracao_total_segundos": 60,
                    "blocos": blocos,
                }
            }
            self._ideias[str(ideia_id)] = {
                "id": str(ideia_id),
                "perfil_producao": "default"
            }
            self._setup_routes()

        def _setup_routes(self):
            # Limpar rotas anteriores
            respx_mock.reset()

            # GET /roteiros/ (listar aprovados)
            respx_mock.get("/roteiros/").mock(
                return_value=Response(200, json=[
                    r for r in self._roteiros.values() if r["status"] == "aprovado"
                ])
            )

            # GET /roteiros/{id}
            def get_roteiro(request, roteiro_id):
                if roteiro_id in self._roteiros:
                    return Response(200, json=self._roteiros[roteiro_id])
                return Response(404, json={"detail": "Roteiro não encontrado"})

            respx_mock.get("/roteiros/{roteiro_id}").mock(side_effect=get_roteiro)

            # GET /ideias/{id}
            def get_ideia(request, ideia_id):
                if ideia_id in self._ideias:
                    return Response(200, json=self._ideias[ideia_id])
                return Response(404, json={"detail": "Ideia não encontrada"})

            respx_mock.get("/ideias/{ideia_id}").mock(side_effect=get_ideia)

    return MockIdeasHub()


@pytest.fixture
def mock_voice_client():
    """
    Mock do voice_client usando patch.
    Para testes de unidade do audio_service.
    """
    with patch("app.services.audio_service.voice_client") as mock:
        mock.iniciar_narracao = AsyncMock(return_value="job-123")
        mock.aguardar_conclusao = AsyncMock(return_value={"status": "concluido", "job_id": "job-123"})
        mock.baixar_audio = AsyncMock(return_value=b"fake_audio_bytes")
        mock.consultar_job = AsyncMock(return_value={"status": "concluido", "job_id": "job-123"})
        yield mock


# ============================================================================
# Fixtures de dados de teste
# ============================================================================

@pytest.fixture
def roteiro_exemplo():
    """Roteiro de exemplo para testes."""
    return {
        "id": str(uuid.uuid4()),
        "ideia_id": str(uuid.uuid4()),
        "versao": 1,
        "status": "aprovado",
        "estrutura": {
            "titulo": "Vídeo de Teste",
            "duracao_total_segundos": 120,
            "blocos": [
                {"tipo": "intro", "conteudo": "Primeiro bloco do vídeo"},
                {"tipo": "desenvolvimento", "conteudo": "Segundo bloco com mais conteúdo"},
                {"tipo": "conclusao", "conteudo": "Terceiro bloco finalizando"},
            ],
        }
    }


@pytest.fixture
def workflow_exemplo():
    """Workflow de exemplo para testes."""
    return {
        "id": str(uuid.uuid4()),
        "ideia_id": str(uuid.uuid4()),
        "roteiro_id": str(uuid.uuid4()),
        "roteiro_snapshot": {
            "titulo": "Teste",
            "blocos": [{"tipo": "intro", "conteudo": "Texto"}]
        },
        "status": "agendada",
    }
