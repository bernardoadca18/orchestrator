from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.repositories.workflow_repository import (
    WorkflowRepository,
    evolve_production_error_history,
)
from app.services.production_orchestration_service import ProductionOrchestrationService


def test_failure_then_success_clears_active_error_and_preserves_recovered_history() -> None:
    failed_at = dt.datetime(2026, 8, 30, 12, tzinfo=dt.UTC)
    history = evolve_production_error_history(
        None,
        previous_category=None,
        previous_message=None,
        next_category="audio",
        next_message="Final narration production failed",
        status="failed",
        phase="audio",
        now=failed_at,
    )
    recovered = evolve_production_error_history(
        history,
        previous_category="audio",
        previous_message="Final narration production failed",
        next_category=None,
        next_message=None,
        status="running",
        phase="narration_alignment",
        now=failed_at + dt.timedelta(minutes=1),
    )
    assert recovered is not None
    assert recovered[0]["category"] == "audio"
    assert recovered[0]["recovered_at"] is not None
    assert recovered[0]["recovery_phase"] == "narration_alignment"


def test_duplicate_active_failure_is_not_duplicated() -> None:
    now = dt.datetime(2026, 8, 30, tzinfo=dt.UTC)
    first = evolve_production_error_history(
        None,
        previous_category=None,
        previous_message=None,
        next_category="render",
        next_message="failed",
        status="failed",
        phase="render",
        now=now,
    )
    second = evolve_production_error_history(
        first,
        previous_category="render",
        previous_message="failed",
        next_category="render",
        next_message="failed",
        status="failed",
        phase="render",
        now=now + dt.timedelta(seconds=5),
    )
    assert second is not None
    assert len(second) == 1


def test_quality_unvalidated_audio_with_valid_artifact_is_reusable() -> None:
    workflow = SimpleNamespace(
        status="audio_concluido",
        audio_phase="quality_unvalidated",
        audio_final_path="/data/audio/final.wav",
        audio_final_sha256="a" * 64,
        duracao_audio_segundos=359.5,
    )
    assert ProductionOrchestrationService._audio_is_reusable(workflow)


def test_video_failure_does_not_turn_valid_audio_into_active_audio_error() -> None:
    workflow = SimpleNamespace(
        status="audio_concluido",
        audio_phase="completed",
        audio_final_path="/data/audio/final.wav",
        audio_final_sha256="b" * 64,
        duracao_audio_segundos=359.5,
        production_status="failed",
        production_phase="render",
        production_error_category="composer:render_failure",
    )
    assert ProductionOrchestrationService._audio_is_reusable(workflow)
    assert workflow.production_error_category.startswith("composer:")


@pytest.mark.asyncio
async def test_relationship_reload_repopulates_stale_identity_map() -> None:
    session = AsyncMock()
    result = AsyncMock()
    result.scalar_one_or_none = lambda: None
    session.execute.return_value = result

    await WorkflowRepository(session).obter_por_id(uuid4(), com_blocos=True)

    statement = session.execute.await_args.args[0]
    assert statement.get_execution_options()["populate_existing"] is True
