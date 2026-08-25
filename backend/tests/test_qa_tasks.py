from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.services.production_orchestration_service import OrchestrationResult
from app.tasks import production_tasks, qa_tasks


@asynccontextmanager
async def _session_context():
    yield MagicMock()


@pytest.mark.asyncio
async def test_completed_stage17_poll_enqueues_standard_qa_once() -> None:
    workflow_id = uuid.uuid4()
    render_id = uuid.uuid4()
    service = MagicMock()
    service.poll = AsyncMock(
        return_value=OrchestrationResult(terminal=True, qa_workflow_id=workflow_id)
    )
    with (
        patch("app.tasks.production_tasks.task_session", _session_context),
        patch(
            "app.tasks.production_tasks.ProductionOrchestrationService",
            return_value=service,
        ),
        patch.object(qa_tasks.executar_qa_producao, "delay") as enqueue,
    ):
        await production_tasks._poll(str(workflow_id), str(render_id))
    enqueue.assert_called_once_with(str(workflow_id))


@pytest.mark.asyncio
async def test_qa_recovery_enqueues_only_completed_workflow_without_current_report() -> None:
    missing = SimpleNamespace(id=uuid.uuid4())
    interrupted = SimpleNamespace(id=uuid.uuid4())
    passed = SimpleNamespace(id=uuid.uuid4())
    failed = SimpleNamespace(id=uuid.uuid4())
    repository = MagicMock()
    repository.completed_productions = AsyncMock(
        return_value=[missing, interrupted, passed, failed]
    )
    service = MagicMock()
    service.current = AsyncMock(
        side_effect=[
            None,
            SimpleNamespace(status="running"),
            SimpleNamespace(status="passed"),
            SimpleNamespace(status="failed"),
        ]
    )
    with (
        patch("app.tasks.qa_tasks.task_session", _session_context),
        patch("app.tasks.qa_tasks.WorkflowRepository", return_value=repository),
        patch("app.tasks.qa_tasks.ProductionQAService", return_value=service),
        patch.object(qa_tasks.executar_qa_producao, "delay") as enqueue,
    ):
        await qa_tasks.recover_standard_qa()
    assert enqueue.call_args_list == [
        call(str(missing.id)),
        call(str(interrupted.id)),
    ]


@pytest.mark.asyncio
async def test_task_execution_never_requests_deep_mode_automatically() -> None:
    workflow_id = uuid.uuid4()
    service = MagicMock()
    service.ensure = AsyncMock(
        return_value=SimpleNamespace(report=SimpleNamespace(status="passed", error_category=None))
    )
    with (
        patch("app.tasks.qa_tasks.task_session", _session_context),
        patch("app.tasks.qa_tasks.ProductionQAService", return_value=service),
    ):
        status, category = await qa_tasks._execute(str(workflow_id), force=False)
    assert (status, category) == ("passed", None)
    service.ensure.assert_awaited_once_with(workflow_id, mode="standard", force=False)
