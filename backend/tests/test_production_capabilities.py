"""Snapshot resolution and persistence contract for production capabilities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.repositories.workflow_repository import WorkflowRepository
from app.services.production_capabilities import burned_in_captions_for, capabilities_for


@pytest.mark.asyncio
async def test_repository_persists_snapshot_on_creation() -> None:
    session = SimpleNamespace(add=lambda item: None, commit=AsyncMock(), refresh=AsyncMock())
    snapshot = {
        "delivery_mode": "rendered_video",
        "visual_mode": "semantic_match",
        "requires_media_scope": True,
        "caption_policy": "none",
        "render_profile": None,
        "version": "production_capabilities_v1",
    }
    workflow = await WorkflowRepository(session).criar(
        ideia_id=uuid4(),
        roteiro_id=uuid4(),
        roteiro_snapshot={"blocos": []},
        perfil_producao="faceless_pop_culture_lists",
        production_capabilities=snapshot,
    )
    assert workflow.production_capabilities == snapshot
    assert not burned_in_captions_for(workflow)
    snapshot["caption_policy"] = "burned_in"
    assert not burned_in_captions_for(workflow)
    session.commit.assert_awaited_once()


def test_snapshot_takes_precedence_over_legacy_profile_id() -> None:
    workflow = SimpleNamespace(
        perfil_producao="faceless_pop_culture_lists",
        production_capabilities={"caption_policy": "burned_in", "visual_mode": "storyboard"},
    )
    assert burned_in_captions_for(workflow)
    assert capabilities_for(workflow)["visual_mode"] == "storyboard"
