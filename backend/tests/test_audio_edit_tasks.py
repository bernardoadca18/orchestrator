from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks import audio_edit_tasks


@asynccontextmanager
async def _session_context():
    yield MagicMock()


@pytest.mark.asyncio
async def test_materialization_task_dispatches_downstream_once_only_for_claimed_work() -> None:
    workflow_id = uuid.uuid4()
    service = MagicMock()
    service.apply = AsyncMock(
        side_effect=[
            SimpleNamespace(
                performed=True,
                downstream_ready=True,
                workflow_id=workflow_id,
            ),
            SimpleNamespace(
                performed=False,
                downstream_ready=True,
                workflow_id=workflow_id,
            ),
        ]
    )
    with (
        patch("app.tasks.audio_edit_tasks.task_session", _session_context),
        patch(
            "app.tasks.audio_edit_tasks.AudioEditMaterializationService",
            return_value=service,
        ),
        patch.object(
            audio_edit_tasks.processar_producao_workflow, "delay"
        ) as downstream,
    ):
        await audio_edit_tasks._apply(str(uuid.uuid4()))
        await audio_edit_tasks._apply(str(uuid.uuid4()))

    downstream.assert_called_once_with(str(workflow_id))


@pytest.mark.asyncio
async def test_worker_recovery_requeues_each_durable_update() -> None:
    updates = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
    repository = MagicMock()
    repository.recoverable_audio_edit_updates = AsyncMock(return_value=updates)
    with (
        patch("app.tasks.audio_edit_tasks.task_session", _session_context),
        patch("app.tasks.audio_edit_tasks.WorkflowRepository", return_value=repository),
        patch.object(audio_edit_tasks.aplicar_edicoes_audio, "delay") as enqueue,
    ):
        await audio_edit_tasks.recover_audio_edit_updates()

    assert enqueue.call_count == 2
    enqueue.assert_any_call(str(updates[0].id))
    enqueue.assert_any_call(str(updates[1].id))
