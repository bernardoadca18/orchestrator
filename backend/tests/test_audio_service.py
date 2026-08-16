"""
Testes de unidade para audio_service.

Cobre:
- Fluxo feliz completo de processamento
- Bloco com erro permanente abortando o workflow
- Retry em erro transitório
- Concatenação de áudio com e sem silêncio
- Reconciliação de jobs órfãos (Fase 1)

NOTA: Estes testes usam mocks pesados para isolar a lógica.
Para testes com banco real, usar test_api_integration.py com Postgres.
"""
import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.voice_client import VoiceClientError, VoiceClientTransientError


class TestProcessarWorkflow:
    """Testes para processar_workflow usando mocks."""

    @pytest.mark.asyncio
    async def test_fluxo_feliz_completo(self, tmp_path):
        """
        Testa fluxo feliz: workflow com 2 blocos é processado corretamente,
        áudios são gerados e concatenados, status final é 'audio_concluido'.
        """
        workflow_id = uuid.uuid4()
        roteiro_snapshot = {
            "titulo": "Vídeo de Teste",
            "blocos": [
                {"tipo": "intro", "conteudo": "Primeiro bloco"},
                {"tipo": "desenvolvimento", "conteudo": "Segundo bloco"},
            ]
        }

        # Mock repository
        mock_repo = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = workflow_id
        mock_workflow.status = "agendada"
        mock_workflow.roteiro_snapshot = roteiro_snapshot

        mock_repo.obter_por_id = AsyncMock(return_value=mock_workflow)
        mock_repo.atualizar_status = AsyncMock()
        mock_repo.upsert_bloco = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        mock_repo.atualizar_bloco = AsyncMock()
        mock_repo.criar_transcricao = AsyncMock()

        # Mock voice_client
        mock_voice = MagicMock()
        mock_voice.iniciar_narracao = AsyncMock(return_value="job-123")
        mock_voice.aguardar_conclusao = AsyncMock(return_value={"status": "concluido", "job_id": "job-123"})
        mock_voice.baixar_audio = AsyncMock(return_value=b"fake_audio_bytes")

        # Mock ffmpeg_utils
        mock_concat = AsyncMock(return_value=tmp_path / "audio_final.wav")
        mock_duracao = AsyncMock(return_value=10.5)

        with patch("app.services.audio_service.WorkflowRepository", return_value=mock_repo), \
             patch("app.services.audio_service.voice_client", mock_voice), \
             patch("app.services.audio_service.settings") as mock_settings, \
             patch("app.services.audio_service.concatenar_com_silencio", mock_concat), \
             patch("app.services.audio_service.obter_duracao_segundos", mock_duracao), \
             patch("app.services.audio_service.transcrever_audio", return_value=("texto", [])), \
             patch("pathlib.Path.mkdir"):

            mock_settings.audio_storage_path = str(tmp_path)
            mock_settings.silencio_entre_blocos_ms = 500

            from app.services.audio_service import processar_workflow

            await processar_workflow(MagicMock(), workflow_id)

        # Verificar chamadas
        mock_repo.atualizar_status.assert_called()
        assert mock_repo.atualizar_status.call_count >= 2  # gerando_audio + audio_concluido

        # Verificar que blocos foram processados
        assert mock_repo.upsert_bloco.call_count == 2
        assert mock_voice.iniciar_narracao.call_count == 2

    @pytest.mark.asyncio
    async def test_bloco_erro_permanente_aborta_workflow(self, tmp_path):
        """
        Testa que um erro permanente (VoiceClientError) em um bloco
        aborta o workflow inteiro com status 'erro'.
        """
        workflow_id = uuid.uuid4()
        roteiro_snapshot = {
            "titulo": "Vídeo de Teste",
            "blocos": [{"tipo": "intro", "conteudo": "Primeiro bloco"}]
        }

        # Mock repository
        mock_repo = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = workflow_id
        mock_workflow.status = "agendada"
        mock_workflow.roteiro_snapshot = roteiro_snapshot

        mock_repo.obter_por_id = AsyncMock(return_value=mock_workflow)
        mock_repo.atualizar_status = AsyncMock()
        mock_repo.upsert_bloco = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        mock_repo.atualizar_bloco = AsyncMock()

        # Mock voice_client para lançar erro permanente
        mock_voice = MagicMock()
        mock_voice.iniciar_narracao = AsyncMock(side_effect=VoiceClientError("Texto inválido"))

        with patch("app.services.audio_service.WorkflowRepository", return_value=mock_repo), \
             patch("app.services.audio_service.voice_client", mock_voice), \
             patch("app.services.audio_service.settings") as mock_settings, \
             patch("pathlib.Path.mkdir"):

            mock_settings.audio_storage_path = str(tmp_path)

            from app.services.audio_service import processar_workflow

            await processar_workflow(MagicMock(), workflow_id)

        # Verificar status 'erro'
        status_calls = [call for call in mock_repo.atualizar_status.call_args_list]
        final_call = status_calls[-1] if status_calls else None

        assert final_call is not None
        assert final_call[0][1] == "erro"
        assert "Texto inválido" in str(final_call[1].get("erro_mensagem", ""))

    @pytest.mark.asyncio
    async def test_erro_transitorio_com_retry(self, tmp_path):
        """
        Testa que erro transitório é retentado pelo voice_client
        e eventualmente sucede.
        """
        workflow_id = uuid.uuid4()
        roteiro_snapshot = {
            "titulo": "Vídeo de Teste",
            "blocos": [{"tipo": "intro", "conteudo": "Primeiro bloco"}]
        }

        call_count = 0

        async def mock_iniciar(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise VoiceClientTransientError("Timeout temporário")
            return "job-123"

        # Mock repository
        mock_repo = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = workflow_id
        mock_workflow.status = "agendada"
        mock_workflow.roteiro_snapshot = roteiro_snapshot

        mock_repo.obter_por_id = AsyncMock(return_value=mock_workflow)
        mock_repo.atualizar_status = AsyncMock()
        mock_repo.upsert_bloco = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        mock_repo.atualizar_bloco = AsyncMock()
        mock_repo.criar_transcricao = AsyncMock()

        # Mock voice_client com retry
        mock_voice = MagicMock()
        mock_voice.iniciar_narracao = AsyncMock(side_effect=mock_iniciar)
        mock_voice.aguardar_conclusao = AsyncMock(return_value={"status": "concluido"})
        mock_voice.baixar_audio = AsyncMock(return_value=b"fake_audio")

        with patch("app.services.audio_service.WorkflowRepository", return_value=mock_repo), \
             patch("app.services.audio_service.voice_client", mock_voice), \
             patch("app.services.audio_service.settings") as mock_settings, \
             patch("app.services.audio_service.concatenar_com_silencio", AsyncMock()), \
             patch("app.services.audio_service.obter_duracao_segundos", AsyncMock(return_value=5.0)), \
             patch("app.services.audio_service.transcrever_audio", return_value=("texto", [])), \
             patch("pathlib.Path.mkdir"):

            mock_settings.audio_storage_path = str(tmp_path)
            mock_settings.silencio_entre_blocos_ms = 0

            from app.services.audio_service import processar_workflow

            await processar_workflow(MagicMock(), workflow_id)

        # Verificar que retry aconteceu (voice_client tem retry interno)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_workflow_cancelado_durante_processamento(self, tmp_path):
        """
        Testa que workflow cancelado tem processamento abortado.
        """
        workflow_id = uuid.uuid4()

        # Mock repository com workflow cancelado
        mock_repo = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = workflow_id
        mock_workflow.status = "cancelada"
        mock_workflow.roteiro_snapshot = {"blocos": []}

        mock_repo.obter_por_id = AsyncMock(return_value=mock_workflow)

        with patch("app.services.audio_service.WorkflowRepository", return_value=mock_repo), \
             patch("app.services.audio_service.settings") as mock_settings:

            mock_settings.audio_storage_path = str(tmp_path)

            from app.services.audio_service import processar_workflow

            await processar_workflow(MagicMock(), workflow_id)

        # Verificar: status não foi alterado (continua cancelada)
        mock_repo.atualizar_status.assert_not_called()


class TestConcatenacaoAudio:
    """Testes para ffmpeg_utils."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        True,  # Pular por padrão (requer ffmpeg instalado)
        reason="Requer ffmpeg instalado no sistema"
    )
    async def test_concatenacao_com_silencio(self, tmp_path):
        """
        Testa concatenação de múltiplos áudios com silêncio entre eles.
        """
        from app.services.ffmpeg_utils import concatenar_com_silencio, gerar_silencio, obter_duracao_segundos

        bloco1 = tmp_path / "bloco_00.wav"
        bloco2 = tmp_path / "bloco_01.wav"
        destino = tmp_path / "final.wav"

        await gerar_silencio(bloco1, 1000)
        await gerar_silencio(bloco2, 1000)

        resultado = await concatenar_com_silencio([bloco1, bloco2], 500, destino)

        assert resultado.exists()
        duracao = await obter_duracao_segundos(resultado)
        assert 2.4 <= duracao <= 2.6  # 1s + 0.5s + 1s

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        True,  # Pular por padrão (requer ffmpeg instalado)
        reason="Requer ffmpeg instalado no sistema"
    )
    async def test_concatenacao_sem_silencio(self, tmp_path):
        """
        Testa concatenação sem silêncio entre blocos (silencio_ms=0).
        """
        from app.services.ffmpeg_utils import concatenar_com_silencio, gerar_silencio, obter_duracao_segundos

        bloco1 = tmp_path / "bloco_00.wav"
        bloco2 = tmp_path / "bloco_01.wav"
        destino = tmp_path / "final.wav"

        await gerar_silencio(bloco1, 1000)
        await gerar_silencio(bloco2, 1000)

        resultado = await concatenar_com_silencio([bloco1, bloco2], 0, destino)

        assert resultado.exists()
        duracao = await obter_duracao_segundos(resultado)
        assert 1.9 <= duracao <= 2.1  # 1s + 1s


class TestReconciliacaoJobsOrfaos:
    """Testes para rotina de reconciliação de jobs órfãos."""

    @pytest.mark.asyncio
    async def test_reconciliacao_job_em_andamento(self, tmp_path):
        """
        Testa que a reconciliação detecta jobs que estavam em processamento
        quando o worker reiniciou e os retoma corretamente.
        """
        workflow_id = uuid.uuid4()
        bloco_id = uuid.uuid4()

        roteiro_snapshot = {
            "titulo": "Vídeo de Teste",
            "blocos": [{"tipo": "intro", "conteudo": "Primeiro bloco"}]
        }

        # Mock repository com bloco em estado 'gerando'
        mock_repo = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = workflow_id
        mock_workflow.status = "gerando_audio"
        mock_workflow.roteiro_snapshot = roteiro_snapshot

        # Simular bloco com job_id mas status 'gerando' (órfão)
        mock_bloco = MagicMock()
        mock_bloco.id = bloco_id
        mock_bloco.job_id_voz = "job-orfao-123"
        mock_bloco.ordem = 0
        mock_bloco.status = "gerando"

        mock_repo.obter_por_id = AsyncMock(return_value=mock_workflow)
        mock_repo.atualizar_status = AsyncMock()
        mock_repo.upsert_bloco = AsyncMock(return_value=mock_bloco)
        mock_repo.atualizar_bloco = AsyncMock()
        mock_repo.obter_bloco_por_job_id = AsyncMock(return_value=mock_bloco)
        mock_repo.criar_transcricao = AsyncMock()

        # Mock voice_client: job órfão foi encontrado e está concluído
        mock_voice = MagicMock()
        mock_voice.consultar_job = AsyncMock(return_value={"status": "concluido", "job_id": "job-orfao-123"})
        mock_voice.baixar_audio = AsyncMock(return_value=b"audio_bytes")
        mock_voice.aguardar_conclusao = AsyncMock(return_value={"status": "concluido"})

        with patch("app.services.audio_service.WorkflowRepository", return_value=mock_repo), \
             patch("app.services.audio_service.voice_client", mock_voice), \
             patch("app.services.audio_service.settings") as mock_settings, \
             patch("app.services.audio_service.concatenar_com_silencio", AsyncMock()), \
             patch("app.services.audio_service.obter_duracao_segundos", AsyncMock(return_value=3.0)), \
             patch("app.services.audio_service.transcrever_audio", return_value=("texto", [])), \
             patch("pathlib.Path.mkdir"):

            mock_settings.audio_storage_path = str(tmp_path)
            mock_settings.silencio_entre_blocos_ms = 0

            from app.services.audio_service import processar_workflow

            await processar_workflow(MagicMock(), workflow_id)

        # Verificar que bloco foi atualizado
        mock_repo.atualizar_bloco.assert_called()
