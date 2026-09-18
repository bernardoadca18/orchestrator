import copy
import datetime as dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.clients.thumbnail_director import ThumbnailClientError, ThumbnailDirectorClient
from app.core.config import Settings
from app.schemas.thumbnail import Package
from app.services.thumbnail_identity import digest
from app.services.thumbnails import ThumbnailService, build_request, candidate_for
from app.services.visual_director_service import MediaScope


def workflow():
    return SimpleNamespace(
        id=uuid.uuid4(),
        roteiro_id=uuid.uuid4(),
        roteiro_snapshot={
            "titulo": "GTA IV vs GTA V",
            "content_locale": "pt-BR",
            "media_context": {"media_project_id": str(uuid.uuid4())},
            "blocos": [{"ordem": 0, "tipo": "intro", "conteudo": "GTA IV and GTA V cars"}],
        },
    )


def package(request):
    candidates = []
    for key, template in [
        ("A", "split_compare_v1"),
        ("B", "hero_focus_v1"),
        ("C", "curiosity_focus_v1"),
    ]:
        concept = dict(
            candidate_id=key,
            template_id=template,
            intent=template,
            headline="CARROS",
            primary_asset_query="cars",
            secondary_asset_query="cars",
            primary_subject=template,
            secondary_subject="",
        )
        candidate = dict(
            candidate_id=key,
            template_id=template,
            headline="CARROS",
            status="available",
            error_category=None,
            artifact=dict(
                artifact_id=f"thumb_{request['request_fingerprint']}_{key}",
                sha256="b" * 64,
                width=1280,
                height=720,
                format="png",
            ),
            concept=concept,
            sources=[
                dict(
                    media_id=str(uuid.uuid4()),
                    media_sha256="c" * 64,
                    materialized_sha256="d" * 64,
                    source_type="image",
                    filename="car.png",
                    session_id=None,
                    session_label=None,
                    game=None,
                    timestamp_seconds=None,
                    search_query="cars",
                    rank=1,
                    score=0.9,
                    crop_box=[0, 0, 100, 100],
                    transforms={},
                )
            ],
            render_parameters={},
        )
        candidate["candidate_identity"] = digest(candidate)
        candidates.append(candidate)
    body = dict(
        schema_version="thumbnail_package_v1",
        request_fingerprint=request["request_fingerprint"],
        strategy_version=request["strategy_version"],
        candidates=candidates,
    )
    body["result_fingerprint"] = digest(body)
    return Package.model_validate(body).model_dump(mode="json")


def test_request_fingerprint_is_frozen_and_revision_sensitive():
    w = workflow()
    scope = MediaScope(
        uuid.UUID(w.roteiro_snapshot["media_context"]["media_project_id"]),
        (),
        "script_media_context",
    )
    a = build_request(w, scope, 0)
    assert a == build_request(w, scope, 0)
    assert a["request_fingerprint"] != build_request(w, scope, 1)["request_fingerprint"]
    w.roteiro_snapshot["blocos"][0]["conteudo"] = "Changed approved text"
    assert a["request_fingerprint"] != build_request(w, scope, 0)["request_fingerprint"]


@pytest.mark.asyncio
async def test_create_reuse_regenerate_selection_history():
    w = workflow()
    rows = []
    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    def add(row):
        row.id = uuid.uuid4()
        row.created_at = dt.datetime.now(dt.UTC)
        rows.append(row)

    session.add.side_effect = add
    service = ThumbnailService(session)
    service.workflow = AsyncMock(return_value=w)
    service.current = AsyncMock(side_effect=lambda _: rows[-1] if rows else None)
    first = await service.ensure(w.id)
    assert await service.ensure(w.id) is first and len(rows) == 1
    with pytest.raises(HTTPException):
        await service.ensure(w.id, True)
    first.result = package(first.request_snapshot)
    first.status = "completed"
    with pytest.raises(HTTPException):
        await service.select(w.id, "X")
    await service.select(w.id, "B", first.id)
    assert first.selected_candidate_id == "B" and first.selected_at
    frozen = copy.deepcopy(first.result)
    second = await service.ensure(w.id, True)
    assert second.creative_revision == 1 and second.selected_candidate_id is None
    assert first.result == frozen and first.selected_candidate_id == "B"
    assert second.request_fingerprint != first.request_fingerprint
    with pytest.raises(HTTPException):
        await service.select(w.id, "B", first.id)
    with pytest.raises(HTTPException):
        candidate_for(second, "A")
    second.status = "completed"
    second.result = package(second.request_snapshot)
    with pytest.raises(HTTPException):
        candidate_for(second, "../../etc/passwd")


@pytest.mark.asyncio
async def test_client_rejects_wrong_package_and_proxy_checks_sha():
    w = workflow()
    scope = MediaScope(
        uuid.UUID(w.roteiro_snapshot["media_context"]["media_project_id"]), (), "snapshot"
    )
    request = build_request(w, scope, 0)
    result = package(request)
    client = ThumbnailDirectorClient(
        Settings(), httpx.MockTransport(lambda r: httpx.Response(200, json=result))
    )
    assert (await client.generate(request)) == result
    result["candidates"][0]["headline"] = "tampered"
    with pytest.raises(ThumbnailClientError):
        await client.generate(request)
    with pytest.raises(ThumbnailClientError):
        await client.artifact(result["candidates"][0]["artifact"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_duplicate_terminal_delivery_does_not_call_director(monkeypatch, status):
    from contextlib import asynccontextmanager

    from app.tasks import thumbnail_tasks

    session = MagicMock()
    session.scalar = AsyncMock(return_value=SimpleNamespace(status=status))

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(thumbnail_tasks, "task_session", factory)
    client = MagicMock()
    monkeypatch.setattr(thumbnail_tasks, "ThumbnailDirectorClient", client)
    assert not await thumbnail_tasks.execute(str(uuid.uuid4()))
    client.assert_not_called()
