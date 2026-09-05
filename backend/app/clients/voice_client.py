"""Provider-neutral client for the canonical asynchronous Voice Service."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings, settings


class VoiceClientError(RuntimeError):
    """Permanent request or contract failure."""


class VoiceClientTransientError(RuntimeError):
    """Temporary network, backpressure or service failure."""


class VoiceClientTimeoutError(VoiceClientTransientError):
    """The remote narration did not finish within its overall deadline."""


class VoiceJobNotFoundError(VoiceClientError):
    """A persisted remote job identifier no longer exists."""


class VoiceJobFailedError(VoiceClientError):
    """The remote job reached its explicit failed state."""

    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category
        self.public_message = message


class VoiceClientCancelledError(VoiceClientError):
    """The local workflow was cancelled while polling."""


class VoiceNarrationJob(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    id: str
    status: Literal["queued", "running", "completed", "failed"]
    created: bool | None = None
    voice_profile: str
    generation_profile: str
    resolved_seed: int
    model_id: str
    model_revision: str
    result_duration_seconds: float | None = None
    result_sample_rate: int | None = None
    result_channels: int | None = None
    result_subtype: str | None = None
    result_byte_size: int | None = None
    result_sha256: str | None = None
    result_frame_count: int | None = None
    result_riff_data_byte_size: int | None = None
    error_category: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceProfileDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    version: str
    identity: str
    clone_mode: str = "unknown"
    reference_id: str | None = None
    audio_sha256: str | None = None


class GenerationProfileDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    version: str
    identity: str


@dataclass(frozen=True)
class VoiceProfileProvenance:
    voice: VoiceProfileDescriptor
    generation: GenerationProfileDescriptor
    # The service owns chunking; older Voice Service deployments do not expose it.
    # Keeping a stable fallback preserves compatibility while a mismatch is never
    # silently accepted when the canonical health endpoint is available.
    chunking_identity: str = "legacy:unknown"
    chunking_strategy_version: str = "legacy:unknown"


class VoiceClient:
    """One task-scoped HTTP client, safe with Celery's per-task event loops."""

    def __init__(
        self,
        config: Settings = settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> VoiceClient:
        self._client = httpx.AsyncClient(
            base_url=self.settings.voice_api_url,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_narration(
        self,
        *,
        text: str,
        voice_profile: str,
        generation_profile: str,
        idempotency_key: str,
        language: str,
        seed: int = 0,
    ) -> VoiceNarrationJob:
        if not idempotency_key:
            raise VoiceClientError("Idempotency-Key is required for narration creation")
        response = await self._request_with_retry(
            "POST",
            "/v1/narrations",
            timeout=self.settings.voice_create_timeout_seconds,
            headers={"Idempotency-Key": idempotency_key},
            json={
                "text": text,
                "language": language,
                "voice_profile": voice_profile,
                "generation_profile": generation_profile,
                "seed": seed,
            },
        )
        return self._parse_job(response)

    async def create_reference_narration(
        self,
        *,
        text: str,
        reference_id: str,
        generation_profile: str,
        idempotency_key: str,
        language: str = "Portuguese",
        seed: int = 0,
    ) -> VoiceNarrationJob:
        """Create an ephemeral x-vector benchmark narration.

        ``reference_id`` is deliberately a relative identifier understood by the
        benchmark-only Voice Service endpoint; callers must never send host paths.
        """
        if not idempotency_key:
            raise VoiceClientError("Idempotency-Key is required for narration creation")
        response = await self._request_with_retry(
            "POST",
            "/v1/benchmark/narrations",
            timeout=self.settings.voice_create_timeout_seconds,
            headers={"Idempotency-Key": idempotency_key},
            json={
                "text": text,
                "language": language,
                "reference_id": reference_id,
                "generation_profile": generation_profile,
                "seed": seed,
            },
        )
        return self._parse_job(response)

    async def get_narration(self, job_id: str) -> VoiceNarrationJob:
        response = await self._request_with_retry(
            "GET",
            f"/v1/narrations/{job_id}",
            timeout=self.settings.voice_status_timeout_seconds,
        )
        return self._parse_job(response)

    async def wait_for_narration(
        self,
        job_id: str,
        *,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> VoiceNarrationJob:
        deadline = time.monotonic() + self.settings.voice_job_timeout_seconds
        while time.monotonic() < deadline:
            if should_stop is not None and await should_stop():
                raise VoiceClientCancelledError(
                    "workflow was cancelled while narration was running"
                )
            job = await self.get_narration(job_id)
            if job.status == "completed":
                return job
            if job.status == "failed":
                category = job.error_category or "provider_failure"
                message = job.error_message or "remote narration failed"
                raise VoiceJobFailedError(category, message)
            await asyncio.sleep(self.settings.voice_poll_interval_seconds)
        raise VoiceClientTimeoutError(
            f"narration job exceeded {self.settings.voice_job_timeout_seconds:g}s"
        )

    async def download_audio(self, job_id: str) -> bytes:
        response = await self._request_with_retry(
            "GET",
            f"/v1/narrations/{job_id}/audio",
            timeout=self.settings.voice_download_timeout_seconds,
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
            raise VoiceClientError("Voice Service returned an unexpected audio content type")
        if not response.content:
            raise VoiceClientError("Voice Service returned an empty audio result")
        return response.content

    async def list_chunks(self, job_id: str) -> list[dict[str, Any]]:
        """List all persisted chunk artifacts for a narration job."""
        response = await self._request_with_retry(
            "GET",
            f"/v1/narrations/{job_id}/chunks",
            timeout=self.settings.voice_status_timeout_seconds,
        )
        return response.json()

    async def download_chunk_audio(self, job_id: str, chunk_index: int) -> bytes:
        """Download a specific chunk's WAV audio file."""
        response = await self._request_with_retry(
            "GET",
            f"/v1/narrations/{job_id}/chunks/{chunk_index}/audio",
            timeout=self.settings.voice_download_timeout_seconds,
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
            raise VoiceClientError("Voice Service returned an unexpected audio content type")
        if not response.content:
            raise VoiceClientError("Voice Service returned an empty chunk audio result")
        return response.content

    async def regenerate_chunk(
        self, job_id: str, chunk_index: int, seed: int, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Regenerate a single chunk with a new seed."""
        if not idempotency_key:
            raise VoiceClientError("Idempotency-Key is required for chunk regeneration")
        response = await self._request_with_retry(
            "POST",
            f"/v1/narrations/{job_id}/chunks/{chunk_index}/regenerate",
            timeout=self.settings.voice_create_timeout_seconds,
            headers={"Idempotency-Key": idempotency_key},
            json={"seed": seed},
        )
        return response.json()

    async def download_chunk_candidate_audio(
        self, job_id: str, candidate_id: str
    ) -> bytes:
        """Download one exact immutable candidate artifact."""
        response = await self._request_with_retry(
            "GET",
            f"/v1/narrations/{job_id}/chunk-candidates/{candidate_id}/audio",
            timeout=self.settings.voice_download_timeout_seconds,
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
            raise VoiceClientError("Voice Service returned an unexpected audio content type")
        if not response.content:
            raise VoiceClientError("Voice Service returned an empty chunk candidate")
        return response.content

    async def resolve_profiles(
        self, voice_profile: str, generation_profile: str
    ) -> VoiceProfileProvenance:
        voices_response, generations_response = await asyncio.gather(
            self._request_with_retry(
                "GET",
                "/v1/voices",
                timeout=self.settings.voice_status_timeout_seconds,
            ),
            self._request_with_retry(
                "GET",
                "/v1/generation-profiles",
                timeout=self.settings.voice_status_timeout_seconds,
            ),
        )
        try:
            voices = [
                VoiceProfileDescriptor.model_validate(item) for item in voices_response.json()
            ]
            generations = [
                GenerationProfileDescriptor.model_validate(item)
                for item in generations_response.json()
            ]
        except (ValidationError, ValueError, TypeError) as exc:
            raise VoiceClientError("Voice Service profile response is malformed") from exc
        voice = next((item for item in voices if item.name == voice_profile), None)
        generation = next(
            (item for item in generations if item.name == generation_profile),
            None,
        )
        if voice is None or generation is None:
            raise VoiceClientError("configured voice or generation profile is unavailable")
        chunking_identity = "legacy:unknown"
        chunking_strategy_version = "legacy:unknown"
        try:
            health_response = await self._request_with_retry(
                "GET",
                "/health/ready",
                timeout=self.settings.voice_status_timeout_seconds,
            )
            health = health_response.json()
            if isinstance(health, dict):
                chunking_identity = str(health.get("chunking_identity") or chunking_identity)
                chunking_strategy_version = str(
                    health.get("chunking_strategy_version") or chunking_strategy_version
                )
        except (VoiceClientError, ValueError, TypeError):
            pass
        return VoiceProfileProvenance(
            voice=voice,
            generation=generation,
            chunking_identity=chunking_identity,
            chunking_strategy_version=chunking_strategy_version,
        )

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        **kwargs: Any,
    ) -> httpx.Response:
        last_error: VoiceClientTransientError | None = None
        for attempt in range(self.settings.voice_http_max_retries + 1):
            try:
                response = await self._client_required().request(
                    method,
                    path,
                    timeout=httpx.Timeout(timeout),
                    **kwargs,
                )
                self._raise_for_status(response)
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = VoiceClientTransientError("Voice Service network request failed")
                last_error.__cause__ = exc
            except VoiceClientTransientError as exc:
                last_error = exc
            if attempt < self.settings.voice_http_max_retries:
                await asyncio.sleep(self.settings.voice_http_backoff_seconds * (2**attempt))
        assert last_error is not None
        raise last_error

    def _client_required(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("VoiceClient must be used as an async context manager")
        return self._client

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 404:
            raise VoiceJobNotFoundError("remote narration job was not found")
        if status in {400, 409, 422}:
            raise VoiceClientError(f"Voice Service rejected the request with HTTP {status}")
        if status == 429 or status >= 500:
            raise VoiceClientTransientError(f"Voice Service returned HTTP {status}")
        raise VoiceClientError(f"unexpected Voice Service response HTTP {status}")

    @staticmethod
    def _parse_job(response: httpx.Response) -> VoiceNarrationJob:
        try:
            return VoiceNarrationJob.model_validate(response.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise VoiceClientError("Voice Service narration response is malformed") from exc
