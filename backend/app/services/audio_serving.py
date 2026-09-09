"""Identity-checked HTTP publication of active narration artifacts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from fastapi.responses import FileResponse

from app.services.production_audio import AudioFileMetadata, ProductionAudioError, inspect_wav


class AudioServingIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioArtifactExpectation:
    sha256: str | None
    byte_size: int | None
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None
    frame_count: int | None = None
    subtype: str | None = None


def verify_audio_artifact(
    path: str | Path, expectation: AudioArtifactExpectation
) -> tuple[Path, AudioFileMetadata]:
    resolved = Path(path)
    if not resolved.is_file():
        raise AudioServingIntegrityError("active audio artifact is missing")
    try:
        metadata = inspect_wav(resolved)
    except ProductionAudioError as exc:
        raise AudioServingIntegrityError(
            "active audio artifact failed physical validation"
        ) from exc
    comparisons = {
        "checksum": (expectation.sha256, metadata.sha256),
        "byte size": (expectation.byte_size, metadata.byte_size),
        "sample rate": (expectation.sample_rate, metadata.sample_rate),
        "channels": (expectation.channels, metadata.channels),
        "frame count": (expectation.frame_count, metadata.frame_count),
        "subtype": (expectation.subtype, metadata.subtype),
    }
    for label, (expected, physical) in comparisons.items():
        if expected is not None and expected != physical:
            raise AudioServingIntegrityError(
                f"active audio artifact {label} differs from persisted metadata"
            )
    if expectation.duration_seconds is not None and not math.isclose(
        expectation.duration_seconds,
        metadata.duration_seconds,
        rel_tol=0.0,
        abs_tol=1.0 / metadata.sample_rate,
    ):
        raise AudioServingIntegrityError(
            "active audio artifact duration differs from persisted metadata"
        )
    return resolved, metadata


def audio_file_response(
    path: Path,
    metadata: AudioFileMetadata,
    *,
    filename: str,
    immutable: bool = False,
) -> FileResponse:
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=filename,
        content_disposition_type="inline",
        stat_result=path.stat(),
        headers={
            "ETag": f'"{metadata.sha256}"',
            "Cache-Control": (
                "private, max-age=31536000, immutable"
                if immutable
                else "private, no-cache, must-revalidate"
            ),
            "X-Audio-SHA256": metadata.sha256,
            "X-Audio-Duration-Seconds": f"{metadata.duration_seconds:.9f}",
            "X-Audio-Frame-Count": str(metadata.frame_count),
        },
    )
