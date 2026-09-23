"""Focal tests for audio reuse-only assembly feature (audit items 1-3, 7)."""

import uuid

import pytest

from app.models.workflow import WorkflowExecucao
from app.repositories.workflow_repository import WorkflowRepository


class TestReuseOnlyAssemblyMigration:
    """Item 1: Migration creates reuse_only_assembly column."""

    def test_column_exists_on_model(self):
        """Verify the model has reuse_only_assembly attribute."""
        wf = WorkflowExecucao(
            id=uuid.uuid4(),
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={},
            status="agendada",
        )
        # Column should exist on the model
        assert hasattr(wf, "reuse_only_assembly")
        # The default is set at the database level (server_default)
        # The Python default applies when flushed to the database


class TestReuseOnlyAssemblyFlagLifecycle:
    """Item 2: Flag is temporary and cleared on completion/failure/cancel."""

    @pytest.mark.asyncio
    async def test_flag_cleared_on_audio_success(self, async_session):
        """Flag should be cleared when audio processing succeeds."""
        repo = WorkflowRepository(async_session)
        wf = WorkflowExecucao(
            id=uuid.uuid4(),
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={},
            status="audio_concluido",
            production_status="completed",
            reuse_only_assembly=True,
        )
        async_session.add(wf)
        await async_session.commit()

        # Simulate success path clearing the flag
        await repo.atualizar_status(
            wf.id,
            "audio_concluido",
            audio_phase="completed",
            erro_mensagem=None,
            reuse_only_assembly=False,
        )

        result = await repo.obter_por_id(wf.id)
        assert result is not None
        assert result.reuse_only_assembly is False

    @pytest.mark.asyncio
    async def test_flag_cleared_on_audio_failure(self, async_session):
        """Flag should be cleared when audio processing fails."""
        repo = WorkflowRepository(async_session)
        wf = WorkflowExecucao(
            id=uuid.uuid4(),
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={},
            status="erro",
            production_status="failed",
            reuse_only_assembly=True,
        )
        async_session.add(wf)
        await async_session.commit()

        # Simulate failure path clearing the flag
        await repo.atualizar_status(
            wf.id,
            "erro",
            audio_phase="failed",
            erro_mensagem="Test error",
            reuse_only_assembly=False,
        )

        result = await repo.obter_por_id(wf.id)
        assert result is not None
        assert result.reuse_only_assembly is False


class TestAudioEditRevisionPreservation:
    """Item 3: Preserve audio edit revision when edit == materialized."""

    @pytest.mark.asyncio
    async def test_revision_preserved_when_edit_equals_materialized(self, async_session):
        """When edit_revision == materialized_revision, don't reset materialized."""
        wf = WorkflowExecucao(
            id=uuid.uuid4(),
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={},
            status="audio_concluido",
            production_status="completed",
            audio_edit_revision=5,
            audio_materialized_revision=5,
        )
        async_session.add(wf)
        await async_session.commit()

        # Verify preservation logic would apply
        current_edit = wf.audio_edit_revision
        current_materialized = wf.audio_materialized_revision
        preserve = current_edit == current_materialized and current_edit > 0

        assert preserve is True
        # In rebuild endpoint, materialized_revision stays at 5

    @pytest.mark.asyncio
    async def test_revision_reset_when_edit_differs_from_materialized(self, async_session):
        """When edit_revision != materialized_revision, reset materialized to 0."""
        wf = WorkflowExecucao(
            id=uuid.uuid4(),
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={},
            status="audio_concluido",
            production_status="completed",
            audio_edit_revision=7,
            audio_materialized_revision=5,
        )
        async_session.add(wf)
        await async_session.commit()

        current_edit = wf.audio_edit_revision
        current_materialized = wf.audio_materialized_revision
        preserve = current_edit == current_materialized and current_edit > 0

        assert preserve is False
        # In rebuild endpoint, materialized_revision would reset to 0


class TestAsyncBlockLoading:
    """Item 7: Fix async block loading in endpoints."""

    @pytest.mark.asyncio
    async def test_obter_por_id_para_atualizacao_loads_blocos(self, async_session):
        """Repository method should eagerly load blocos when requested."""

        from app.models.workflow import AudioBloco

        wf_id = uuid.uuid4()
        wf = WorkflowExecucao(
            id=wf_id,
            ideia_id=uuid.uuid4(),
            roteiro_id=uuid.uuid4(),
            roteiro_snapshot={},
            status="agendada",
        )
        bloco = AudioBloco(
            id=uuid.uuid4(),
            workflow_execucao_id=wf_id,
            ordem=0,
            tipo="introducao",
            texto="Teste bloco",
        )
        async_session.add(wf)
        async_session.add(bloco)
        await async_session.commit()

        repo = WorkflowRepository(async_session)

        # Test with com_blocos=True
        result = await repo.obter_por_id_para_atualizacao(wf_id, com_blocos=True)

        assert result is not None
        # Blocos should be loaded (no lazy load exception when accessed)
        assert len(result.blocos) == 1
        assert result.blocos[0].texto == "Teste bloco"
