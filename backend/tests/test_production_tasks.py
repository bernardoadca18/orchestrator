from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.services.production_orchestration_service import OrchestrationResult
from app.tasks import production_tasks


@asynccontextmanager
async def _session_context():
    yield MagicMock()


@pytest.mark.asyncio
async def test_poll_task_performs_one_get_and_schedules_one_future_poll() -> None:
    workflow_id = uuid.uuid4()
    render_id = uuid.uuid4()
    service = MagicMock()
    service.poll = AsyncMock(
        return_value=OrchestrationResult(terminal=False, poll_render_id=render_id)
    )
    with (
        patch("app.tasks.production_tasks.task_session", _session_context),
        patch(
            "app.tasks.production_tasks.ProductionOrchestrationService",
            return_value=service,
        ),
        patch.object(production_tasks.poll_video_render, "apply_async") as schedule,
    ):
        await production_tasks._poll(str(workflow_id), str(render_id))
    service.poll.assert_awaited_once()
    schedule.assert_called_once()


@pytest.mark.asyncio
async def test_restart_recovery_routes_render_to_poll_and_other_phase_to_process() -> None:
    render_workflow = SimpleNamespace(
        id=uuid.uuid4(), production_phase="render", production_status="running"
    )
    audio_workflow = SimpleNamespace(
        id=uuid.uuid4(), production_phase="audio", production_status="queued"
    )
    alignment_workflow = SimpleNamespace(
        id=uuid.uuid4(),
        production_phase="narration_alignment",
        production_status="queued",
    )
    repository = MagicMock()
    repository.recoverable_productions = AsyncMock(
        return_value=[render_workflow, audio_workflow, alignment_workflow]
    )
    current_render = SimpleNamespace(id=uuid.uuid4(), composer_render_id=str(uuid.uuid4()))
    service = MagicMock()
    service.current_render = AsyncMock(return_value=current_render)
    with (
        patch("app.tasks.production_tasks.task_session", _session_context),
        patch("app.tasks.production_tasks.WorkflowRepository", return_value=repository),
        patch(
            "app.tasks.production_tasks.ProductionOrchestrationService",
            return_value=service,
        ),
        patch.object(production_tasks.poll_video_render, "apply_async") as poll,
        patch.object(production_tasks.processar_producao_workflow, "delay") as process,
    ):
        await production_tasks.recover_productions()
    poll.assert_called_once()
    assert process.call_args_list == [
        call(str(audio_workflow.id)),
        call(str(alignment_workflow.id)),
    ]
