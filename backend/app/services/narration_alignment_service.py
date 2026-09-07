"""Stage 13 orchestration over immutable script/audio/transcription evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.models.workflow import (
    NarrationAlignment,
    NarrationBlockAlignment,
    TranscricaoAudio,
    VisualQueryNarrationBinding,
    WorkflowExecucao,
)
from app.repositories.narration_alignment_repository import NarrationAlignmentRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.services.content_coverage import (
    ContentCoverageLimitError,
    ContentCoverageSegment,
    ContentCoverageThresholds,
)
from app.services.narration_alignment_algorithm import (
    AlignmentAlgorithmError,
    BlockRange,
    TimedWord,
    build_block_text_alignment,
    match_excerpt,
    partition_words_by_block,
    resolve_match_timestamps,
    validate_timed_words,
)
from app.services.narration_quality import NarrationSpeechQualityPolicy

logger = logging.getLogger(__name__)

ALIGNMENT_STRATEGY_VERSION = "narration_alignment_v2"


class NarrationAlignmentNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class AlignmentIdentity:
    script_fingerprint: str
    transcription_fingerprint: str
    fingerprint: str


def _canonical_hash(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _script_identity(workflow: WorkflowExecucao) -> str:
    blocks: list[dict[str, object]] = []
    raw_blocks = workflow.roteiro_snapshot.get("blocos", [])
    if not isinstance(raw_blocks, list):
        raw_blocks = []
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict):
            blocks.append({"order": index, "invalid": True})
            continue
        queries = raw.get("visual_queries", [])
        if not isinstance(queries, list):
            queries = []
        blocks.append(
            {
                "order": raw.get("ordem", index),
                "canonical_text": raw.get("conteudo"),
                "visual_queries": [
                    {
                        "id": query.get("id"),
                        "order": query.get("order", query_index),
                        "narration_excerpt": query.get("narration_excerpt"),
                    }
                    for query_index, query in enumerate(queries)
                    if isinstance(query, dict)
                ],
            }
        )
    return _canonical_hash({"version": "stage_13_script_v1", "blocks": blocks})


def _transcription_identity(transcription: TranscricaoAudio | None) -> str:
    if transcription is None:
        return _canonical_hash({"state": "missing"})
    return _canonical_hash(
        {
            "id": str(transcription.id),
            "audio_sha256": transcription.audio_sha256,
            "provider": transcription.provider,
            "model": transcription.model,
            "language": transcription.language,
            "words": transcription.palavras_timestamps,
        }
    )


def alignment_identity(
    workflow: WorkflowExecucao,
    transcription: TranscricaoAudio | None,
    *,
    strategy_version: str = ALIGNMENT_STRATEGY_VERSION,
    quality_policy_identity: str = "legacy-unversioned-quality-policy",
) -> AlignmentIdentity:
    script_fingerprint = _script_identity(workflow)
    transcription_fingerprint = _transcription_identity(transcription)
    blocks = sorted(workflow.blocos or [], key=lambda block: block.ordem)
    fingerprint = _canonical_hash(
        {
            "version": "stage_13_alignment_fingerprint_v2",
            "final_audio_sha256": workflow.audio_final_sha256,
            "script_fingerprint": script_fingerprint,
            "transcription_fingerprint": transcription_fingerprint,
            "strategy_version": strategy_version,
            "quality_policy_identity": quality_policy_identity,
            "blocks": [
                {
                    "order": block.ordem,
                    "synthesis_identity": block.synthesis_identity,
                    "canonical_hash": block.source_text_sha256,
                    "synthesis_hash": block.synthesis_text_sha256,
                    "start": block.narration_start_seconds,
                    "end": block.narration_end_seconds,
                }
                for block in blocks
            ],
        }
    )
    return AlignmentIdentity(script_fingerprint, transcription_fingerprint, fingerprint)


class NarrationAlignmentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        config: Settings = settings,
        strategy_version: str | None = None,
    ) -> None:
        self.session = session
        self.workflows = WorkflowRepository(session)
        self.alignments = NarrationAlignmentRepository(session)
        self.strategy_version = strategy_version or config.narration_alignment_strategy_version
        self.fuzzy_threshold = config.narration_alignment_fuzzy_min_score
        self.ambiguity_delta = config.narration_alignment_ambiguity_delta
        self.minimum_speech_coverage = config.narration_alignment_min_speech_coverage
        self.block_boundary_tolerance_seconds = config.narration_alignment_block_tolerance_seconds
        self.transcription_tolerance_seconds = (
            config.narration_alignment_timestamp_tolerance_seconds
        )
        self.max_tokens_per_sequence = config.narration_alignment_max_tokens
        self.max_alignment_cells = config.narration_alignment_max_cells
        self.quality_policy = NarrationSpeechQualityPolicy(
            version=config.narration_quality_policy_version,
            minimum_speech_coverage=self.minimum_speech_coverage,
            max_repeated_ngram_occurrences=(
                config.narration_quality_max_repeated_ngram_occurrences
            ),
            content_thresholds=ContentCoverageThresholds(
                minimum_coverage_ratio=config.audio_content_minimum_coverage_ratio,
                maximum_deletion_ratio=config.audio_content_maximum_deletion_ratio,
                maximum_insertion_ratio=config.audio_content_maximum_insertion_ratio,
                maximum_repetition_ratio=config.audio_content_maximum_repetition_ratio,
                maximum_missing_contiguous_tokens=config.audio_content_maximum_missing_tokens,
                maximum_trailing_missing_tokens=config.audio_content_maximum_trailing_missing_tokens,
            ),
        )

    async def current(self, workflow_id: uuid.UUID) -> NarrationAlignment | None:
        workflow = await self.workflows.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            raise NarrationAlignmentNotFoundError("Workflow não encontrado")
        identity = alignment_identity(
            workflow,
            workflow.transcricao,
            strategy_version=self.strategy_version,
            quality_policy_identity=self.quality_policy.identity,
        )
        return await self.alignments.get_by_fingerprint(identity.fingerprint, eager=True)

    async def ensure(
        self, workflow_id: uuid.UUID, *, force: bool = False
    ) -> tuple[NarrationAlignment, bool]:
        workflow = await self.workflows.obter_por_id(
            workflow_id, com_blocos=True, com_transcricao=True
        )
        if workflow is None:
            raise NarrationAlignmentNotFoundError("Workflow não encontrado")
        transcription = workflow.transcricao
        identity = alignment_identity(
            workflow,
            transcription,
            strategy_version=self.strategy_version,
            quality_policy_identity=self.quality_policy.identity,
        )
        alignment, created = await self.alignments.create_or_get(
            workflow_id=workflow.id,
            transcription_id=transcription.id if transcription else None,
            final_audio_sha256=workflow.audio_final_sha256,
            script_fingerprint=identity.script_fingerprint,
            transcription_fingerprint=identity.transcription_fingerprint,
            strategy_version=self.strategy_version,
            fingerprint=identity.fingerprint,
        )
        if alignment.status == "completed" and not force:
            completed = await self.alignments.reload(alignment)
            await self._sync_audio_quality_phase(workflow, completed)
            return completed, False
        if alignment.status == "processing" and not force:
            return await self.alignments.reload(alignment), False

        precondition = self._precondition_error(workflow, transcription)
        if precondition:
            await self.alignments.clear_outputs(alignment)
            await self.alignments.mark_blocked(alignment, precondition)
            return await self.alignments.reload(alignment), created

        started = time.perf_counter()
        await self.alignments.mark_processing(alignment)
        await self.alignments.clear_outputs(alignment)
        try:
            blocks, bindings, metrics = self._build_outputs(workflow, transcription)
            metrics["processing_seconds"] = round(time.perf_counter() - started, 6)
            for block in blocks:
                block.narration_alignment_id = alignment.id
            for binding in bindings:
                binding.narration_alignment_id = alignment.id
            await self.alignments.replace_outputs(
                alignment, blocks=blocks, bindings=bindings, metrics=metrics
            )
            speech_quality = cast(dict[str, object], metrics["speech_quality"])
            if speech_quality["status"] == "invalid":
                await self.alignments.mark_blocked(
                    alignment,
                    "final narration failed the versioned speech-quality policy",
                )
        except Exception as exc:
            logger.exception(
                "narration alignment failed without invalidating final audio",
                extra={
                    "workflow_id": str(workflow.id),
                    "alignment_id": str(alignment.id),
                    "strategy_version": self.strategy_version,
                },
            )
            await self.alignments.mark_failed(alignment, self._safe_error(exc))
        result = await self.alignments.reload(alignment)
        await self._sync_audio_quality_phase(workflow, result)
        logger.info(
            "narration alignment finished",
            extra={
                "workflow_id": str(workflow.id),
                "alignment_id": str(alignment.id),
                "strategy_version": self.strategy_version,
                "alignment_status": result.status,
                "resolved_bindings": result.resolved_visual_queries,
                "unresolved_bindings": result.unresolved_visual_queries,
                "ambiguous_bindings": result.ambiguous_visual_queries,
                "processing_seconds": (result.metrics or {}).get("processing_seconds"),
            },
        )
        return result, created

    async def _sync_audio_quality_phase(
        self, workflow: WorkflowExecucao, alignment: NarrationAlignment
    ) -> None:
        if workflow.audio_phase != "quality_unvalidated" or alignment.status != "completed":
            return
        speech_quality = (alignment.metrics or {}).get("speech_quality")
        if not isinstance(speech_quality, dict):
            return
        quality_metrics = speech_quality.get("metrics")
        if not isinstance(quality_metrics, dict):
            return
        content = quality_metrics.get("content_coverage")
        if not isinstance(content, dict) or content.get("status") != "passed":
            return
        await self.workflows.atualizar_status(
            workflow.id,
            workflow.status,
            audio_phase="completed",
        )

    def _precondition_error(
        self, workflow: WorkflowExecucao, transcription: TranscricaoAudio | None
    ) -> str | None:
        if not workflow.audio_final_sha256 or not workflow.duracao_audio_segundos:
            return "final narration identity is unavailable"
        if workflow.status != "audio_concluido":
            return "final narration is not completed"
        if transcription is None:
            return "final narration transcription is unavailable"
        if transcription.audio_sha256 != workflow.audio_final_sha256:
            return "transcription belongs to a different final narration"
        if not workflow.blocos:
            return "audio blocks are unavailable"
        if any(
            block.narration_start_seconds is None
            or block.narration_end_seconds is None
            or block.synthesis_text is None
            for block in workflow.blocos
        ):
            return "audio block alignment metadata is incomplete"
        try:
            script_blocks = self._script_blocks_by_order(workflow)
        except AlignmentAlgorithmError as exc:
            return str(exc)
        for block in workflow.blocos:
            script_block = script_blocks.get(block.ordem)
            if script_block is None or script_block.get("conteudo") != block.texto:
                return "audio blocks belong to a different canonical script snapshot"
        return None

    def _build_outputs(
        self, workflow: WorkflowExecucao, transcription: TranscricaoAudio | None
    ) -> tuple[
        list[NarrationBlockAlignment],
        list[VisualQueryNarrationBinding],
        dict[str, object],
    ]:
        assert transcription is not None
        assert workflow.duracao_audio_segundos is not None
        timed_words = self._parse_words(transcription.palavras_timestamps)
        validate_timed_words(
            timed_words,
            duration_seconds=workflow.duracao_audio_segundos,
            tolerance_seconds=self.transcription_tolerance_seconds,
        )
        audio_blocks = sorted(workflow.blocos, key=lambda item: item.ordem)
        ranges = tuple(
            BlockRange(
                order=block.ordem,
                start_seconds=cast(float, block.narration_start_seconds),
                end_seconds=cast(float, block.narration_end_seconds),
            )
            for block in audio_blocks
        )
        words_by_block = partition_words_by_block(
            timed_words,
            ranges,
            tolerance_seconds=self.block_boundary_tolerance_seconds,
        )
        script_blocks = self._script_blocks_by_order(workflow)
        persisted_blocks: list[NarrationBlockAlignment] = []
        bindings: list[VisualQueryNarrationBinding] = []
        seen_query_ids: set[uuid.UUID] = set()

        for block in audio_blocks:
            canonical = block.texto
            synthesis = block.synthesis_text or block.texto
            block_words = words_by_block.get(block.ordem, ())
            text_alignment = build_block_text_alignment(
                canonical,
                synthesis,
                block_words,
                max_tokens=self.max_tokens_per_sequence,
                max_cells=self.max_alignment_cells,
            )
            if not block_words:
                block_status = "no_words"
                diagnostic_reason = "no ASR words assigned to block"
            elif text_alignment.overall_coverage < self.minimum_speech_coverage:
                block_status = "low_coverage"
                diagnostic_reason = "canonical-to-speech coverage below threshold"
            else:
                block_status = "aligned"
                diagnostic_reason = None
            persisted_blocks.append(
                NarrationBlockAlignment(
                    narration_alignment_id=uuid.UUID(int=0),  # replaced below
                    audio_bloco_id=block.id,
                    block_order=block.ordem,
                    status=block_status,
                    narration_start_seconds=cast(float, block.narration_start_seconds),
                    narration_end_seconds=cast(float, block.narration_end_seconds),
                    canonical_token_count=len(text_alignment.canonical_tokens),
                    synthesis_token_count=len(text_alignment.synthesis_tokens),
                    asr_word_count=len(block_words),
                    canonical_synthesis_coverage=text_alignment.canonical_synthesis_coverage,
                    synthesis_asr_coverage=text_alignment.synthesis_asr_coverage,
                    overall_coverage=text_alignment.overall_coverage,
                    diagnostics={"reason": diagnostic_reason} if diagnostic_reason else None,
                )
            )

            script_block = script_blocks.get(block.ordem, {})
            raw_queries = script_block.get("visual_queries", [])
            if not isinstance(raw_queries, list):
                raise AlignmentAlgorithmError("visual_queries must be a list")
            last_resolved_char: int | None = None
            for fallback_order, query in enumerate(raw_queries):
                if not isinstance(query, dict):
                    raise AlignmentAlgorithmError("visual query must be an object")
                query_id = self._query_id(query)
                if query_id in seen_query_ids:
                    raise AlignmentAlgorithmError("visual query ID is duplicated")
                seen_query_ids.add(query_id)
                query_order = int(query.get("order", fallback_order))
                excerpt = query.get("narration_excerpt")
                if excerpt is not None and not isinstance(excerpt, str):
                    raise AlignmentAlgorithmError("narration_excerpt must be text or null")
                excerpt_match = match_excerpt(
                    canonical,
                    excerpt,
                    after_char=last_resolved_char,
                    fuzzy_threshold=self.fuzzy_threshold,
                    ambiguity_delta=self.ambiguity_delta,
                )
                status, start, end, speech_coverage, reason = resolve_match_timestamps(
                    excerpt_match,
                    text_alignment,
                    minimum_coverage=self.minimum_speech_coverage,
                )
                if status == "resolved":
                    assert start is not None and end is not None
                    start = max(cast(float, block.narration_start_seconds), start)
                    end = min(cast(float, block.narration_end_seconds), end)
                    last_resolved_char = excerpt_match.end_char
                confidence = excerpt_match.score * speech_coverage if status == "resolved" else 0.0
                bindings.append(
                    VisualQueryNarrationBinding(
                        narration_alignment_id=uuid.UUID(int=0),  # replaced below
                        audio_bloco_id=block.id,
                        visual_query_id=query_id,
                        block_order=block.ordem,
                        query_order=query_order,
                        status=status,
                        narration_start_seconds=start,
                        narration_end_seconds=end,
                        excerpt_match_method=excerpt_match.method,
                        excerpt_match_score=round(excerpt_match.score, 6),
                        speech_alignment_coverage=round(speech_coverage, 6),
                        binding_confidence=round(confidence, 6),
                        canonical_start_char=excerpt_match.start_char,
                        canonical_end_char=excerpt_match.end_char,
                        reason=reason,
                        diagnostics={"excerpt_status": excerpt_match.status},
                    )
                )

        # Parent IDs are assigned before this method is called; placeholders are
        # replaced by the caller immediately before persistence.
        average_coverage = (
            sum(block.overall_coverage for block in persisted_blocks) / len(persisted_blocks)
            if persisted_blocks
            else 0.0
        )
        total_queries = len(bindings)
        resolved = sum(binding.status == "resolved" for binding in bindings)
        metrics: dict[str, object] = {
            "average_block_coverage": round(average_coverage, 6),
            "visual_query_resolution_rate": round(resolved / total_queries, 6)
            if total_queries
            else 1.0,
            "word_count": len(timed_words),
            "algorithm_complexity": (
                "per-block bounded windows; O(max_window_source_tokens * "
                "max_window_target_tokens) peak matrix"
            ),
        }
        content_segments = tuple(
            ContentCoverageSegment(
                key=f"block:{block.ordem}",
                reference_text=block.synthesis_text or block.texto,
                hypothesis_text=" ".join(
                    word.word for word in words_by_block.get(block.ordem, ())
                ),
            )
            for block in audio_blocks
        )
        quality = self.quality_policy.evaluate(
            persisted_blocks,
            [word.word for word in timed_words],
            content_segments=content_segments,
        )
        metrics["speech_quality"] = quality.to_dict()
        return persisted_blocks, bindings, metrics

    @staticmethod
    def _parse_words(raw_words: list[dict]) -> tuple[TimedWord, ...]:
        words: list[TimedWord] = []
        for raw in raw_words or []:
            try:
                words.append(
                    TimedWord(
                        word=str(raw["palavra"]),
                        start_seconds=float(raw["inicio"]),
                        end_seconds=float(raw["fim"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AlignmentAlgorithmError("transcription word payload is malformed") from exc
        return tuple(words)

    @staticmethod
    def _script_blocks_by_order(workflow: WorkflowExecucao) -> dict[int, dict[str, Any]]:
        raw_blocks = workflow.roteiro_snapshot.get("blocos", [])
        if not isinstance(raw_blocks, list):
            raise AlignmentAlgorithmError("script blocks must be a list")
        result: dict[int, dict[str, Any]] = {}
        for index, raw in enumerate(raw_blocks):
            if not isinstance(raw, dict):
                raise AlignmentAlgorithmError("script block must be an object")
            order = int(raw.get("ordem", index))
            if order in result:
                raise AlignmentAlgorithmError("script block order is duplicated")
            result[order] = raw
        return result

    @staticmethod
    def _query_id(query: dict[str, Any]) -> uuid.UUID:
        try:
            return uuid.UUID(str(query["id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise AlignmentAlgorithmError("visual query ID is invalid") from exc

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, AlignmentAlgorithmError):
            return str(exc)
        if isinstance(exc, ContentCoverageLimitError):
            return "narration content coverage exceeded a bounded evaluation window"
        return "unexpected narration alignment failure"
