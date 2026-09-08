import uuid

import httpx
import pytest

from app.clients.ideas_hub_client import (
    IdeasHubClient,
    IdeasHubContractError,
    IdeasHubTransientError,
)
from app.core.config import Settings


def settings(**overrides) -> Settings:
    return Settings(
        ideas_hub_api_url="http://ideas.test",
        ideas_hub_api_timeout=1,
        ideas_hub_http_max_retries=overrides.get("retries", 0),
        ideas_hub_http_backoff_seconds=0,
    )


def script_payload() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "ideia_id": str(uuid.uuid4()),
        "versao": 2,
        "status": "aprovado",
        "estrutura": {
            "titulo": "Roteiro",
            "duracao_total_segundos": 60,
            "blocos": [{"tipo": "narração", "duracao_segundos": 60, "conteudo": "Texto"}],
        },
    }


@pytest.mark.asyncio
async def test_valid_ideas_hub_response_is_contract_checked() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=script_payload()))
    result = await IdeasHubClient(settings(), transport=transport).obter_roteiro(uuid.uuid4())
    assert result["estrutura"]["titulo"] == "Roteiro"


@pytest.mark.asyncio
async def test_malformed_ideas_hub_response_is_explicit() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(IdeasHubContractError):
        await IdeasHubClient(settings(), transport=transport).obter_roteiro(uuid.uuid4())


@pytest.mark.asyncio
async def test_visual_readiness_contract_is_typed_and_uses_canonical_endpoint() -> None:
    script_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/roteiros/{script_id}/visual-readiness"
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "policy_version": "visual_requirements_v2",
                "policy_identity": "a" * 64,
                "speakable_content_identity": "b" * 64,
                "block_count": 3,
                "visual_beat_count": 24,
                "textual_coverage_ratio": 1.0,
                "missing_ranges": [],
                "overlap_ranges": [],
                "issues": [],
            },
        )

    result = await IdeasHubClient(
        settings(), transport=httpx.MockTransport(handler)
    ).obter_visual_readiness(script_id)
    assert result["status"] == "ready"
    assert result["visual_beat_count"] == 24


@pytest.mark.asyncio
async def test_safe_get_retries_transient_server_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=script_payload())

    client = IdeasHubClient(settings(retries=1), transport=httpx.MockTransport(handler))
    await client.obter_roteiro(uuid.uuid4())
    assert calls == 2


@pytest.mark.asyncio
async def test_transient_failure_is_categorized_after_retry_budget() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(IdeasHubTransientError):
        await IdeasHubClient(settings(), transport=transport).obter_roteiro(uuid.uuid4())
