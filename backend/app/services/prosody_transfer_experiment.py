"""Provider-neutral, opt-in guide-TTS -> timbre-transfer experiment boundary.

This module is intentionally disconnected from production workflow dispatch. Heavy
providers are released between phases so one 12 GB GPU can run them sequentially.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from app.services.audio_diagnostics import analyze_audio
from app.services.production_audio import AudioFileMetadata, inspect_wav

EXPERIMENT_VERSION = "professional_prosody_timbre_transfer_v1"


@dataclass(frozen=True)
class ExperimentalAudio:
    path: Path
    provider: str
    provider_identity: str
    request_identity: str


class GuideTTSProvider(Protocol):
    name: str

    def generate_guide(self, *, text: str, language: str, style: str) -> ExperimentalAudio: ...

    def release(self) -> None: ...


class TimbreTransferProvider(Protocol):
    name: str

    def transfer_timbre(
        self, *, guide: Path, target_reference: Path, output: Path
    ) -> ExperimentalAudio: ...

    def release(self) -> None: ...


@dataclass(frozen=True)
class ProsodyTransferResult:
    identity: str
    guide: AudioFileMetadata
    converted: AudioFileMetadata
    guide_diagnostics: dict[str, object]
    converted_diagnostics: dict[str, object]
    manifest: dict[str, object]


def run_prosody_transfer_experiment(
    *,
    text: str,
    language: str,
    style: str,
    target_reference: Path,
    output: Path,
    guide_provider: GuideTTSProvider,
    conversion_provider: TimbreTransferProvider,
    maximum_duration_delta_ratio: float = 0.08,
) -> ProsodyTransferResult:
    if not text.strip() or not target_reference.is_file():
        raise ValueError("experiment text and immutable target reference are required")
    if not 0 <= maximum_duration_delta_ratio <= 0.5:
        raise ValueError("experiment duration tolerance is invalid")
    target_metadata = inspect_wav(target_reference)
    if output.resolve() == target_reference.resolve():
        raise ValueError("experiment output must not overwrite the target reference")
    guide_artifact: ExperimentalAudio | None = None
    converted_artifact: ExperimentalAudio | None = None
    try:
        guide_artifact = guide_provider.generate_guide(
            text=text,
            language=language,
            style=style,
        )
        guide_metadata = inspect_wav(guide_artifact.path)
        if guide_artifact.path.resolve() == target_reference.resolve():
            raise ValueError("guide provider must not overwrite the target reference")
    finally:
        guide_provider.release()
    try:
        assert guide_artifact is not None
        converted_artifact = conversion_provider.transfer_timbre(
            guide=guide_artifact.path,
            target_reference=target_reference,
            output=output,
        )
        converted_metadata = inspect_wav(converted_artifact.path)
        if converted_artifact.path.resolve() in {
            target_reference.resolve(),
            guide_artifact.path.resolve(),
        }:
            raise ValueError("converted output must be a distinct artifact")
    finally:
        conversion_provider.release()
    duration_delta_ratio = (
        abs(converted_metadata.duration_seconds - guide_metadata.duration_seconds)
        / guide_metadata.duration_seconds
    )
    if duration_delta_ratio > maximum_duration_delta_ratio:
        raise ValueError("voice conversion did not preserve guide timing")
    if inspect_wav(target_reference) != target_metadata:
        raise ValueError("voice conversion mutated the immutable target reference")
    payload = {
        "version": EXPERIMENT_VERSION,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "language": language,
        "style": style,
        "guide_provider": guide_artifact.provider,
        "guide_provider_identity": guide_artifact.provider_identity,
        "guide_request_identity": guide_artifact.request_identity,
        "conversion_provider": converted_artifact.provider,
        "conversion_provider_identity": converted_artifact.provider_identity,
        "conversion_request_identity": converted_artifact.request_identity,
        "target_reference_sha256": target_metadata.sha256,
        "guide": asdict(guide_metadata),
        "converted": asdict(converted_metadata),
        "duration_delta_ratio": round(duration_delta_ratio, 6),
        "production_promoted": False,
    }
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ProsodyTransferResult(
        identity=identity,
        guide=guide_metadata,
        converted=converted_metadata,
        guide_diagnostics=analyze_audio(guide_artifact.path),
        converted_diagnostics=analyze_audio(converted_artifact.path),
        manifest=payload,
    )
