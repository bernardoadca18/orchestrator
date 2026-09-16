"""Incremental publication of promoted physical narration chunk edits."""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.models.workflow import (
    AudioBloco,
    AudioBlocoTake,
    AudioChunkTake,
    AudioEditUpdate,
    WorkflowExecucao,
)
from app.repositories.workflow_repository import WorkflowRepository
from app.services.content_locale import workflow_content_locale
from app.services.audio_service import _mastering_config, ensure_transcription
from app.services.audio_serving import AudioArtifactExpectation, verify_audio_artifact
from app.services.mastering import MasteringError, master_narration
from app.services.production_audio import (
    AssemblyResult,
    BlockAssemblyChunk,
    BlockAssemblyManifest,
    ProductionAudioError,
    assemble_narration,
    inspect_wav,
    reassemble_block_from_manifest,
)

logger = logging.getLogger(__name__)


class AudioEditMaterializationError(RuntimeError):
    """A pending edit snapshot could not be safely published."""


@dataclass(frozen=True)
class AudioEditMaterializationResult:
    update_id: uuid.UUID
    workflow_id: uuid.UUID
    status: str
    materialized: bool
    downstream_ready: bool
    final_audio_sha256: str | None
    performed: bool


@dataclass(frozen=True)
class _RebuiltBlock:
    block_id: uuid.UUID
    order: int
    edit_revision: int
    path: Path
    assembly: AssemblyResult
    pause_frames: tuple[int, ...]
    selected_take_ids: tuple[uuid.UUID, ...]
    manifest: BlockAssemblyManifest


