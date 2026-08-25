"""Persistence boundary for Stage 13 derived narration alignments."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import (
    NarrationAlignment,
    NarrationBlockAlignment,
    VisualQueryNarrationBinding,
)


class NarrationAlignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _eager_options():
        return (
            selectinload(NarrationAlignment.block_alignments),
            selectinload(NarrationAlignment.visual_query_bindings),
        )

    async def get_by_fingerprint(
        self, fingerprint: str, *, eager: bool = False
    ) -> NarrationAlignment | None:
        stmt = select(NarrationAlignment).where(NarrationAlignment.fingerprint == fingerprint)
        if eager:
            stmt = stmt.options(*self._eager_options())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(
        self, alignment_id: uuid.UUID, *, eager: bool = False
    ) -> NarrationAlignment | None:
        stmt = select(NarrationAlignment).where(NarrationAlignment.id == alignment_id)
        if eager:
            stmt = stmt.options(*self._eager_options())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: uuid.UUID) -> Sequence[NarrationAlignment]:
        stmt = (
            select(NarrationAlignment)
            .where(NarrationAlignment.workflow_execucao_id == workflow_id)
            .order_by(NarrationAlignment.criado_em.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def create_or_get(
        self,
        *,
        workflow_id: uuid.UUID,
        transcription_id: uuid.UUID | None,
        final_audio_sha256: str | None,
        script_fingerprint: str,
        transcription_fingerprint: str,
        strategy_version: str,
        fingerprint: str,
    ) -> tuple[NarrationAlignment, bool]:
        existing = await self.get_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False
        alignment = NarrationAlignment(
            workflow_execucao_id=workflow_id,
            transcription_id=transcription_id,
            final_audio_sha256=final_audio_sha256,
            script_fingerprint=script_fingerprint,
            transcription_fingerprint=transcription_fingerprint,
            strategy_version=strategy_version,
            fingerprint=fingerprint,
            status="pending",
        )
        self.session.add(alignment)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            concurrent = await self.get_by_fingerprint(fingerprint)
            if concurrent is None:
                raise
            return concurrent, False
        await self.session.refresh(alignment)
        return alignment, True

    async def mark_processing(self, alignment: NarrationAlignment) -> None:
        alignment.status = "processing"
        alignment.started_at = dt.datetime.now(dt.UTC)
        alignment.completed_at = None
        alignment.error_message = None
        await self.session.commit()

    async def clear_outputs(self, alignment: NarrationAlignment) -> None:
        """Remove only derived rows before an explicit retry/rebuild."""
        await self.session.execute(
            delete(VisualQueryNarrationBinding).where(
                VisualQueryNarrationBinding.narration_alignment_id == alignment.id
            )
        )
        await self.session.execute(
            delete(NarrationBlockAlignment).where(
                NarrationBlockAlignment.narration_alignment_id == alignment.id
            )
        )
        alignment.block_count = 0
        alignment.resolved_visual_queries = 0
        alignment.unresolved_visual_queries = 0
        alignment.ambiguous_visual_queries = 0
        alignment.not_applicable_visual_queries = 0
        alignment.metrics = None
        await self.session.commit()
        self.session.expire(alignment, ["block_alignments", "visual_query_bindings"])

    async def mark_blocked(self, alignment: NarrationAlignment, reason: str) -> None:
        alignment.status = "blocked"
        alignment.error_message = reason
        alignment.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def mark_failed(self, alignment: NarrationAlignment, reason: str) -> None:
        alignment.status = "failed"
        alignment.error_message = reason[:1000]
        alignment.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def replace_outputs(
        self,
        alignment: NarrationAlignment,
        *,
        blocks: Sequence[NarrationBlockAlignment],
        bindings: Sequence[VisualQueryNarrationBinding],
        metrics: dict[str, object],
    ) -> None:
        await self.session.execute(
            delete(VisualQueryNarrationBinding).where(
                VisualQueryNarrationBinding.narration_alignment_id == alignment.id
            )
        )
        await self.session.execute(
            delete(NarrationBlockAlignment).where(
                NarrationBlockAlignment.narration_alignment_id == alignment.id
            )
        )
        self.session.add_all([*blocks, *bindings])
        counts = {
            status: sum(binding.status == status for binding in bindings)
            for status in ("resolved", "unresolved", "ambiguous", "not_applicable")
        }
        alignment.status = "completed"
        alignment.block_count = len(blocks)
        alignment.resolved_visual_queries = counts["resolved"]
        alignment.unresolved_visual_queries = counts["unresolved"]
        alignment.ambiguous_visual_queries = counts["ambiguous"]
        alignment.not_applicable_visual_queries = counts["not_applicable"]
        alignment.metrics = metrics
        alignment.error_message = None
        alignment.completed_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def reload(self, alignment: NarrationAlignment) -> NarrationAlignment:
        loaded = await self.get_by_id(alignment.id, eager=True)
        if loaded is None:
            raise RuntimeError("narration alignment disappeared after persistence")
        return loaded
