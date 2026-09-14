"""Deterministic Program Audio V1 planning and controlled music registry access."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import stat
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import Settings
from app.models.workflow import ProductionTimeline, WorkflowExecucao
from app.schemas.workflow import AudioMixPlanV1Out, ProgramMusicAssetOut

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SPEECH_REGIONS = 512
logger = logging.getLogger(__name__)


class ProgramAudioError(ValueError):
    """Stable planning/registry failure with a public-safe category."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class MusicRegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(pattern=r"^music_asset_registry_v1$")
    assets: list[ProgramMusicAssetOut] = Field(default_factory=list, max_length=1000)
    workflow_overrides: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self) -> MusicRegistryDocument:
        seen: dict[str, str] = {}
        for asset in self.assets:
            previous = seen.get(asset.asset_id)
            if previous is not None:
                suffix = " with different SHA" if previous != asset.sha256 else ""
                raise ValueError(f"duplicate music asset id{suffix}")
            seen[asset.asset_id] = asset.sha256
            if asset.vocals:
                raise ValueError("Program Audio V1 registry supports instrumental assets only")
            if not asset.moods or not asset.styles:
                raise ValueError("music asset requires mood and style metadata")
            for tag in [*asset.moods, *asset.styles]:
                if not TAG_PATTERN.fullmatch(tag):
                    raise ValueError("music asset tags must already be canonical")
        for workflow_id, asset_id in self.workflow_overrides.items():
            try:
                uuid.UUID(workflow_id)
            except ValueError as exc:
                raise ValueError("workflow override key must be a UUID") from exc
            if asset_id not in seen:
                raise ValueError("workflow override references an unknown asset")
        return self


class AudioIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    music_enabled: bool
    moods: list[str] = Field(default_factory=list, max_length=32)
    energy: float | None = Field(default=None, ge=0, le=1)
    styles: list[str] = Field(default_factory=list, max_length=32)
    vocals: bool = False
    transition: str | None = None
    sfx_intent: list[str] = Field(default_factory=list, max_length=32)


