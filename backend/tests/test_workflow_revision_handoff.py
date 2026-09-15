from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routers.workflow import (
    aprovar_revisao_para_producao,
    criar_workflow,
    listar_roteiros_disponiveis,
    production_readiness_blockers,
    vincular_midia_para_producao,
)
from app.clients.media_indexer_search import (
    MediaProjectMetadata,
    MediaSessionSummary,
)
from app.schemas.workflow import WorkflowCreateIn, WorkflowMediaBindingIn


def _script(
    *,
    script_id: uuid.UUID,
    idea_id: uuid.UUID,
    version: int,
    status: str = "aprovado",
    media: bool = True,
) -> dict:
    return {
        "id": str(script_id),
        "ideia_id": str(idea_id),
        "versao": version,
        "parent_roteiro_id": None,
        "revision_kind": "human_edit",
        "status": status,
        "criado_em": dt.datetime(2026, 1, version, tzinfo=dt.UTC).isoformat(),
        "estrutura": {
            "titulo": "GTA",
            "duracao_total_segundos": 60,
            "blocos": [{"tipo": "corpo", "conteudo": "Texto"}],
            "media_context": {"media_project_id": str(uuid.uuid4())} if media else None,
        },
    }


def _readiness(*codes: str) -> dict:
    return {
        "status": "not_ready" if codes else "ready",
        "profile": "game_analysis",
        "editorial_status": "aprovado",
        "visual": {
            "status": "not_ready" if "visual_readiness_not_ready" in codes else "ready",
            "issues": [{"code": "visual_query_gap"}]
            if "visual_readiness_not_ready" in codes
            else [],
        },
        "media_scope": {
            "required": True,
            "status": "missing" if "media_scope_missing" in codes else "ready",
        },
        "issues": [{"code": code} for code in codes],
    }


@pytest.mark.asyncio
async def test_revision_list_keeps_historical_items_and_marks_only_latest() -> None:
    idea_id = uuid.uuid4()
    historical = _script(script_id=uuid.uuid4(), idea_id=idea_id, version=2)
    latest = _script(script_id=uuid.uuid4(), idea_id=idea_id, version=4)
    hub = MagicMock()
    hub.listar_roteiros = AsyncMock(return_value=[latest, historical])

    with patch("app.api.routers.workflow.ideas_hub_client", hub):
        result = await listar_roteiros_disponiveis()

    assert [item.id for item in result] == [uuid.UUID(latest["id"]), uuid.UUID(historical["id"])]
    assert result[0].is_latest is True
    assert result[1].is_latest is False
    assert result[1].status == "aprovado"


@pytest.mark.asyncio
async def test_approved_historical_ready_revision_creates_exact_workflow_snapshot() -> None:
    script_id = uuid.uuid4()
    idea_id = uuid.uuid4()
    historical = _script(script_id=script_id, idea_id=idea_id, version=2)
    workflow = SimpleNamespace(id=uuid.uuid4())
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(return_value=historical)
    hub.obter_production_readiness = AsyncMock(return_value=_readiness())
    repository = MagicMock()
    repository.criar = AsyncMock(return_value=workflow)

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.processar_producao_workflow") as task,
    ):
        result = await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())

    assert result is workflow
    assert repository.criar.await_args.kwargs["roteiro_id"] == script_id
    assert repository.criar.await_args.kwargs["roteiro_snapshot"] == historical["estrutura"]
    hub.obter_roteiro.assert_awaited_once_with(script_id)
    task.delay.assert_called_once_with(str(workflow.id))


def test_readiness_codes_have_distinct_operator_actions() -> None:
    blockers = production_readiness_blockers(
        _readiness(
            "editorial_approval_missing",
            "media_scope_missing",
            "visual_readiness_not_ready",
        )
    )
    assert [(item["code"], item["action"]) for item in blockers] == [
        ("editorial_approval_missing", "APPROVE_REVISION"),
        ("media_scope_missing", "CONFIGURE_MEDIA"),
        ("visual_readiness_not_ready", "REVIEW_VISUAL_ISSUES"),
    ]
    assert blockers[2]["details"] == [{"code": "visual_query_gap"}]


