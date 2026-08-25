from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routers.workflow import executar_qa_report, obter_qa_report
from app.services.production_qa_service import (
    EnsureProductionQAResult,
    ProductionQANotFoundError,
)


def _report(workflow_id: uuid.UUID) -> SimpleNamespace:
    now = dt.datetime.now(dt.UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        workflow_execucao_id=workflow_id,
        workflow_render_id=uuid.uuid4(),
        production_timeline_id=uuid.uuid4(),
        visual_plan_id=uuid.uuid4(),
        narration_alignment_id=uuid.uuid4(),
        final_audio_sha256="a" * 64,
        render_output_sha256="b" * 64,
        qa_mode="standard",
        policy_name="game_analysis_qa_v1",
        policy_version="1",
        policy_identity="c" * 64,
        strategy_version="production_qa_v1",
        request_fingerprint="d" * 64,
        status="passed_with_warnings",
        publication_ready=True,
        check_count=2,
        passed_count=1,
        warning_count=1,
        failed_count=0,
        blocked_count=0,
        checks=[
            {
                "code": "artifact_chain_identity",
                "category": "artifact_chain",
                "result": "passed",
                "message": "ok",
                "expected": None,
                "actual": None,
                "details": None,
            }
        ],
        metrics={"total_qa_time_ms": 1.0},
        error_category=None,
        error_message=None,
        created_at=now,
        started_at=now,
        completed_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_get_qa_report_is_side_effect_free() -> None:
    workflow_id = uuid.uuid4()
    report = _report(workflow_id)
    service = MagicMock()
    service.current = AsyncMock(return_value=report)
    service.ensure = AsyncMock(side_effect=AssertionError("GET must not run QA"))
    with patch("app.api.routers.workflow.ProductionQAService", return_value=service):
        result = await obter_qa_report(workflow_id, MagicMock(), "standard")
    assert result.publication_ready is True
    service.current.assert_awaited_once_with(workflow_id, "standard")
    service.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_qa_report_returns_created_and_report() -> None:
    workflow_id = uuid.uuid4()
    report = _report(workflow_id)
    service = MagicMock()
    service.ensure = AsyncMock(return_value=EnsureProductionQAResult(report=report, created=True))
    with patch("app.api.routers.workflow.ProductionQAService", return_value=service):
        result = await executar_qa_report(workflow_id, MagicMock(), "standard", False)
    assert result.created is True
    assert result.report.id == report.id
    service.ensure.assert_awaited_once_with(workflow_id, mode="standard", force=False)


@pytest.mark.asyncio
async def test_invalid_workflow_returns_404() -> None:
    service = MagicMock()
    service.current = AsyncMock(side_effect=ProductionQANotFoundError("missing"))
    with (
        patch("app.api.routers.workflow.ProductionQAService", return_value=service),
        pytest.raises(HTTPException) as error,
    ):
        await obter_qa_report(uuid.uuid4(), MagicMock(), "standard")
    assert error.value.status_code == 404
