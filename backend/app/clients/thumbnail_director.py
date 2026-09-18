import httpx

from app.core.config import settings
from app.schemas.thumbnail import Package, ThumbnailRequest
from app.services.thumbnail_identity import sha


class ThumbnailClientError(RuntimeError):
    def __init__(self, category, retryable=False):
        self.category = category
        self.retryable = retryable
        super().__init__(category)


class ThumbnailDirectorClient:
    def __init__(self, config=settings, transport=None):
        self.config, self.transport = config, transport

    async def generate(self, snapshot):
        request = ThumbnailRequest.model_validate(snapshot)
        try:
            async with httpx.AsyncClient(
                base_url=self.config.thumbnail_director_base_url,
                timeout=self.config.thumbnail_director_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post("/v1/thumbnails/generate", json=snapshot)
                if response.status_code >= 400:
                    category = "concept_generation_failed"
                    try:
                        supplied = response.json()["detail"]["category"]
                        if supplied in {
                            "concept_generation_failed",
                            "artifact_integrity_failed",
                            "contract_error",
                        }:
                            category = supplied
                    except (ValueError, KeyError, TypeError):
                        pass
                    raise ThumbnailClientError(category, response.status_code >= 500)
                package = Package.model_validate(response.json())
                if (
                    package.strategy_version != request.strategy_version
                    or not package.result_fingerprint
                    or package.request_fingerprint != request.request_fingerprint
                ):
                    raise ValueError("Package request mismatch")
                for candidate in package.candidates:
                    for source in candidate.sources:
                        if (
                            source.source_type == "video_frame"
                            and request.media_scope.session_ids
                            and source.session_id not in request.media_scope.session_ids
                        ):
                            raise ValueError("Source session mismatch")
                return package.model_dump(mode="json")
        except httpx.HTTPError:
            raise ThumbnailClientError("source_materialization_failed", True) from None
        except ValueError:
            raise ThumbnailClientError("contract_error") from None

    async def artifact(self, artifact):
        from app.schemas.thumbnail import Artifact

        artifact = Artifact.model_validate(artifact)
        try:
            async with httpx.AsyncClient(  # noqa: SIM117 - explicit response stream lifetime
                base_url=self.config.thumbnail_director_base_url,
                timeout=30,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "GET", f"/v1/artifacts/{artifact.artifact_id}"
                ) as response:
                    response.raise_for_status()
                    parts, size = [], 0
                    async for part in response.aiter_bytes():
                        size += len(part)
                        if size > 16 * 1024 * 1024:
                            raise ValueError("Artifact exceeds limit")
                        parts.append(part)
            data = b"".join(parts)
            if sha(data) != artifact.sha256 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Checksum mismatch")
            return data
        except (httpx.HTTPError, ValueError):
            raise ThumbnailClientError("artifact_integrity_failed") from None