@pytest.mark.asyncio
async def test_unapproved_revision_is_actionable_and_never_creates_workflow() -> None:
    script_id = uuid.uuid4()
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value=_script(
            script_id=script_id,
            idea_id=uuid.uuid4(),
            version=2,
            status="rascunho",
        )
    )
    repository = MagicMock()
    repository.criar = AsyncMock()

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        pytest.raises(HTTPException) as caught,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())

    assert caught.value.status_code == 409
    assert caught.value.detail["action"] == "APPROVE_REVISION"
    repository.criar.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_scope_missing_returns_configure_media_without_workflow() -> None:
    script_id = uuid.uuid4()
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value=_script(
            script_id=script_id,
            idea_id=uuid.uuid4(),
            version=2,
            media=False,
        )
    )
    hub.obter_production_readiness = AsyncMock(
        return_value=_readiness("media_scope_missing")
    )
    repository = MagicMock()
    repository.criar = AsyncMock()

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        pytest.raises(HTTPException) as caught,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())

    assert caught.value.status_code == 409
    assert caught.value.detail["blockers"] == [
        {
            "code": "media_scope_missing",
            "message": "Configure o escopo de mídia antes de iniciar a produção.",
            "action": "CONFIGURE_MEDIA",
        }
    ]
    repository.criar.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_approval_targets_exact_historical_revision() -> None:
    script_id = uuid.uuid4()
    script = _script(
        script_id=script_id,
        idea_id=uuid.uuid4(),
        version=2,
        status="aprovado",
        media=False,
    )
    hub = MagicMock()
    hub.aprovar_roteiro = AsyncMock(return_value=script)
    hub.obter_production_readiness = AsyncMock(
        return_value=_readiness("media_scope_missing")
    )

    with patch("app.api.routers.workflow.ideas_hub_client", hub):
        result = await aprovar_revisao_para_producao(script_id)

    assert result.roteiro_id == script_id
    assert result.versao == 2
    assert result.status == "aprovado"
    hub.aprovar_roteiro.assert_awaited_once_with(script_id)


@pytest.mark.asyncio
async def test_inline_binding_validates_scope_and_returns_ready_bound_revision() -> None:
    script_id = uuid.uuid4()
    bound_id = uuid.uuid4()
    idea_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()
    script = _script(script_id=script_id, idea_id=idea_id, version=2, media=False)
    bound = _script(script_id=bound_id, idea_id=idea_id, version=5, media=True)
    bound["parent_roteiro_id"] = str(script_id)
    bound["revision_kind"] = "media_binding"
    provider = MagicMock()
    provider.list_projects = AsyncMock(
        return_value=(MediaProjectMetadata(project_id, "Corpus", None),)
    )
    provider.list_project_sessions = AsyncMock(
        return_value=(
            MediaSessionSummary(
                id=session_id,
                project_id=project_id,
                media_id=uuid.uuid4(),
                label="gta_iv_gameplay",
                status="ready",
            ),
        )
    )
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(return_value=script)
    hub.vincular_midia = AsyncMock(return_value=bound)
    hub.obter_production_readiness = AsyncMock(return_value=_readiness())

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.MediaIndexerSearchProvider", return_value=provider),
    ):
        result = await vincular_midia_para_producao(
            script_id,
            WorkflowMediaBindingIn(
                media_project_id=project_id,
                media_session_ids=[session_id],
            ),
        )

    assert result.roteiro_id == bound_id
    assert result.parent_roteiro_id == script_id
    assert result.status == "aprovado"
    assert result.production_readiness["status"] == "ready"
    hub.vincular_midia.assert_awaited_once_with(
        script_id,
        {
            "media_project_id": str(project_id),
            "media_session_ids": [str(session_id)],
        },
    )


@pytest.mark.asyncio
async def test_followup_workflow_pins_new_bound_revision_id() -> None:
    parent_id = uuid.uuid4()
    bound_id = uuid.uuid4()
    idea_id = uuid.uuid4()
    bound = _script(script_id=bound_id, idea_id=idea_id, version=5, media=True)
    bound["parent_roteiro_id"] = str(parent_id)
    bound["revision_kind"] = "media_binding"
    workflow = SimpleNamespace(id=uuid.uuid4())
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(return_value=bound)
    hub.obter_production_readiness = AsyncMock(return_value=_readiness())
    repository = MagicMock()
    repository.criar = AsyncMock(return_value=workflow)

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        patch("app.api.routers.workflow.processar_producao_workflow"),
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=bound_id), MagicMock())

    assert repository.criar.await_args.kwargs["roteiro_id"] == bound_id
    assert repository.criar.await_args.kwargs["roteiro_id"] != parent_id
    assert repository.criar.await_args.kwargs["roteiro_snapshot"] == bound["estrutura"]


