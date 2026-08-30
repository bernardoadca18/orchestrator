"""Robust speaking-rate snapshots derived only from accepted immutable takes."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import statistics
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.models.workflow import AudioBloco, AudioBlocoTake, WorkflowExecucao

CALIBRATION_VERSION = "accepted_take_speaking_rate_v1"
_WORD_RE = re.compile(r"\b[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*\b", re.UNICODE)


def _trimmed_mean(values: list[float]) -> float:
    ordered = sorted(values)
    trim = int(len(ordered) * 0.1) if len(ordered) >= 10 else 0
    selected = ordered[trim : len(ordered) - trim] if trim else ordered
    return statistics.fmean(selected)


def _identity(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SpeakingRateCalibrationService:
    def __init__(self, session: AsyncSession, config: Settings = settings) -> None:
        self.session = session
        self.config = config

    async def snapshot(self, production_profile: str | None = None) -> dict[str, Any]:
        stmt = (
            select(AudioBlocoTake, WorkflowExecucao.perfil_producao)
            .join(AudioBloco, AudioBloco.id == AudioBlocoTake.audio_bloco_id)
            .join(WorkflowExecucao, WorkflowExecucao.id == AudioBloco.workflow_execucao_id)
            .where(
                AudioBlocoTake.status == "completed",
                AudioBlocoTake.is_current.is_(True),
                AudioBlocoTake.audio_duration_seconds.is_not(None),
                AudioBlocoTake.synthesis_text.is_not(None),
            )
            .order_by(AudioBlocoTake.completed_at.desc())
            .limit(200)
        )
        if production_profile:
            stmt = stmt.where(WorkflowExecucao.perfil_producao == production_profile)
        rows = (await self.session.execute(stmt)).all()

        samples: list[dict[str, Any]] = []
        for take, profile in rows:
            duration = float(take.audio_duration_seconds or 0)
            text = " ".join((take.synthesis_text or "").split())
            words = len(_WORD_RE.findall(text))
            if duration < 5 or words < 10:
                continue
            integrity = take.artifact_integrity or {}
            if integrity and integrity.get("status") not in {None, "valid", "passed"}:
                continue
            samples.append(
                {
                    "id": str(take.id),
                    "completed_at": take.completed_at.isoformat() if take.completed_at else None,
                    "words_per_minute": words * 60 / duration,
                    "chars_per_second": len(text) / duration,
                    "voice_profile": take.voice_profile,
                    "generation_profile": take.generation_profile,
                    "production_profile": profile,
                }
            )

        fallback = len(samples) < 3
        if fallback:
            words_per_minute = self.config.speaking_rate_fallback_words_per_minute
            chars_per_second = self.config.speaking_rate_fallback_chars_per_second
            voice_profile = self.config.voice_profile
            generation_profile = self.config.voice_generation_profile
            source_window = "configured_fallback"
        else:
            word_rates = [item["words_per_minute"] for item in samples]
            char_rates = [item["chars_per_second"] for item in samples]
            words_per_minute = _trimmed_mean(word_rates)
            chars_per_second = _trimmed_mean(char_rates)
            voice_profile = samples[0]["voice_profile"]
            generation_profile = samples[0]["generation_profile"]
            source_window = f"latest_{len(samples)}_accepted_takes"

        identity_payload = {
            "version": CALIBRATION_VERSION,
            "sample_ids": [item["id"] for item in samples],
            "words_per_minute": round(words_per_minute, 6),
            "chars_per_second": round(chars_per_second, 6),
            "voice_profile": voice_profile,
            "generation_profile": generation_profile,
            "production_profile": production_profile,
            "fallback": fallback,
        }
        return {
            "version": CALIBRATION_VERSION,
            "identity": _identity(identity_payload),
            "words_per_minute": round(words_per_minute, 3),
            "chars_per_second": round(chars_per_second, 3),
            "median_words_per_minute": round(
                statistics.median([item["words_per_minute"] for item in samples]), 3
            )
            if samples
            else None,
            "median_chars_per_second": round(
                statistics.median([item["chars_per_second"] for item in samples]), 3
            )
            if samples
            else None,
            "sample_count": len(samples),
            "source_window": source_window,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "fallback": fallback,
            "language": "pt-BR",
            "voice_profile": voice_profile,
            "generation_profile": generation_profile,
            "production_profile": production_profile,
        }
