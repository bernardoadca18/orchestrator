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
async def test_production_readiness_contract_is_typed_and_uses_canonical_endpoint() -> None:
    script_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/roteiros/{script_id}/production-readiness"
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "profile": "game_analysis",
                "capabilities": {
                    "delivery_mode": "rendered_video",
                    "visual_mode": "semantic_match",
                    "requires_media_scope": True,
                    "caption_policy": "burned_in",
                    "render_profile": None,
                    "version": "production_capabilities_v1",
                },
                "editorial_status": "aprovado",
                "visual": {
                    "status": "ready",
                    "policy_version": "visual_requirements_v2",
                    "policy_identity": "a" * 64,
                    "speakable_content_identity": "b" * 64,
                    "block_count": 1,
                    "visual_beat_count": 1,
                    "textual_coverage_ratio": 1.0,
                    "missing_ranges": [],
                    "overlap_ranges": [],
                    "issues": [],
                },
                "media_scope": {
                    "required": True,
                    "status": "ready",
                    "media_project_id": str(project_id),
                    "media_session_ids": [str(session_id)],
                },
                "issues": [],
            },
        )

    result = await IdeasHubClient(
        settings(), transport=httpx.MockTransport(handler)
    ).obter_production_readiness(script_id)
    assert result["status"] == "ready"
    assert result["profile"] == "game_analysis"
    assert result["media_scope"]["media_project_id"] == str(project_id)


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


@pytest.mark.asyncio
async def test_historical_approval_uses_exact_revision_id() -> None:
    script_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == f"/roteiros/{script_id}/aprovar"
        return httpx.Response(200, json=script_payload())

    result = await IdeasHubClient(
        settings(), transport=httpx.MockTransport(handler)
    ).aprovar_roteiro(script_id)
    assert result["status"] == "aprovado"


@pytest.mark.asyncio
async def test_media_binding_mutation_is_single_attempt_and_preserves_scope() -> None:
    script_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session_id = uuid.uuid4()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        assert request.url.path == f"/roteiros/{script_id}/revisoes/vincular-midia"
        assert request.read()
        return httpx.Response(200, json=script_payload())

    scope = {
        "media_project_id": str(project_id),
        "media_session_ids": [str(session_id)],
    }
    await IdeasHubClient(
        settings(retries=3), transport=httpx.MockTransport(handler)
    ).vincular_midia(script_id, scope)
    assert calls == 1


@pytest.mark.asyncio
async def test_visual_enrichment_uses_exact_revision_and_validates_child_lineage() -> None:
    script_id = uuid.uuid4()
    child = script_payload()
    child["parent_roteiro_id"] = str(script_id)
    child["revision_kind"] = "visual_enrichment"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "POST"
        assert request.url.path == f"/roteiros/{script_id}/preparar-producao"
        return httpx.Response(201, json=child)

    result = await IdeasHubClient(
        settings(retries=3), transport=httpx.MockTransport(handler)
    ).preparar_producao(script_id)

    assert calls == 1
    assert result["id"] == child["id"]
    assert result["parent_roteiro_id"] == str(script_id)
    assert result["revision_kind"] == "visual_enrichment"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok"},
        {**script_payload(), "revision_kind": "human_edit"},
    ],
)
async def test_visual_enrichment_rejects_malformed_or_invalid_upstream_revision(
    payload: dict,
) -> None:
    script_id = uuid.uuid4()
    transport = httpx.MockTransport(lambda request: httpx.Response(201, json=payload))

    with pytest.raises(IdeasHubContractError):
        await IdeasHubClient(settings(), transport=transport).preparar_producao(script_id)


def test_invalid_production_capabilities_are_rejected() -> None:
    from pydantic import ValidationError

    from app.clients.ideas_hub_client import IdeasHubProductionCapabilities

    valid = dict(
        delivery_mode="rendered_video",
        visual_mode="semantic_match",
        requires_media_scope=True,
        caption_policy="burned_in",
        render_profile=None,
        version="production_capabilities_v1",
    )
    assert IdeasHubProductionCapabilities.model_validate(valid).visual_mode == "semantic_match"
    assert (
        IdeasHubProductionCapabilities.model_validate(
            {**valid, "delivery_mode": "manual_edit_package", "visual_mode": "none"}
        ).visual_mode
        == "none"
    )
    with pytest.raises(ValidationError):
        IdeasHubProductionCapabilities.model_validate({**valid, "visual_mode": "unknown"})