class AudioEditMaterializationService:
    """Materialize one durable edit snapshot without invoking TTS."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        config: Settings = settings,
        transcription_provider: Any | None = None,
    ) -> None:
        self.session = session
        self.repo = WorkflowRepository(session)
        self.config = config
        self.transcription_provider = transcription_provider

    async def apply(
        self, update_id: uuid.UUID, *, recovery: bool = False
    ) -> AudioEditMaterializationResult:
        update = await self.repo.claim_audio_edit_update(update_id, recovery=recovery)
        if update is None:
            existing = await self.repo.get_audio_edit_update(update_id)
            if existing is None:
                raise LookupError("audio edit update not found")
            return AudioEditMaterializationResult(
                update_id=existing.id,
                workflow_id=existing.workflow_execucao_id,
                status=existing.status,
                materialized=existing.published_audio_sha256 is not None,
                downstream_ready=existing.status == "completed",
                final_audio_sha256=existing.published_audio_sha256,
                performed=False,
            )

        update_id_value = update.id
        workflow_id_value = update.workflow_execucao_id
        stage = "audio_materialization"
        try:
            workflow = await self.repo.obter_por_id(update.workflow_execucao_id)
            if workflow is None:
                raise LookupError("workflow not found")
            materialized = False
            final_path: Path
            final_sha: str
            if update.published_audio_sha256 is not None:
                if (
                    workflow.audio_final_sha256 != update.published_audio_sha256
                    or workflow.audio_final_path is None
                ):
                    raise AudioEditMaterializationError(
                        "published update no longer matches the current final narration"
                    )
                final_path, _ = verify_audio_artifact(
                    workflow.audio_final_path,
                    AudioArtifactExpectation(
                        sha256=workflow.audio_final_sha256,
                        byte_size=workflow.audio_final_byte_size,
                        duration_seconds=workflow.duracao_audio_segundos,
                        sample_rate=workflow.audio_final_sample_rate,
                        channels=workflow.audio_final_channels,
                        frame_count=workflow.audio_final_frame_count,
                        subtype=workflow.audio_final_subtype,
                    ),
                )
                final_sha = update.published_audio_sha256
            else:
                final_path, final_sha = await self._materialize(update)
                materialized = True

            stage = "downstream_transcription"
            await ensure_transcription(
                self.session,
                update.workflow_execucao_id,
                final_path,
                final_sha,
                provider=self.transcription_provider,
                content_locale=workflow_content_locale(workflow),
            )
            await self.repo.atualizar_status(
                update.workflow_execucao_id,
                "audio_concluido",
                audio_phase="completed",
                erro_mensagem=None,
            )
            await self.repo.atualizar_producao(
                update.workflow_execucao_id,
                status="queued",
                phase="narration_alignment",
                error_category=None,
                error_message=None,
            )
            await self.repo.mark_audio_edit_update_completed(update.id)
            return AudioEditMaterializationResult(
                update_id=update.id,
                workflow_id=update.workflow_execucao_id,
                status="completed",
                materialized=materialized,
                downstream_ready=True,
                final_audio_sha256=final_sha,
                performed=True,
            )
        except Exception as exc:
            await self.session.rollback()
            failure_stage = (
                "edit_snapshot_stale"
                if isinstance(exc, AudioEditMaterializationError)
                and str(exc).startswith("edit_snapshot_stale:")
                else stage
            )
            await self.repo.mark_audio_edit_update_failed(
                update_id_value, stage=failure_stage, message=self._safe_message(exc)
            )
            current = await self.repo.obter_por_id(workflow_id_value)
            failed_update = await self.repo.get_audio_edit_update(update_id_value)
            if (
                current is not None
                and failed_update is not None
                and failed_update.published_audio_sha256 is not None
            ):
                await self.repo.atualizar_producao(
                    current.id,
                    status="failed",
                    phase="narration_alignment",
                    error_category=failure_stage,
                    error_message=self._safe_message(exc),
                    complete=True,
                )
            raise

    async def _materialize(self, update: AudioEditUpdate) -> tuple[Path, str]:
        snapshot = update.edit_snapshot
        if not isinstance(snapshot, dict) or snapshot.get("version") != "audio_edit_snapshot_v1":
            raise AudioEditMaterializationError("audio edit snapshot is missing")
        raw_blocks = snapshot.get("blocks")
        if not isinstance(raw_blocks, list) or not raw_blocks:
            raise AudioEditMaterializationError("audio edit snapshot has no dirty blocks")

        workflow = await self.repo.obter_por_id(update.workflow_execucao_id, com_blocos=True)
        if workflow is None:
            raise LookupError("workflow not found")
        root = Path(self.config.audio_storage_path) / str(workflow.id)
        edit_root = root / "audio-edits" / str(update.id)
        rebuilt: dict[uuid.UUID, _RebuiltBlock] = {}
        for raw_block in raw_blocks:
            rebuilt_block = await self._rebuild_block(workflow, update, raw_block, edit_root)
            rebuilt[rebuilt_block.block_id] = rebuilt_block

        ordered_blocks = sorted(workflow.blocos, key=lambda item: item.ordem)
        block_paths: list[Path] = []
        for block in ordered_blocks:
            replacement = rebuilt.get(block.id)
            if replacement is not None:
                block_paths.append(replacement.path)
                continue
            if not block.audio_path:
                raise AudioEditMaterializationError(
                    f"clean block {block.ordem} has no published artifact"
                )
            path, _ = verify_audio_artifact(
                block.audio_path,
                AudioArtifactExpectation(
                    sha256=block.audio_sha256,
                    byte_size=block.audio_byte_size,
                    duration_seconds=block.duracao_segundos,
                    sample_rate=block.audio_sample_rate,
                    channels=block.audio_channels,
                    frame_count=block.audio_frame_count,
                    subtype=block.audio_subtype,
                ),
            )
            block_paths.append(path)

        raw_path = edit_root / "narration_assembled.wav"
        final_assembly_started = time.monotonic()
        narration = assemble_narration(
            block_paths,
            raw_path,
            sample_rate=self.config.audio_final_sample_rate,
            silence_ms=self.config.silencio_entre_blocos_ms,
        )
        final_assembly_seconds = time.monotonic() - final_assembly_started
        final_path = edit_root / "narration_final.wav"
        mastering_config = _mastering_config(self.config)
        mastering_started = time.monotonic()
        mastered = master_narration(raw_path, final_path, mastering_config)
        mastering_seconds = time.monotonic() - mastering_started
        if (
            abs(mastered.metadata.duration_seconds - narration.metadata.duration_seconds)
            > self.config.audio_duration_tolerance_seconds
        ):
            raise MasteringError("final narration duration changed after mastering")
        final_metadata = inspect_wav(final_path)
        if final_metadata != mastered.metadata:
            raise MasteringError("mastered narration metadata differs after final reopen")

        await self._publish_database_state(
            update,
            workflow,
            rebuilt,
            narration,
            final_path,
            final_metadata,
            mastering_config=asdict(mastering_config),
            mastering_metrics=mastered.to_dict(),
            performance_metrics={
                "final_assembly_seconds": final_assembly_seconds,
                "mastering_seconds": mastering_seconds,
            },
        )
        return final_path, final_metadata.sha256

    async def _rebuild_block(
        self,
        workflow: WorkflowExecucao,
        update: AudioEditUpdate,
        raw_block: object,
        edit_root: Path,
    ) -> _RebuiltBlock:
        if not isinstance(raw_block, dict):
            raise AudioEditMaterializationError("audio edit block snapshot is malformed")
        try:
            block_id = uuid.UUID(str(raw_block["block_id"]))
            order = int(raw_block["order"])
            edit_revision = int(raw_block["edit_revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AudioEditMaterializationError(
                "audio edit block identity is malformed"
            ) from exc
        block = await self.repo.obter_bloco_por_id(block_id)
        if block is None or block.workflow_execucao_id != workflow.id or block.ordem != order:
            raise AudioEditMaterializationError("audio edit block no longer belongs to workflow")
        chunks = list(await self.repo.listar_chunks(block_id))
        raw_chunks = raw_block.get("chunks")
        if not isinstance(raw_chunks, list) or len(raw_chunks) != len(chunks) or not chunks:
            raise AudioEditMaterializationError("physical chunk snapshot is incomplete")

        manifest_chunks: list[BlockAssemblyChunk] = []
        pause_frames: list[int] = []
        selected_take_ids: list[uuid.UUID] = []
        sample_rate: int | None = None
        for chunk, raw_chunk in zip(chunks, raw_chunks, strict=True):
            if not isinstance(raw_chunk, dict):
                raise AudioEditMaterializationError("physical chunk snapshot is malformed")
            try:
                snapshot_chunk_id = uuid.UUID(str(raw_chunk["chunk_id"]))
                take_id = uuid.UUID(str(raw_chunk["take_id"]))
                snapshot_index = int(raw_chunk["chunk_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AudioEditMaterializationError(
                    "physical chunk selection is malformed"
                ) from exc
            if chunk.id != snapshot_chunk_id or chunk.chunk_index != snapshot_index:
                raise AudioEditMaterializationError("physical chunk order changed")
            if chunk.pause_after_frames is None or chunk.pause_after_frames < 0:
                raise AudioEditMaterializationError(
                    f"chunk {chunk.chunk_index} has no frozen pause provenance"
                )
            if (
                "pause_after_frames" in raw_chunk
                and chunk.pause_after_frames != int(raw_chunk["pause_after_frames"])
            ):
                raise AudioEditMaterializationError("physical chunk pause plan changed")
            take = await self.session.get(AudioChunkTake, take_id)
            if (
                take is None
                or take.audio_chunk_id != chunk.id
                or take.status not in {"completed", "superseded"}
                or not take.audio_path
            ):
                raise AudioEditMaterializationError(
                    f"chunk {chunk.chunk_index} selected take is unavailable"
                )
            path, metadata = verify_audio_artifact(
                take.audio_path,
                AudioArtifactExpectation(
                    sha256=take.audio_sha256,
                    byte_size=take.audio_byte_size,
                    duration_seconds=take.audio_duration_seconds,
                    sample_rate=take.audio_sample_rate,
                    channels=None,
                    frame_count=take.audio_frame_count,
                    subtype=None,
                ),
            )
            if metadata.channels != 1:
                raise AudioEditMaterializationError("selected chunk is not mono")
            if any(
                (
                    "audio_sha256" in raw_chunk
                    and metadata.sha256 != str(raw_chunk["audio_sha256"]),
                    "audio_frame_count" in raw_chunk
                    and metadata.frame_count != int(raw_chunk["audio_frame_count"]),
                    "audio_sample_rate" in raw_chunk
                    and metadata.sample_rate != int(raw_chunk["audio_sample_rate"]),
                )
            ):
                raise AudioEditMaterializationError(
                    "selected chunk artifact differs from frozen edit snapshot"
                )
            if chunk.sample_rate is not None and metadata.sample_rate != chunk.sample_rate:
                raise AudioEditMaterializationError(
                    "selected chunk differs from its frozen sample rate"
                )
            if sample_rate is None:
                sample_rate = metadata.sample_rate
            elif metadata.sample_rate != sample_rate:
                raise AudioEditMaterializationError("selected chunks have different sample rates")
            pause_frames.append(chunk.pause_after_frames)
            selected_take_ids.append(take.id)
            manifest_chunks.append(
                BlockAssemblyChunk(
                    chunk_id=str(chunk.id),
                    chunk_index=chunk.chunk_index,
                    current_take_id=str(take.id),
                    audio_path=path,
                    audio_sha256=metadata.sha256,
                    frame_count=metadata.frame_count,
                    sample_rate=metadata.sample_rate,
                    channels=metadata.channels,
                    subtype=metadata.subtype,
                    pause_after_frames=chunk.pause_after_frames,
                )
            )
        if pause_frames[-1] != 0:
            raise AudioEditMaterializationError("final physical chunk must not append a pause")
        assert sample_rate is not None
        manifest = BlockAssemblyManifest(
            block_id=str(block.id), block_order=order, chunks=tuple(manifest_chunks)
        )
        destination = (
            edit_root / "blocks" / f"block_{order:03d}_{manifest.identity[:16]}.wav"
        )
        assembly = reassemble_block_from_manifest(manifest, destination)
        return _RebuiltBlock(
            block_id=block_id,
            order=order,
            edit_revision=edit_revision,
            path=destination,
            assembly=assembly,
            pause_frames=tuple(pause_frames),
            selected_take_ids=tuple(selected_take_ids),
            manifest=manifest,
        )

    async def _publish_database_state(
        self,
        update: AudioEditUpdate,
        workflow: WorkflowExecucao,
        rebuilt: dict[uuid.UUID, _RebuiltBlock],
        narration: AssemblyResult,
        final_path: Path,
        final_metadata: Any,
        *,
        mastering_config: dict[str, object],
        mastering_metrics: dict[str, object],
        performance_metrics: dict[str, float],
    ) -> None:
        locked_workflow = (
            await self.session.execute(
                select(WorkflowExecucao)
                .where(WorkflowExecucao.id == workflow.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        locked_update = (
            await self.session.execute(
                select(AudioEditUpdate)
                .where(AudioEditUpdate.id == update.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if locked_update.status != "running":
            raise AudioEditMaterializationError("audio edit update is no longer running")
        if locked_workflow.audio_edit_revision != locked_update.target_revision:
            raise AudioEditMaterializationError(
                "edit_snapshot_stale: a newer promotion arrived during materialization"
            )

        locked_blocks = list(
            (
                await self.session.execute(
                    select(AudioBloco)
                    .where(AudioBloco.workflow_execucao_id == workflow.id)
                    .order_by(AudioBloco.ordem)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        for block, offset in zip(locked_blocks, narration.offsets, strict=True):
            block.narration_start_seconds = offset.start_seconds
            block.narration_end_seconds = offset.end_seconds
            replacement = rebuilt.get(block.id)
            if replacement is None:
                continue
            metadata = replacement.assembly.metadata
            block.audio_path = str(replacement.path)
            block.duracao_segundos = metadata.duration_seconds
            block.audio_sha256 = metadata.sha256
            block.audio_sample_rate = metadata.sample_rate
            block.audio_channels = metadata.channels
            block.audio_byte_size = metadata.byte_size
            block.audio_frame_count = metadata.frame_count
            block.audio_subtype = metadata.subtype
            block.artifact_integrity = replacement.assembly.integrity
            block.assembly_manifest_identity = replacement.manifest.identity
            block.assembly_manifest = replacement.manifest.identity_payload()
            block.pause_diagnostics = {
                "version": "frozen_chunk_pause_frames_v1",
                "pause_after_frames": list(replacement.pause_frames),
                "selected_take_ids": [str(item) for item in replacement.selected_take_ids],
            }
            if block.current_take_id is not None:
                previous = await self.session.get(AudioBlocoTake, block.current_take_id)
                if previous is not None:
                    previous.is_current = False
                    if previous.status == "completed":
                        previous.status = "superseded"
            max_attempt = (
                await self.session.execute(
                    select(func.max(AudioBlocoTake.attempt_index)).where(
                        AudioBlocoTake.audio_bloco_id == block.id
                    )
                )
            ).scalar_one()
            block_take = AudioBlocoTake(
                audio_bloco_id=block.id,
                attempt_index=(max_attempt if max_attempt is not None else -1) + 1,
                kind="chunk_edit_reassembly",
                status="completed",
                request_key=f"audio-edit:{update.id}:block:{block.id}",
                idempotency_key=f"audio-edit:{update.id}:block:{block.id}",
                model_id=block.model_id,
                model_revision=block.model_revision,
                voice_profile=block.voice_profile,
                voice_profile_identity=block.voice_profile_identity,
                generation_profile=block.generation_profile,
                generation_profile_identity=block.generation_profile_identity,
                chunking_identity=block.voice_chunking_identity,
                voice_chunking_identity=block.voice_chunking_identity,
                synthesis_identity=block.synthesis_identity,
                canonical_text=block.texto,
                normalized_text=block.normalized_text,
                synthesis_text=block.synthesis_text,
                pronunciation_glossary_identity=block.pronunciation_glossary_identity,
                pronunciation_replacements=block.pronunciation_replacements,
                text_normalizer_version=block.text_normalizer_version,
                text_normalizer_identity=block.text_normalizer_identity,
                audio_path=str(replacement.path),
                audio_duration_seconds=metadata.duration_seconds,
                audio_sha256=metadata.sha256,
                audio_byte_size=metadata.byte_size,
                audio_sample_rate=metadata.sample_rate,
                audio_channels=metadata.channels,
                audio_frame_count=metadata.frame_count,
                audio_subtype=metadata.subtype,
                artifact_integrity=replacement.assembly.integrity,
                assembly_manifest_identity=replacement.manifest.identity,
                assembly_manifest=replacement.manifest.identity_payload(),
                provider_metadata={
                    "source": "audio_chunk_takes",
                    "audio_edit_update_id": str(update.id),
                    "assembly_manifest_identity": replacement.manifest.identity,
                    "block_assembly": replacement.assembly.integrity,
                    "selected_take_ids": [
                        str(item) for item in replacement.selected_take_ids
                    ],
                    "orchestrator_performance": replacement.assembly.timings,
                },
                pause_diagnostics=block.pause_diagnostics,
                is_current=True,
                completed_at=dt.datetime.now(dt.UTC),
            )
            self.session.add(block_take)
            await self.session.flush()
            block.current_take_id = block_take.id
            block.audio_materialized_revision = replacement.edit_revision
            block.status = "concluido"
            block.erro_mensagem = None

        locked_workflow.audio_final_path = str(final_path)
        locked_workflow.duracao_audio_segundos = final_metadata.duration_seconds
        locked_workflow.audio_final_sha256 = final_metadata.sha256
        locked_workflow.audio_final_sample_rate = final_metadata.sample_rate
        locked_workflow.audio_final_channels = final_metadata.channels
        locked_workflow.audio_final_byte_size = final_metadata.byte_size
        locked_workflow.audio_final_frame_count = final_metadata.frame_count
        locked_workflow.audio_final_subtype = final_metadata.subtype
        locked_workflow.audio_integrity = {
            "status": "passed",
            "artifact": asdict(final_metadata),
            "expected_frame_count": narration.expected_frame_count,
        }
        locked_workflow.narration_diagnostics = {
            "version": "narration_diagnostic_manifest_v1",
            "workflow_id": str(workflow.id),
            "artifact_sha256": final_metadata.sha256,
            "audio_edit_update_id": str(update.id),
            "audio_edit_revision": update.target_revision,
            "assembly": {
                "artifact": asdict(narration.metadata),
                "expected_frame_count": narration.expected_frame_count,
            },
            "mastering": mastering_metrics,
            "orchestrator_performance": performance_metrics,
            "blocks": [
                {
                    "block_id": str(item.block_id),
                    "order": item.order,
                    "edit_revision": item.edit_revision,
                    "selected_take_ids": [str(value) for value in item.selected_take_ids],
                    "pause_after_frames": list(item.pause_frames),
                }
                for item in sorted(rebuilt.values(), key=lambda value: value.order)
            ],
        }
        locked_workflow.mastering_config = mastering_config
        locked_workflow.mastering_metrics = mastering_metrics
        locked_workflow.audio_materialized_revision = update.target_revision
        locked_workflow.status = "audio_concluido"
        locked_workflow.audio_phase = "transcribing"
        locked_workflow.erro_mensagem = None
        locked_workflow.production_status = "queued"
        locked_workflow.production_phase = "narration_alignment"
        locked_workflow.production_error_category = None
        locked_workflow.production_error_message = None
        locked_workflow.production_completed_at = None
        locked_update.published_audio_sha256 = final_metadata.sha256
        locked_update.materialized_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    @staticmethod
    def _safe_message(exc: Exception) -> str:
        if isinstance(exc, AudioEditMaterializationError | ProductionAudioError | MasteringError):
            return str(exc)
        return type(exc).__name__