def canonical_hash(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        return item

    encoded = json.dumps(
        normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normal_tag(value: str) -> str | None:
    folded = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_value = "".join(character for character in folded if not unicodedata.combining(character))
    normalized = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return normalized if TAG_PATTERN.fullmatch(normalized) else None


def _normalized_tags(values: list[str]) -> set[str]:
    return {tag for value in values if (tag := _normal_tag(value)) is not None}


def load_music_registry(config: Settings) -> MusicRegistryDocument:
    registry_path = config.music_registry_path.resolve()
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        return MusicRegistryDocument.model_validate(raw)
    except FileNotFoundError as exc:
        raise ProgramAudioError("music_resolution_failed", "Music registry is unavailable") from exc
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ProgramAudioError("music_resolution_failed", "Music registry is invalid") from exc


def _asset_extension(asset: ProgramMusicAssetOut) -> str:
    return "wav" if asset.format == "wav" else "flac"


def music_asset_path(config: Settings, asset: ProgramMusicAssetOut) -> Path:
    root = config.music_library_root.resolve()
    candidate = (root / f"{asset.sha256}.{_asset_extension(asset)}").resolve()
    if candidate.parent != root:
        raise ProgramAudioError("music_asset_unavailable", "Music asset path is invalid")
    return candidate


def verify_music_asset(config: Settings, asset: ProgramMusicAssetOut) -> Path:
    candidate = music_asset_path(config, asset)
    try:
        details = candidate.stat()
    except OSError as exc:
        raise ProgramAudioError("music_asset_unavailable", "Music asset is unavailable") from exc
    if not stat.S_ISREG(details.st_mode) or details.st_size != asset.byte_size:
        raise ProgramAudioError("music_asset_integrity_failed", "Music asset size mismatch")
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ProgramAudioError("music_asset_unavailable", "Music asset cannot be read") from exc
    if digest.hexdigest() != asset.sha256:
        raise ProgramAudioError("music_asset_integrity_failed", "Music asset SHA mismatch")
    return candidate


def resolve_music_asset(
    registry: MusicRegistryDocument,
    intents: list[AudioIntent],
    *,
    workflow_id: uuid.UUID,
    override_asset_id: str | None = None,
) -> ProgramMusicAssetOut:
    enabled = [intent for intent in intents if intent.music_enabled]
    if not enabled:
        raise ProgramAudioError("music_resolution_failed", "No block requests music")
    if any(intent.vocals for intent in enabled):
        raise ProgramAudioError(
            "music_resolution_failed", "Program Audio V1 does not support requested vocals"
        )
    selected_override = override_asset_id or registry.workflow_overrides.get(str(workflow_id))
    assets = {asset.asset_id: asset for asset in registry.assets}
    if selected_override is not None:
        asset = assets.get(selected_override)
        if asset is None or asset.vocals or not asset.monetization_allowed:
            raise ProgramAudioError("music_resolution_failed", "Music override is invalid")
        logger.info(
            "music asset resolved",
            extra={
                "event": "music_asset_resolved",
                "workflow_id": str(workflow_id),
                "asset_id": asset.asset_id,
                "asset_sha256": asset.sha256,
                "resolution": "operator_override",
            },
        )
        return asset

    requested_moods = set().union(*(_normalized_tags(item.moods) for item in enabled))
    requested_styles = set().union(*(_normalized_tags(item.styles) for item in enabled))
    energies = [item.energy for item in enabled if item.energy is not None]
    requested_energy = sum(energies) / len(energies) if energies else 0.5
    candidates: list[tuple[float, str, ProgramMusicAssetOut]] = []
    for asset in registry.assets:
        if asset.vocals or not asset.monetization_allowed:
            continue
        mood_overlap = len(requested_moods & set(asset.moods))
        style_overlap = len(requested_styles & set(asset.styles))
        if (requested_moods or requested_styles) and mood_overlap + style_overlap == 0:
            continue
        score = mood_overlap * 100 + style_overlap * 60 - abs(asset.energy - requested_energy) * 20
        candidates.append((score, asset.asset_id, asset))
    if not candidates:
        raise ProgramAudioError("music_resolution_failed", "No compatible music asset exists")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = candidates[0][2]
    logger.info(
        "music asset resolved",
        extra={
            "event": "music_asset_resolved",
            "workflow_id": str(workflow_id),
            "asset_id": selected.asset_id,
            "asset_sha256": selected.sha256,
            "resolution": "deterministic_score",
        },
    )
    return selected


def build_speech_regions(
    words: list[dict[str, Any]],
    *,
    duration_seconds: float,
    merge_gap_seconds: float = 0.35,
) -> list[dict[str, float]]:
    parsed: list[tuple[float, float]] = []
    for raw in words:
        try:
            start = float(raw["inicio"])
            end = float(raw["fim"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProgramAudioError("audio_mix_plan_invalid", "Word timestamp is invalid") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ProgramAudioError("audio_mix_plan_invalid", "Word timestamp range is invalid")
        start = min(start, duration_seconds)
        end = min(end, duration_seconds)
        if end > start:
            parsed.append((start, end))
    parsed.sort()
    merged: list[list[float]] = []
    for start, end in parsed:
        if merged and start - merged[-1][1] <= merge_gap_seconds:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if len(merged) > MAX_SPEECH_REGIONS:
        raise ProgramAudioError("audio_mix_plan_invalid", "Speech envelope exceeds bounded size")
    return [
        {"start_seconds": round(start, 6), "end_seconds": round(end, 6)}
        for start, end in merged
    ]


def _parse_intents(workflow: WorkflowExecucao) -> tuple[list[AudioIntent | None], bool]:
    snapshot = getattr(workflow, "roteiro_snapshot", None) or {}
    raw_blocks = snapshot.get("blocos", [])
    if not isinstance(raw_blocks, list):
        raise ProgramAudioError("audio_mix_plan_invalid", "Script blocks are invalid")
    found = False
    intents: list[AudioIntent | None] = []
    for raw in raw_blocks:
        value = raw.get("audio_intent") if isinstance(raw, dict) else None
        if value is None:
            intents.append(None)
            continue
        found = True
        try:
            intents.append(AudioIntent.model_validate(value))
        except ValidationError as exc:
            raise ProgramAudioError("audio_mix_plan_invalid", "audio_intent is invalid") from exc
    return intents, found


def _transition_preset(value: str | None) -> str:
    normalized = (value or "none").strip().lower()
    if normalized in {"", "none"}:
        return "none_v1"
    if normalized in {"smooth", "rise"}:
        return "smooth_v1"
    raise ProgramAudioError("audio_mix_plan_invalid", "Unsupported audio transition preset")


def build_audio_mix_plan(
    workflow: WorkflowExecucao,
    timeline: ProductionTimeline,
    config: Settings,
    *,
    registry: MusicRegistryDocument | None = None,
    override_asset_id: str | None = None,
) -> AudioMixPlanV1Out | None:
    raw_intents, present = _parse_intents(workflow)
    if not present:
        return None
    duration = float(timeline.duration_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ProgramAudioError("audio_mix_plan_invalid", "Timeline duration is invalid")
    if (
        not workflow.audio_final_sha256
        or not SHA256_PATTERN.fullmatch(workflow.audio_final_sha256)
        or workflow.duracao_audio_segundos is None
        or not math.isclose(workflow.duracao_audio_segundos, duration, abs_tol=0.01)
        or workflow.audio_final_sample_rate != 48_000
        or workflow.audio_final_channels != 1
    ):
        raise ProgramAudioError("audio_mix_plan_invalid", "Narration stem identity is incompatible")
    transcription = workflow.transcricao
    if transcription is None or transcription.audio_sha256 != workflow.audio_final_sha256:
        raise ProgramAudioError("audio_mix_plan_invalid", "Narration timestamps are stale")
    words = transcription.palavras_timestamps or []
    speech_regions = build_speech_regions(words, duration_seconds=duration)
    concrete_intents = [item for item in raw_intents if item is not None]
    any_enabled = any(item.music_enabled for item in concrete_intents)
    music: dict[str, Any]
    if not any_enabled:
        music = {"enabled": False}
    else:
        catalog = registry or load_music_registry(config)
        asset = resolve_music_asset(
            catalog,
            concrete_intents,
            workflow_id=workflow.id,
            override_asset_id=override_asset_id,
        )
        segments: list[dict[str, Any]] = []
        blocks_by_order = {block.ordem: block for block in workflow.blocos}
        for order, intent in enumerate(raw_intents):
            if intent is None or not intent.music_enabled:
                continue
            block = blocks_by_order.get(order)
            if (
                block is None
                or block.narration_start_seconds is None
                or block.narration_end_seconds is None
            ):
                raise ProgramAudioError("audio_mix_plan_invalid", "Block timing is unavailable")
            start = max(0.0, float(block.narration_start_seconds))
            end = min(duration, float(block.narration_end_seconds))
            if end <= start:
                raise ProgramAudioError("audio_mix_plan_invalid", "Block timing is invalid")
            preset = _transition_preset(intent.transition)
            fade = min(0.35 if preset == "smooth_v1" else 0.12, (end - start) / 2)
            energy = intent.energy if intent.energy is not None else 0.5
            segments.append(
                {
                    "start_seconds": round(start, 6),
                    "end_seconds": round(end, 6),
                    "gain_adjustment_db": round((energy - 0.5) * 4, 3),
                    "fade_in_seconds": round(fade, 6),
                    "fade_out_seconds": round(fade, 6),
                    "transition_preset": preset,
                }
            )
        if not segments:
            raise ProgramAudioError("audio_mix_plan_invalid", "Music ranges are unavailable")
        required_end = max(item["end_seconds"] for item in segments)
        loop_policy = "no_loop_v1"
        if required_end > asset.duration_seconds + 0.001:
            if not asset.loopable:
                raise ProgramAudioError(
                    "music_resolution_failed", "Selected music asset cannot cover the timeline"
                )
            loop_policy = "repeat_from_start_v1"
        music = {
            "enabled": True,
            "asset": asset.model_dump(mode="json"),
            "source_start_seconds": 0,
            "source_end_seconds": asset.duration_seconds,
            "loop_policy": loop_policy,
            "normalization_policy": "music_loudnorm_v1",
            "normalization_target_lufs": -23.0,
            "segments": segments,
        }
    content = {
        "schema_version": "audio_mix_plan_v1",
        "strategy_version": "longform_program_audio_v1",
        "profile_id": "music_mix_profile_v1_candidate",
        "duration_seconds": round(duration, 6),
        "narration": {
            "sha256": workflow.audio_final_sha256,
            "duration_seconds": round(float(workflow.duracao_audio_segundos), 6),
            "sample_rate": 48_000,
            "channels": 1,
        },
        "music": music,
        "ducking": {
            "policy_version": "speech_envelope_v1",
            "base_gain_db": -9.0,
            "ducked_gain_db": -20.0,
            "attack_seconds": 0.25,
            "release_seconds": 0.5,
            "merge_gap_seconds": 0.35,
            "speech_regions": speech_regions,
        },
        "program_master": {
            "profile_id": "youtube_program_master_v1_candidate",
            "policy_version": "ffmpeg_loudnorm_two_pass_v1",
            "target_lufs": -14.0,
            "max_true_peak_dbtp": -1.0,
            "target_lra": 11.0,
        },
        "output": {
            "profile_id": "youtube_1080p30_program_audio_v1",
            "codec": "aac",
            "bitrate": "192k",
            "sample_rate": 48_000,
            "channels": 2,
        },
    }
    plan = AudioMixPlanV1Out.model_validate(
        {**content, "fingerprint": canonical_hash(content)}
    )
    logger.info(
        "audio mix plan created",
        extra={
            "event": "audio_mix_plan_created",
            "workflow_id": str(workflow.id),
            "audio_mix_plan_fingerprint": plan.fingerprint,
            "music_enabled": plan.music.enabled,
        },
    )
    return plan
