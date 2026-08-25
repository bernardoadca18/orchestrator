from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.clients.media_indexer_search import (
    MediaIndexerConflictError,
    MediaIndexerContractError,
    MediaIndexerNotFoundError,
    MediaIndexerSearchProvider,
    MediaIndexerUnavailableError,
)
from app.core.config import Settings

PROJECT_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
SESSION_ID = uuid.UUID("10000000-0000-4000-8000-000000000002")
MEDIA_ID = uuid.UUID("10000000-0000-4000-8000-000000000003")
INDEX_ID = uuid.UUID("10000000-0000-4000-8000-000000000004")


def _settings(**values) -> Settings:
    return Settings(
        media_indexer_api_url="http://media-indexer.test",
        media_indexer_http_max_retries=values.pop("retries", 0),
        media_indexer_http_backoff_seconds=0.001,
        **values,
    )


def _session_payload() -> dict:
    return {
        "id": str(SESSION_ID),
        "project_id": str(PROJECT_ID),
        "media_id": str(MEDIA_ID),
        "label": "001",
        "status": "ready",
        "media": {
            "id": str(MEDIA_ID),
            "original_filename": "001.mp4",
            "duration_seconds": 3600.0,
        },
    }


def _search_payload(scope: str = "session") -> dict:
    return {
        "query": "Geralt learns Aard",
        "scope": scope,
        "project_id": str(PROJECT_ID),
        "session_id": str(SESSION_ID) if scope == "session" else None,
        "results": [
            {
                "session_id": str(SESSION_ID),
                "media_id": str(MEDIA_ID),
                "start_seconds": 1200.0,
                "end_seconds": 1240.0,
                "duration_seconds": 40.0,
                "score": 0.82,
                "rank": 1,
                "session_label": "001",
                "media_filename": "001.mp4",
                "temporal_index_id": str(INDEX_ID),
                "retrieval_candidate_id": None,
                "transcript_preview": "Geralt aprende Aard",
                "matched_note_ids": [],
                "provenance": {
                    "compatibility_signature": "sig",
                    "selection_reason": "default_latest",
                    "score_components": {"semantic_mean": 0.82},
                    "unsafe_external_field": "/host/private/path",
                },
            }
        ],
        "searched_sessions": 1,
        "skipped_sessions": 0,
        "session_summaries": [
            {
                "id": str(SESSION_ID),
                "label": "001",
                "searched": True,
                "result_count": 1,
                "skip_reason": None,
            }
        ],
        "strategy": "media_search_v1",
        "embedding_provider": "litellm",
        "embedding_model": "mxbai-large",
        "embedding_dimensions": 1024,
        "created_at": "2026-08-25T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_session_metadata_and_search_use_actual_public_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_session_payload())
        body = json.loads(request.content)
        assert body["min_score"] == 0.25
        return httpx.Response(200, json=_search_payload())

    provider = MediaIndexerSearchProvider(_settings(), transport=httpx.MockTransport(handler))
    session = await provider.get_session(SESSION_ID)
    result = await provider.search_session(
        SESSION_ID, query="Geralt learns Aard", max_results=5, min_score=0.25
    )
    assert session.duration_seconds == 3600.0
    assert result.results[0].temporal_index_id == INDEX_ID
    assert "unsafe_external_field" not in result.results[0].provenance


@pytest.mark.asyncio
async def test_project_search_preserves_skipped_session_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = _search_payload("project")
        second_session = uuid.uuid4()
        second = dict(payload["results"][0])
        second["session_id"] = str(second_session)
        second["rank"] = 2
        payload["results"].append(second)
        payload["skipped_sessions"] = 1
        payload["session_summaries"].append(
            {
                "id": str(uuid.uuid4()),
                "label": "not indexed",
                "searched": False,
                "result_count": 0,
                "skip_reason": "no completed temporal index",
            }
        )
        return httpx.Response(200, json=payload)

    provider = MediaIndexerSearchProvider(_settings(), transport=httpx.MockTransport(handler))
    result = await provider.search_project(
        PROJECT_ID,
        query="Aard",
        max_results=5,
        min_score=0.25,
        session_ids=[SESSION_ID],
    )
    assert result.scope == "project"
    assert len({item.session_id for item in result.results}) == 2
    assert result.skipped_sessions == 1
    assert result.session_summaries[-1]["searched"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, MediaIndexerNotFoundError),
        (409, MediaIndexerConflictError),
        (422, MediaIndexerContractError),
    ],
)
async def test_http_error_categories_are_distinct(status: int, error: type[Exception]) -> None:
    provider = MediaIndexerSearchProvider(
        _settings(), transport=httpx.MockTransport(lambda request: httpx.Response(status))
    )
    with pytest.raises(error):
        await provider.get_session(SESSION_ID)


@pytest.mark.asyncio
async def test_transient_timeout_retries_once_then_succeeds() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=_session_payload())

    provider = MediaIndexerSearchProvider(
        _settings(retries=1),
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: _no_sleep(),
    )
    assert (await provider.get_session(SESSION_ID)).id == SESSION_ID
    assert calls == 2


async def _no_sleep() -> None:
    return None


@pytest.mark.asyncio
async def test_retry_exhaustion_and_malformed_response_are_safe() -> None:
    unavailable = MediaIndexerSearchProvider(
        _settings(retries=1),
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        sleep=lambda seconds: _no_sleep(),
    )
    with pytest.raises(MediaIndexerUnavailableError, match="server error"):
        await unavailable.get_session(SESSION_ID)

    malformed = MediaIndexerSearchProvider(
        _settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    with pytest.raises(MediaIndexerContractError, match="session response"):
        await malformed.get_session(SESSION_ID)
