"""
Testes de integração da API do orchestrator.

Usa TestClient do FastAPI com banco Postgres de teste (via Docker).
Testa o fluxo completo: POST /workflow → mocks respondendo → validação final.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.repositories.workflow_repository import WorkflowRepository


# ============================================================================
# Testes de integração com banco real (Postgres)
# ============================================================================

class TestWorkflowAPIIntegration:
    """
    Testes de integração da API de workflows.

    NOTA: Estes testes requerem um banco Postgres de teste rodando.
    Para rodar localmente:

    docker run -d --name orchestrator-test-db \
        -e POSTGRES_USER=orchestrator_test \
        -e POSTGRES_PASSWORD=orchestrator_test \
        -e POSTGRES_DB=orchestrator_test \
        -p 5434:5432 \
        postgres:16

    DATABASE_URL=postgresql+asyncpg://orchestrator_test:orchestrator_test@localhost:5434/orchestrator_test
    """

    @pytest.fixture
    def client(self):
        """Cliente de teste do FastAPI."""
        return TestClient(app)

    @pytest.fixture
    def mock_ideas_hub_responses(self, respx_mock):
        """Configura respostas mock do ideas-hub."""
        roteiro_id = str(uuid.uuid4())
        ideia_id = str(uuid.uuid4())

        # GET /roteiros/ (listar aprovados)
        respx_mock.get("/roteiros/").mock(
            return_value=Response(200, json=[
                {
                    "id": roteiro_id,
                    "ideia_id": ideia_id,
                    "versao": 1,
                    "status": "aprovado",
                    "estrutura": {
                        "titulo": "Vídeo de Teste",
                        "duracao_total_segundos": 60,
                        "blocos": [
                            {"tipo": "intro", "conteudo": "Primeiro bloco"},
                            {"tipo": "desenvolvimento", "conteudo": "Segundo bloco"},
                        ]
                    }
                }
            ])
        )

        # GET /roteiros/{id}
        respx_mock.get(f"/roteiros/{roteiro_id}").mock(
            return_value=Response(200, json={
                "id": roteiro_id,
                "ideia_id": ideia_id,
                "versao": 1,
                "status": "aprovado",
                "estrutura": {
                    "titulo": "Vídeo de Teste",
                    "duracao_total_segundos": 60,
                    "blocos": [
                        {"tipo": "intro", "conteudo": "Primeiro bloco"},
                        {"tipo": "desenvolvimento", "conteudo": "Segundo bloco"},
                    ]
                }
            })
        )

        # GET /ideias/{id}
        respx_mock.get(f"/ideias/{ideia_id}").mock(
            return_value=Response(200, json={
                "id": ideia_id,
                "perfil_producao": "default"
            })
        )

        return {"roteiro_id": roteiro_id, "ideia_id": ideia_id}

    def test_listar_roteiros_disponiveis(self, client: TestClient, mock_ideas_hub_responses):
        """
        Testa GET /workflow/roteiros-disponiveis retornando roteiros aprovados.
        """
        with patch("app.clients.ideas_hub_client.ideas_hub_client") as mock_client:
            roteiro_id = mock_ideas_hub_responses["roteiro_id"]
            ideia_id = mock_ideas_hub_responses["ideia_id"]

            mock_client.listar_roteiros_aprovados = AsyncMock(return_value=[
                {
                    "id": roteiro_id,
                    "ideia_id": ideia_id,
                    "versao": 1,
                    "status": "aprovado",
                    "estrutura": {
                        "titulo": "Vídeo de Teste",
                        "duracao_total_segundos": 60,
                        "blocos": [
                            {"tipo": "intro", "conteudo": "Primeiro bloco"},
                        ]
                    }
                }
            ])

            response = client.get("/workflow/roteiros-disponiveis")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["titulo"] == "Vídeo de Teste"
        assert data[0]["num_blocos"] == 1

    def test_criar_workflow_sucesso(self, client: TestClient, mock_ideas_hub_responses):
        """
        Testa POST /workflow criando um novo workflow a partir de um roteiro aprovado.
        """
        roteiro_id = mock_ideas_hub_responses["roteiro_id"]
        ideia_id = mock_ideas_hub_responses["ideia_id"]

        with patch("app.api.routers.workflow.ideas_hub_client") as mock_hub, \
             patch("app.api.routers.workflow.gerar_audio_workflow") as mock_celery:

            mock_hub.obter_roteiro = AsyncMock(return_value={
                "id": roteiro_id,
                "ideia_id": ideia_id,
                "versao": 1,
                "status": "aprovado",
                "estrutura": {
                    "titulo": "Vídeo de Teste",
                    "duracao_total_segundos": 60,
                    "blocos": [{"tipo": "intro", "conteudo": "Texto"}]
                }
            })
            mock_hub.obter_ideia = AsyncMock(return_value={
                "id": ideia_id,
                "perfil_producao": "default"
            })

            mock_celery.delay = lambda x: None

            response = client.post("/workflow", json={"roteiro_id": roteiro_id})

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "agendada"
        assert data["roteiro_id"] == roteiro_id

    def test_criar_workflow_roteiro_nao_aprovado(self, client: TestClient):
        """
        Testa que criar workflow com roteiro não-aprovado retorna erro 400.
        """
        roteiro_id = str(uuid.uuid4())

        with patch("app.api.routers.workflow.ideas_hub_client") as mock_hub:
            mock_hub.obter_roteiro = AsyncMock(return_value={
                "id": roteiro_id,
                "ideia_id": str(uuid.uuid4()),
                "status": "rascunho",  # Não aprovado
                "estrutura": {"titulo": "Teste", "blocos": []}
            })

            response = client.post("/workflow", json={"roteiro_id": roteiro_id})

        assert response.status_code == 400
        assert "aprovado" in response.json()["detail"].lower()

    def test_listar_workflows_com_filtro(self, client: TestClient):
        """
        Testa GET /workflow com filtro de status.
        """
        with patch("app.api.routers.workflow.WorkflowRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.listar = AsyncMock(return_value=[
                {
                    "id": str(uuid.uuid4()),
                    "ideia_id": str(uuid.uuid4()),
                    "roteiro_id": str(uuid.uuid4()),
                    "status": "audio_concluido",
                    "audio_final_path": "/data/test.wav",
                    "duracao_audio_segundos": 30.0,
                    "erro_mensagem": None,
                    "tentativas": 0,
                    "criado_em": "2024-01-01T00:00:00",
                    "atualizado_em": "2024-01-01T00:00:01",
                }
            ])

            response = client.get("/workflow?status=audio_concluido")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_obter_workflow_detalhe(self, client: TestClient):
        """
        Testa GET /workflow/{id} retornando detalhes com blocos.
        """
        workflow_id = str(uuid.uuid4())

        with patch("app.api.routers.workflow.WorkflowRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.obter_por_id = AsyncMock(return_value={
                "id": workflow_id,
                "ideia_id": str(uuid.uuid4()),
                "roteiro_id": str(uuid.uuid4()),
                "status": "gerando_audio",
                "roteiro_snapshot": {"titulo": "Teste", "blocos": []},
                "audio_final_path": None,
                "duracao_audio_segundos": None,
                "erro_mensagem": None,
                "tentativas": 0,
                "total_blocos": 3,
                "blocos_concluidos": 1,
                "blocos_pendentes": 0,
                "blocos_gerando": 2,
                "blocos_erro": 0,
                "blocos": [
                    {
                        "id": str(uuid.uuid4()),
                        "ordem": 0,
                        "tipo": "intro",
                        "status": "concluido",
                        "duracao_segundos": 10.0,
                        "erro_mensagem": None,
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "ordem": 1,
                        "tipo": "desenv",
                        "status": "gerando",
                        "duracao_segundos": None,
                        "erro_mensagem": None,
                    },
                ],
                "criado_em": "2024-01-01T00:00:00",
                "atualizado_em": "2024-01-01T00:00:01",
            })

            response = client.get(f"/workflow/{workflow_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workflow_id
        assert data["total_blocos"] == 3
        assert data["blocos_concluidos"] == 1
        assert len(data["blocos"]) == 2

    def test_cancelar_workflow(self, client: TestClient):
        """
        Testa PATCH /workflow/{id}/cancelar cancelando um workflow pendente.
        """
        workflow_id = str(uuid.uuid4())

        with patch("app.api.routers.workflow.WorkflowRepository") as MockRepo, \
             patch("app.api.routers.workflow.celery_app") as mock_celery:

            mock_repo = MockRepo.return_value
            mock_repo.obter_por_id = AsyncMock(return_value={
                "id": workflow_id,
                "status": "agendada",
            })
            mock_repo.atualizar_status = AsyncMock()
            mock_repo.obter_por_id = AsyncMock(return_value={
                "id": workflow_id,
                "status": "cancelada",
            })

            mock_celery.control.revoke = lambda x, terminate=False: None

            response = client.patch(f"/workflow/{workflow_id}/cancelar")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelada"

    def test_repetir_workflow_com_erro(self, client: TestClient):
        """
        Testa PATCH /workflow/{id}/retry repetindo um workflow com erro.
        """
        workflow_id = str(uuid.uuid4())

        with patch("app.api.routers.workflow.WorkflowRepository") as MockRepo, \
             patch("app.api.routers.workflow.gerar_audio_workflow") as mock_celery:

            mock_repo = MockRepo.return_value

            # Primeira chamada: obter workflow
            mock_repo.obter_por_id = AsyncMock(return_value={
                "id": workflow_id,
                "status": "erro",
                "tentativas": 1,
                "max_tentativas": 3,
            })

            mock_repo.incrementar_tentativa = AsyncMock()
            mock_celery.delay = lambda x: None

            response = client.patch(f"/workflow/{workflow_id}/retry")

        assert response.status_code == 200

    def test_repetir_workflow_sem_erro_400(self, client: TestClient):
        """
        Testa que repetir workflow sem status 'erro' retorna 400.
        """
        workflow_id = str(uuid.uuid4())

        with patch("app.api.routers.workflow.WorkflowRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.obter_por_id = AsyncMock(return_value={
                "id": workflow_id,
                "status": "audio_concluido",  # Sem erro
            })

            response = client.patch(f"/workflow/{workflow_id}/retry")

        assert response.status_code == 400


class TestDashboardAPI:
    """Testes para endpoints de dashboard."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_obter_dashboard_stats(self, client: TestClient):
        """
        Testa GET /dashboard/stats retornando métricas agregadas.
        """
        with patch("app.api.routers.dashboard.WorkflowRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.obter_stats_dashboard = AsyncMock(return_value={
                "fila_tamanho": 5,
                "workflows_ativos": 2,
                "blocos_concluidos_24h": 45,
                "blocos_erro_24h": 3,
                "tempo_medio_bloco_segundos": 12.5,
                "taxa_erro_24h_percent": 6.25,
            })

            response = client.get("/dashboard/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["fila_tamanho"] == 5
        assert data["workflows_ativos"] == 2
        assert data["blocos_concluidos_24h"] == 45
        assert 0 <= data["taxa_erro_24h_percent"] <= 100