@pytest.mark.asyncio
async def test_project_wide_binding_preserves_empty_session_semantics() -> None:
    script_id = uuid.uuid4()
    bound_id = uuid.uuid4()
    project_id = uuid.uuid4()
    script = _script(script_id=script_id, idea_id=uuid.uuid4(), version=2, media=False)
    bound = _script(script_id=bound_id, idea_id=uuid.UUID(script["ideia_id"]), version=3)
    provider = MagicMock()
    provider.list_projects = AsyncMock(
        return_value=(MediaProjectMetadata(project_id, "Corpus", None),)
    )
    provider.list_project_sessions = AsyncMock(return_value=())
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(return_value=script)
    hub.vincular_midia = AsyncMock(return_value=bound)
    hub.obter_production_readiness = AsyncMock(return_value=_readiness())

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.MediaIndexerSearchProvider", return_value=provider),
    ):
        await vincular_midia_para_producao(
            script_id,
            WorkflowMediaBindingIn(media_project_id=project_id, media_session_ids=[]),
        )

    assert hub.vincular_midia.await_args.args[1]["media_session_ids"] == []


@pytest.mark.asyncio
async def test_invalid_project_never_creates_binding() -> None:
    script_id = uuid.uuid4()
    provider = MagicMock()
    provider.list_projects = AsyncMock(return_value=())
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value=_script(
            script_id=script_id,
            idea_id=uuid.uuid4(),
            version=2,
        )
    )
    hub.vincular_midia = AsyncMock()

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.MediaIndexerSearchProvider", return_value=provider),
        pytest.raises(HTTPException) as caught,
    ):
        await vincular_midia_para_producao(
            script_id,
            WorkflowMediaBindingIn(
                media_project_id=uuid.uuid4(),
                media_session_ids=[],
            ),
        )

    assert caught.value.status_code == 422
    hub.vincular_midia.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_session_never_creates_binding_or_workflow() -> None:
    script_id = uuid.uuid4()
    project_id = uuid.uuid4()
    invalid_session_id = uuid.uuid4()
    provider = MagicMock()
    provider.list_projects = AsyncMock(
        return_value=(MediaProjectMetadata(project_id, "Corpus", None),)
    )
    provider.list_project_sessions = AsyncMock(return_value=())
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value=_script(
            script_id=script_id,
            idea_id=uuid.uuid4(),
            version=2,
        )
    )
    hub.vincular_midia = AsyncMock()

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.MediaIndexerSearchProvider", return_value=provider),
        pytest.raises(HTTPException) as caught,
    ):
        await vincular_midia_para_producao(
            script_id,
            WorkflowMediaBindingIn(
                media_project_id=project_id,
                media_session_ids=[invalid_session_id],
            ),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail["category"] == "invalid_media_sessions"
    hub.vincular_midia.assert_not_awaited()


@pytest.mark.asyncio
async def test_visual_not_ready_remains_a_strict_blocker() -> None:
    script_id = uuid.uuid4()
    hub = MagicMock()
    hub.obter_roteiro = AsyncMock(
        return_value=_script(
            script_id=script_id,
            idea_id=uuid.uuid4(),
            version=2,
        )
    )
    hub.obter_production_readiness = AsyncMock(
        return_value=_readiness("visual_readiness_not_ready")
    )
    repository = MagicMock()
    repository.criar = AsyncMock()

    with (
        patch("app.api.routers.workflow.ideas_hub_client", hub),
        patch("app.api.routers.workflow.WorkflowRepository", return_value=repository),
        pytest.raises(HTTPException) as caught,
    ):
        await criar_workflow(WorkflowCreateIn(roteiro_id=script_id), MagicMock())

    assert caught.value.status_code == 409
    assert caught.value.detail["blockers"][0]["action"] == "REVIEW_VISUAL_ISSUES"
    repository.criar.assert_not_awaited()
