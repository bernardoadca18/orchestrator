from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.operator import HumanReviewCreateIn
from app.services.human_review import HumanReviewConflictError, HumanReviewService


def payload(render, status="approved", note=None):
    return HumanReviewCreateIn(
        workflow_render_id=render.id,
        render_fingerprint=render.render_fingerprint,
        output_sha256=render.output_sha256,
        status=status,
        note=note,
    )


def completed_render():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        render_fingerprint="a" * 64,
        output_sha256="b" * 64,
    )


@pytest.mark.asyncio
async def test_review_is_bound_to_current_render_identity_and_appended() -> None:
    workflow_id = uuid.uuid4()
    render = completed_render()
    service = HumanReviewService(MagicMock())
    service.reviews.get_by_idempotency_key = AsyncMock(return_value=None)
    service.workflows.obter_por_id_para_atualizacao = AsyncMock(
        return_value=SimpleNamespace(id=workflow_id)
    )
    service.production.current_render = AsyncMock(return_value=render)
    created = SimpleNamespace(id=uuid.uuid4())
    service.reviews.append = AsyncMock(return_value=(created, True))

    result, was_created = await service.submit(workflow_id, payload(render), "review:one")
    assert result is created and was_created is True
    assert service.reviews.append.await_args.kwargs["workflow_render_id"] == render.id
    assert service.reviews.append.await_args.kwargs["output_sha256"] == "b" * 64


@pytest.mark.asyncio
async def test_stale_browser_render_is_rejected_with_conflict() -> None:
    workflow_id = uuid.uuid4()
    current = completed_render()
    old = completed_render()
    service = HumanReviewService(MagicMock())
    service.reviews.get_by_idempotency_key = AsyncMock(return_value=None)
    service.workflows.obter_por_id_para_atualizacao = AsyncMock(
        return_value=SimpleNamespace(id=workflow_id)
    )
    service.production.current_render = AsyncMock(return_value=current)
    with pytest.raises(HumanReviewConflictError, match="render mudou"):
        await service.submit(workflow_id, payload(old), "review:stale")


def test_changes_requested_and_rejected_require_note() -> None:
    render = completed_render()
    with pytest.raises(ValueError, match="note is required"):
        payload(render, "changes_requested")
    assert payload(render, "changes_requested", "Corrigir corte").note == "Corrigir corte"


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_another_decision() -> None:
    workflow_id = uuid.uuid4()
    render = completed_render()
    existing = SimpleNamespace(
        workflow_execucao_id=workflow_id,
        workflow_render_id=render.id,
        render_fingerprint=render.render_fingerprint,
        output_sha256=render.output_sha256,
        status="approved",
        note=None,
    )
    service = HumanReviewService(MagicMock())
    service.reviews.get_by_idempotency_key = AsyncMock(return_value=existing)
    with pytest.raises(HumanReviewConflictError, match="outra decisão"):
        await service.submit(
            workflow_id,
            payload(render, "changes_requested", "Mudança"),
            "review:reused",
        )
