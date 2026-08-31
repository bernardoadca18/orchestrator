"""Read-only aggregation of real-take pronunciation/ASR disagreements."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.models.workflow import AudioBloco, AudioBlocoTake
from app.services.pronunciation import PronunciationPreprocessor


class PronunciationCandidateService:
    def __init__(self, session: AsyncSession, config: Settings = settings) -> None:
        self.session = session
        self.resolver = PronunciationPreprocessor.load(config.pronunciation_glossary_path)

    async def aggregate(self, limit: int = 100) -> dict[str, Any]:
        rows = (
            await self.session.execute(
                select(AudioBlocoTake, AudioBloco.workflow_execucao_id)
                .join(AudioBloco, AudioBloco.id == AudioBlocoTake.audio_bloco_id)
                .where(
                    AudioBlocoTake.status == "completed",
                    AudioBlocoTake.is_current.is_(True),
                    AudioBlocoTake.content_quality.is_not(None),
                )
                .order_by(AudioBlocoTake.completed_at.desc())
                .limit(500)
            )
        ).all()
        grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "occurrences": 0,
                "workflow_ids": set(),
                "take_ids": set(),
                "block_ids": set(),
                "examples": [],
                "first_seen": None,
                "last_seen": None,
            }
        )
        for take, workflow_id in rows:
            report = take.content_quality or {}
            metrics = report.get("metrics") or {}
            for item in metrics.get("pronunciation_or_asr_mismatches") or []:
                expected = str(item.get("expected") or "").strip()
                observed = str(item.get("observed") or "").strip()
                if not expected or not observed:
                    continue
                entry = grouped[(expected.casefold(), observed.casefold())]
                entry["expected"] = expected
                entry["observed"] = observed
                entry["occurrences"] += int(item.get("occurrences") or 1)
                entry["workflow_ids"].add(str(workflow_id))
                entry["take_ids"].add(str(take.id))
                entry["block_ids"].add(str(take.audio_bloco_id))
                timestamp = take.completed_at or take.created_at
                if entry["first_seen"] is None or timestamp < entry["first_seen"]:
                    entry["first_seen"] = timestamp
                if entry["last_seen"] is None or timestamp > entry["last_seen"]:
                    entry["last_seen"] = timestamp
                if len(entry["examples"]) < 5:
                    canonical = take.canonical_text or ""
                    location = canonical.casefold().find(expected.casefold())
                    start = max(0, location - 80) if location >= 0 else 0
                    end = min(
                        len(canonical), (location + len(expected) + 80) if location >= 0 else 180
                    )
                    entry["examples"].append(
                        {
                            "workflow_id": str(workflow_id),
                            "take_id": str(take.id),
                            "block_id": str(take.audio_bloco_id),
                            "context": canonical[start:end],
                            "audio_preview_url": (
                                f"/workflow/{workflow_id}/blocos/{take.audio_bloco_id}/audio"
                            ),
                            "timestamp_precision": "block_only",
                        }
                    )

        candidates: list[dict[str, Any]] = []
        for entry in grouped.values():
            resolved = self.resolver.preprocess(
                entry["expected"], locale="pt-BR", canonical_text=entry["expected"]
            )
            candidates.append(
                {
                    "expected": entry["expected"],
                    "observed": entry["observed"],
                    "occurrences": entry["occurrences"],
                    "workflow_count": len(entry["workflow_ids"]),
                    "workflow_ids": sorted(entry["workflow_ids"]),
                    "take_ids": sorted(entry["take_ids"]),
                    "block_ids": sorted(entry["block_ids"]),
                    "examples": entry["examples"],
                    "first_seen": entry["first_seen"].isoformat()
                    if isinstance(entry["first_seen"], datetime)
                    else None,
                    "last_seen": entry["last_seen"].isoformat()
                    if isinstance(entry["last_seen"], datetime)
                    else None,
                    "active_lexicon_match": bool(resolved.applied_replacements),
                    "active_replacements": list(resolved.applied_replacements),
                    "review_status": "pending",
                    "policy": "diagnostic_only_never_auto_promoted",
                }
            )
        candidates.sort(
            key=lambda item: (-item["workflow_count"], -item["occurrences"], item["expected"])
        )
        return {
            "version": "pronunciation_candidate_aggregation_v2",
            "lexicon_identity": self.resolver.identity(),
            "candidate_count": len(candidates),
            "candidates": candidates[:limit],
        }
