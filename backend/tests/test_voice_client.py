from __future__ import annotations

import json
from collections import deque

import httpx
import pytest

from app.clients.voice_client import (
    VoiceClient,
    VoiceClientError,
    VoiceClientTimeoutError,
    VoiceClientTransientError,
    VoiceJobFailedError,
    VoiceJobNotFoundError,
)
from app.core.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        voice_api_url="http://voice.test",
        voice_http_max_retries=overrides.pop("voice_http_max_retries", 0),
        voice_http_backoff_seconds=0,
        voice_poll_interval_seconds=overrides.pop("voice_poll_interval_seconds", 0),
        **overrides,
    )


def _job(status: str = "completed", **changes) -> dict:
    payload = {
        "id": "job-1",
        "status": status,
        "created": True,
        "voice_profile": "bernardo_v1",
        "generation_profile": "narration_v1",
        "resolved_seed": 123,
        "model_id": "Qwen/Qwen3-TTS",
        "model_revision": "abc123",
        "result_duration_seconds": 1.0 if status == "completed" else None,
        "result_sample_rate": 24_000 if status == "completed" else None,
        "result_channels": 1 if status == "completed" else None,
        "result_subtype": "PCM_16" if status == "completed" else None,
        "result_byte_size": 100 if status == "completed" else None,
        "result_sha256": "a" * 64 if status == "completed" else None,
        "error_category": None,
        "error_message": None,
        "metadata": {},
    }
    payload.update(changes)
    return payload


@pytest.mark.asyncio
async def test_create_matches_real_contract_and_sends_stable_idempotency_key() -> None:
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(202, json=_job("queued"))

    async with VoiceClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.create_narration(
            text="Geralt usa Árd.",
            voice_profile="bernardo_v1",
            generation_profile="narration_v1",
            idempotency_key="stable-key",
            language="Portuguese",
            seed=0,
        )
    body = json.loads(observed[0].content)
    assert observed[0].url.path == "/v1/narrations"
    assert observed[0].headers["Idempotency-Key"] == "stable-key"
    assert body == {
        "text": "Geralt usa Árd.",
        "language": "Portuguese",
        "voice_profile": "bernardo_v1",
        "generation_profile": "narration_v1",
        "seed": 0,
    }
    assert result.resolved_seed == 123


@pytest.mark.asyncio
async def test_post_network_retry_reuses_identical_request_and_key() -> None:
    attempts: list[tuple[bytes, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append((request.content, request.headers["Idempotency-Key"]))
        if len(attempts) == 1:
            raise httpx.ConnectError("lost response", request=request)
        return httpx.Response(202, json=_job("queued"))

    async with VoiceClient(
        _settings(voice_http_max_retries=1), transport=httpx.MockTransport(handler)
    ) as client:
        await client.create_narration(
            text="text",
            voice_profile="bernardo_v1",
            generation_profile="narration_v1",
            idempotency_key="same-key",
            language="Portuguese",
        )
    assert attempts[0] == attempts[1]


@pytest.mark.asyncio
async def test_poll_queued_running_completed() -> None:
    states = deque([_job("queued"), _job("running"), _job("completed")])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=states.popleft())

    async with VoiceClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.wait_for_narration("job-1")
    assert result.status == "completed"
    assert not states


@pytest.mark.asyncio
async def test_failed_and_timeout_are_distinct() -> None:
    async def failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_job("failed", error_category="synthesis", error_message="provider failed"),
        )

    async with VoiceClient(_settings(), transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(VoiceJobFailedError, match="synthesis") as error:
            await client.wait_for_narration("job-1")
        assert error.value.category == "synthesis"

    async def queued(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_job("queued"))

    async with VoiceClient(
        _settings(voice_job_timeout_seconds=0.001), transport=httpx.MockTransport(queued)
    ) as client:
        with pytest.raises(VoiceClientTimeoutError):
            await client.wait_for_narration("job-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, VoiceClientError),
        (409, VoiceClientError),
        (404, VoiceJobNotFoundError),
        (429, VoiceClientTransientError),
        (503, VoiceClientTransientError),
    ],
)
async def test_http_error_classification(status: int, error: type[Exception]) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    async with VoiceClient(_settings(), transport=transport) as client:
        with pytest.raises(error):
            await client.get_narration("job-1")


@pytest.mark.asyncio
async def test_malformed_response_and_audio_content_type_are_rejected() -> None:
    responses = deque(
        [
            httpx.Response(200, json={"id": "missing-fields"}),
            httpx.Response(200, content=b"html", headers={"content-type": "text/html"}),
        ]
    )
    transport = httpx.MockTransport(lambda request: responses.popleft())
    async with VoiceClient(_settings(), transport=transport) as client:
        with pytest.raises(VoiceClientError, match="malformed"):
            await client.get_narration("job-1")
        with pytest.raises(VoiceClientError, match="content type"):
            await client.download_audio("job-1")


@pytest.mark.asyncio
async def test_profile_identities_and_wav_download() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/voices":
            return httpx.Response(
                200, json=[{"name": "bernardo_v1", "version": "1", "identity": "v" * 64}]
            )
        if request.url.path == "/v1/generation-profiles":
            return httpx.Response(
                200, json=[{"name": "narration_v1", "version": "1", "identity": "g" * 64}]
            )
        return httpx.Response(200, content=b"RIFFdata", headers={"content-type": "audio/wav"})

    async with VoiceClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        profiles = await client.resolve_profiles("bernardo_v1", "narration_v1")
        audio = await client.download_audio("job-1")
    assert profiles.voice.identity == "v" * 64
    assert profiles.generation.identity == "g" * 64
    assert audio == b"RIFFdata"
