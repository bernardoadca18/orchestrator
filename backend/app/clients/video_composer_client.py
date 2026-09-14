"""Typed, provider-neutral HTTP boundary for the frozen Stage 16 Composer API."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import Settings, settings


class VideoComposerError(RuntimeError):
    """Permanent request or response-contract failure."""


class VideoComposerTransientError(VideoComposerError):
    """Network or server failure that may succeed later."""


class VideoComposerNotFoundError(VideoComposerError):
    pass


class VideoComposerConflictError(VideoComposerError):
    pass


class VideoComposerQueueFullError(VideoComposerTransientError):
    pass


class ComposerRenderOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(alias="byteSize", gt=0)
    duration_seconds: float = Field(alias="durationSeconds", gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    frame_count: int | None = Field(alias="frameCount", default=None, gt=0)
    video_codec: str = Field(alias="videoCodec", min_length=1)
    audio_codec: str = Field(alias="audioCodec", min_length=1)
    audio_sample_rate: int = Field(alias="audioSampleRate", gt=0)
    audio_channels: int = Field(alias="audioChannels", default=1, gt=0)
    mime_type: str = Field(alias="mimeType", min_length=1)
    filename: str = Field(min_length=1)


class ComposerRenderError(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str
    message: str | None


class ComposerRenderJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    workflow_id: uuid.UUID
    production_timeline_id: uuid.UUID
    production_timeline_fingerprint: str
    render_profile: str
    render_profile_identity: str
    mode: Literal["preview", "final"]
    status: Literal["queued", "preparing_assets", "rendering", "validating", "completed", "failed"]
    phase: str
    progress: float = Field(ge=0, le=1)
    frames_rendered: int = Field(ge=0)
    total_frames: int = Field(gt=0)
    render_fingerprint: str
    manifest_fingerprint: str
    presentation_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    audio_mix_plan_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    output: ComposerRenderOutput | None = None
    metrics: dict[str, Any] | None = None
    error: ComposerRenderError | None = None
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    status_url: str
    video_url: str | None = None

    @model_validator(mode="after")
    def completed_contract_is_strict(self) -> ComposerRenderJob:
        if self.status == "completed" and self.output is None:
            raise ValueError("completed Composer render is missing output metadata")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed Composer render is missing error metadata")
        return self


class ComposerRenderCreateResponse(ComposerRenderJob):
    created: bool


class ComposerRenderProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    version: str
    identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    container: str
    video_codec: str
    audio_codec: str
    audio_sample_rate: int = Field(gt=0)
    audio_channels: int = Field(default=1, gt=0)
    fit_mode: str
    final_gap_policy: str
    preview_gap_policy: str


class ComposerRenderProfiles(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ComposerRenderProfile]


# Internal compatibility alias keeps repository/service annotations concise.
ComposerRender = ComposerRenderJob


class VideoComposerClient:
    """One event-loop-scoped client; POST retries require a durable key."""

    def __init__(
        self,
        config: Settings = settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> VideoComposerClient:
        self._client = httpx.AsyncClient(
            base_url=self.settings.video_composer_api_url,
            transport=self._transport,
            timeout=httpx.Timeout(self.settings.video_composer_http_timeout_seconds),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_render(
        self,
        *,
        workflow_id: uuid.UUID,
        render_profile: str,
        mode: Literal["preview", "final"] = "final",
        idempotency_key: str | None,
    ) -> ComposerRenderCreateResponse:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = await self._request(
            "POST",
            "/v1/renders",
            headers=headers,
            json={
                "workflow_id": str(workflow_id),
                "render_profile": render_profile,
                "mode": mode,
            },
            allow_retry=idempotency_key is not None,
        )
        return self._parse_create_response(response)

    async def get_render(self, render_id: str) -> ComposerRenderJob:
        response = await self._request("GET", f"/v1/renders/{render_id}", allow_retry=True)
        return self._parse_render(response)

    async def get_render_profiles(self) -> list[ComposerRenderProfile]:
        response = await self._request("GET", "/v1/render-profiles", allow_retry=True)
        try:
            return ComposerRenderProfiles.model_validate(response.json()).items
        except (ValidationError, ValueError, TypeError) as exc:
            raise VideoComposerError(
                "Video Composer render profiles response is malformed"
            ) from exc

    async def health_ready(self) -> bool:
        response = await self._request("GET", "/health/ready", allow_retry=True)
        try:
            return response.json().get("status") == "ready"
        except (ValueError, AttributeError):
            return False

    async def open_video(
        self, render_id: str, *, range_header: str | None = None
    ) -> httpx.Response:
        headers = {"Range": range_header} if range_header else None
        client = self._client_required()
        try:
            response = await client.send(
                client.build_request("GET", f"/v1/renders/{render_id}/video", headers=headers),
                stream=True,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise VideoComposerTransientError("Video Composer stream request failed") from exc
        try:
            self._raise_for_status(response)
        except Exception:
            await response.aclose()
            raise
        return response

    async def _request(
        self,
        method: str,
        path: str,
        *,
        allow_retry: bool,
        **kwargs: Any,
    ) -> httpx.Response:
        retries = self.settings.video_composer_http_max_retries if allow_retry else 0
        last_error: VideoComposerTransientError | None = None
        for attempt in range(retries + 1):
            try:
                response = await self._client_required().request(
                    method,
                    path,
                    timeout=httpx.Timeout(self.settings.video_composer_http_timeout_seconds),
                    **kwargs,
                )
                self._raise_for_status(response)
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = VideoComposerTransientError("Video Composer network request failed")
                last_error.__cause__ = exc
            except VideoComposerTransientError as exc:
                last_error = exc
            if attempt < retries:
                await asyncio.sleep(
                    self.settings.video_composer_http_backoff_seconds * (2**attempt)
                )
        assert last_error is not None
        raise last_error

    def _client_required(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("VideoComposerClient must be used as an async context manager")
        return self._client

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 404:
            raise VideoComposerNotFoundError("Video Composer render was not found")
        if status == 409:
            raise VideoComposerConflictError("Video Composer rejected the current render state")
        if status == 429:
            raise VideoComposerQueueFullError("Video Composer queue is full")
        if status in {400, 422}:
            raise VideoComposerError(f"Video Composer rejected the request with HTTP {status}")
        if status >= 500:
            raise VideoComposerTransientError(f"Video Composer returned HTTP {status}")
        raise VideoComposerError(f"Unexpected Video Composer response HTTP {status}")

    @staticmethod
    def _parse_render(response: httpx.Response) -> ComposerRenderJob:
        try:
            return ComposerRenderJob.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise VideoComposerError("Video Composer render response is malformed") from exc

    @staticmethod
    def _parse_create_response(response: httpx.Response) -> ComposerRenderCreateResponse:
        try:
            return ComposerRenderCreateResponse.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise VideoComposerError("Video Composer render response is malformed") from exc
