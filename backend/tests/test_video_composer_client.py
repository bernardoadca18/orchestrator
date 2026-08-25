from __future__ import annotations

import uuid

import httpx
import pytest

from app.clients.video_composer_client import (
    VideoComposerClient,
    VideoComposerConflictError,
    VideoComposerError,
    VideoComposerNotFoundError,
    VideoComposerQueueFullError,
    VideoComposerTransientError,
)
from app.core.config import Settings


def _payload(status: str = "queued") -> dict:
    render_id = str(uuid.uuid4())
    output = None
    error = None
    if status == "completed":
        output = {
            "sha256": "a" * 64,
            "byteSize": 100,
            "durationSeconds": 2.0,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "frameCount": 60,
            "videoCodec": "h264",
            "audioCodec": "aac",
            "audioSampleRate": 48000,
            "mimeType": "video/mp4",
            "filename": f"{render_id}.mp4",
        }
    if status == "failed":
        error = {"category": "render_failed", "message": "failed safely"}
    return {
        "id": render_id,
        "workflow_id": str(uuid.uuid4()),
        "production_timeline_id": str(uuid.uuid4()),
        "production_timeline_fingerprint": "b" * 64,
        "render_profile": "youtube_1080p30_v1",
        "render_profile_identity": "c" * 64,
        "mode": "final",
        "status": status,
        "phase": status,
        "progress": 1 if status == "completed" else 0,
        "frames_rendered": 60 if status == "completed" else 0,
        "total_frames": 60,
        "render_fingerprint": "d" * 64,
        "manifest_fingerprint": "e" * 64,
        "output": output,
        "metrics": {},
        "error": error,
        "created_at": "2026-08-25T00:00:00Z",
        "started_at": None,
        "completed_at": "2026-08-25T00:01:00Z" if status in {"completed", "failed"} else None,
        "status_url": f"/v1/renders/{render_id}",
        "video_url": f"/v1/renders/{render_id}/video" if status == "completed" else None,
    }


def _settings(**values: object) -> Settings:
    return Settings(video_composer_http_max_retries=0, **values)


@pytest.mark.asyncio
async def test_create_and_completed_aliases_are_typed() -> None:
    payload = _payload("completed")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["idempotency-key"] == "stable"
        return httpx.Response(202, json={"created": True, **payload})

    async with VideoComposerClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.create_render(
            workflow_id=uuid.UUID(payload["workflow_id"]),
            render_profile="youtube_1080p30_v1",
            idempotency_key="stable",
        )
    assert result.output is not None
    assert result.output.byte_size == 100
    assert result.output.audio_sample_rate == 48_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["queued", "preparing_assets", "rendering", "validating", "completed", "failed"],
)
async def test_get_parses_every_frozen_composer_status(status: str) -> None:
    payload = _payload(status)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with VideoComposerClient(_settings(), transport=transport) as client:
        result = await client.get_render(payload["id"])
    assert result.status == status
    assert (result.output is not None) is (status == "completed")
    assert (result.error is not None) is (status == "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, VideoComposerNotFoundError),
        (409, VideoComposerConflictError),
        (429, VideoComposerQueueFullError),
        (500, VideoComposerTransientError),
        (422, VideoComposerError),
    ],
)
async def test_status_mapping(status: int, error: type[Exception]) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json={"error": "safe"}))
    async with VideoComposerClient(_settings(), transport=transport) as client:
        with pytest.raises(error):
            await client.get_render(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_completed_response_requires_output() -> None:
    payload = _payload("completed")
    payload["output"] = None
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with VideoComposerClient(_settings(), transport=transport) as client:
        with pytest.raises(VideoComposerError, match="malformed"):
            await client.get_render(payload["id"])


@pytest.mark.asyncio
async def test_post_without_key_is_never_retried() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with VideoComposerClient(
        Settings(video_composer_http_max_retries=2),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(VideoComposerTransientError):
            await client.create_render(
                workflow_id=uuid.uuid4(),
                render_profile="youtube_1080p30_v1",
                idempotency_key=None,
            )
    assert calls == 1


@pytest.mark.asyncio
async def test_retryable_post_preserves_the_exact_idempotency_key() -> None:
    calls: list[str] = []
    payload = _payload("queued")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["idempotency-key"])
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(202, json={"created": True, **payload})

    async with VideoComposerClient(
        Settings(
            video_composer_http_max_retries=1,
            video_composer_http_backoff_seconds=0.001,
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.create_render(
            workflow_id=uuid.UUID(payload["workflow_id"]),
            render_profile="youtube_1080p30_v1",
            idempotency_key="same-key",
        )
    assert calls == ["same-key", "same-key"]


@pytest.mark.asyncio
async def test_network_timeout_is_a_safe_transient_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private transport detail", request=request)

    async with VideoComposerClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VideoComposerTransientError, match="network request failed"):
            await client.get_render(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_video_stream_forwards_range_without_buffering() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=0-3"
        return httpx.Response(
            206,
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/10"},
            content=b"1234",
        )

    async with VideoComposerClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        response = await client.open_video(str(uuid.uuid4()), range_header="bytes=0-3")
        assert response.status_code == 206
        assert b"".join([part async for part in response.aiter_bytes()]) == b"1234"
        await response.aclose()
