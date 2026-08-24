"""Durable Qwen narration orchestration and final-audio production."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.voice_client import (
    VoiceClient,
    VoiceClientCancelledError,
    VoiceClientError,
    VoiceClientTransientError,
    VoiceJobFailedError,
    VoiceJobNotFoundError,
    VoiceNarrationJob,
)
from app.core.config import Settings, settings
from app.models.workflow import AudioBloco
from app.repositories.workflow_repository import WorkflowRepository
from app.services.mastering import MasteringConfig, MasteringError, master_narration
from app.services.production_audio import (
    ProductionAudioError,
    assemble_narration,
    inspect_wav,
    publish_source_block,
)
from app.services.pronunciation import PronunciationPreprocessor
from app.services.transcription_service import (
    NarrationTranscriptionProvider,
    get_transcription_provider,
)

logger = logging.getLogger(__name__)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_text(serialized)


def _mastering_config(config: Settings) -> MasteringConfig:
    return MasteringConfig(
        target_lufs=config.audio_target_lufs,
        max_true_peak_dbtp=config.audio_max_true_peak_db,
        true_peak_tolerance_db=config.audio_true_peak_tolerance_db,
        true_peak_safety_margin_db=config.audio_true_peak_safety_margin_db,
        min_acceptable_lufs=config.audio_min_acceptable_lufs,
        max_acceptable_lufs=config.audio_max_acceptable_lufs,
        duration_tolerance_seconds=config.audio_duration_tolerance_seconds,
    )


class AudioWorkflowService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        config: Settings = settings,
        voice_client_factory: Callable[[], VoiceClient] | None = None,
        preprocessor: PronunciationPreprocessor | None = None,
        transcription_provider: NarrationTranscriptionProvider | None = None,
    ) -> None:
        self.repo = WorkflowRepository(session)
        self.config = config
        self.voice_client_factory = voice_client_factory or (lambda: VoiceClient(config))
        self.preprocessor = preprocessor or PronunciationPreprocessor.load(
            config.pronunciation_glossary_path
        )
        self.transcription_provider = transcription_provider

    async def process(self, workflow_id: uuid.UUID) -> None:
        workflow = await self.repo.obter_por_id(workflow_id, com_blocos=True)
        if workflow is None:
            logger.warning("workflow not found", extra={"workflow_id": str(workflow_id)})
            return
        if workflow.status == "cancelada":
            return
        blocks = workflow.roteiro_snapshot.get("blocos", [])
        if not isinstance(blocks, list) or not blocks:
            await self.repo.atualizar_status(
                workflow.id, "erro", erro_mensagem="Roteiro sem blocos narrativos"
            )
            return

        await self.repo.atualizar_status(
            workflow.id, "gerando_audio", audio_phase="synthesizing", erro_mensagem=None
        )
        root = Path(self.config.audio_storage_path) / str(workflow.id)
        source_dir = root / "blocks"
        working_dir = root / "working"
        source_dir.mkdir(parents=True, exist_ok=True)
        working_dir.mkdir(parents=True, exist_ok=True)

        active_block: AudioBloco | None = None
        try:
            async with self.voice_client_factory() as voice:
                provenance = await voice.resolve_profiles(
                    self.config.voice_profile,
                    self.config.voice_generation_profile,
                )
                source_paths: list[Path] = []
                for order, raw_block in enumerate(blocks):
                    if await self._cancelled(workflow.id):
                        raise VoiceClientCancelledError("workflow cancelled")
                    if not isinstance(raw_block, dict):
                        raise VoiceClientError(f"invalid script block at order {order}")
                    canonical_text = raw_block.get("conteudo")
                    if not isinstance(canonical_text, str) or not canonical_text.strip():
                        raise VoiceClientError(f"empty script block at order {order}")
                    block_type = str(raw_block.get("tipo", f"bloco_{order}"))
                    pronunciation = self.preprocessor.preprocess(
                        canonical_text,
                        project=str(workflow.ideia_id),
                    )
                    source_hash = _sha256_text(canonical_text)
                    synthesis_hash = _sha256_text(pronunciation.synthesis_text)
                    identity = _canonical_hash(
                        {
                            "version": "qwen_audio_block_v1",
                            "source_text_sha256": source_hash,
                            "synthesis_text_sha256": synthesis_hash,
                            "pronunciation_glossary_identity": pronunciation.glossary_identity,
                            "voice_profile": provenance.voice.name,
                            "voice_profile_identity": provenance.voice.identity,
                            "generation_profile": provenance.generation.name,
                            "generation_profile_identity": provenance.generation.identity,
                            "language": self.config.voice_language,
                            "requested_seed": self.config.voice_seed,
                        }
                    )
                    idempotency_key = f"qwen-narration:{workflow.id}:{order}:{identity}"
                    block, unchanged = await self.repo.ensure_bloco(
                        workflow_id=workflow.id,
                        ordem=order,
                        tipo=block_type,
                        texto=canonical_text,
                        synthesis_text=pronunciation.synthesis_text,
                        source_text_sha256=source_hash,
                        synthesis_text_sha256=synthesis_hash,
                        synthesis_identity=identity,
                        pronunciation_glossary_identity=pronunciation.glossary_identity,
                        idempotency_key=idempotency_key,
                        voice_profile=provenance.voice.name,
                        voice_profile_identity=provenance.voice.identity,
                        generation_profile=provenance.generation.name,
                        generation_profile_identity=provenance.generation.identity,
                    )
                    active_block = block
                    source_path = source_dir / f"block_{order:03d}_{identity[:12]}_source.wav"
                    if unchanged and self._can_reuse(block, source_path):
                        source_paths.append(source_path)
                        logger.info(
                            "reusing completed voice block",
                            extra={"workflow_id": str(workflow.id), "block_order": order},
                        )
                        continue
                    await self.repo.atualizar_bloco(block.id, status="gerando", erro_mensagem=None)
                    job = await self._obtain_remote_job(voice, block, pronunciation.synthesis_text)
                    await self._persist_remote_job(block, job)
                    completed = await voice.wait_for_narration(
                        job.id,
                        should_stop=lambda: self._cancelled(workflow.id),
                    )
                    await self._persist_remote_job(block, completed)
                    payload = await voice.download_audio(completed.id)
                    metadata = publish_source_block(
                        payload,
                        source_path,
                        expected_sha256=completed.result_sha256,
                        expected_byte_size=completed.result_byte_size,
                    )
                    await self.repo.atualizar_bloco(
                        block.id,
                        status="concluido",
                        audio_path=str(source_path),
                        duracao_segundos=metadata.duration_seconds,
                        audio_sha256=metadata.sha256,
                        audio_sample_rate=metadata.sample_rate,
                        audio_channels=metadata.channels,
                        audio_byte_size=metadata.byte_size,
                        erro_mensagem=None,
                    )
                    source_paths.append(source_path)
                    active_block = None

            await self.repo.atualizar_status(workflow.id, "gerando_audio", audio_phase="assembling")
            assembled_path = working_dir / "narration_assembled.wav"
            assembly = assemble_narration(
                source_paths,
                assembled_path,
                sample_rate=self.config.audio_final_sample_rate,
                silence_ms=self.config.silencio_entre_blocos_ms,
            )
            for offset in assembly.offsets:
                offset_block = await self.repo.obter_bloco_por_ordem(workflow.id, offset.order)
                if offset_block is not None:
                    await self.repo.atualizar_bloco(
                        offset_block.id,
                        narration_start_seconds=offset.start_seconds,
                        narration_end_seconds=offset.end_seconds,
                    )

            await self.repo.atualizar_status(workflow.id, "gerando_audio", audio_phase="mastering")
            final_path = root / "narration_final.wav"
            mastering_config = _mastering_config(self.config)
            mastered = master_narration(assembled_path, final_path, mastering_config)
            if (
                abs(mastered.metadata.duration_seconds - assembly.metadata.duration_seconds)
                > self.config.audio_duration_tolerance_seconds
            ):
                raise MasteringError("final narration duration changed after mastering")
            await self.repo.atualizar_status(
                workflow.id,
                "gerando_audio",
                audio_phase="transcribing",
                audio_final_path=str(final_path),
                duracao_audio_segundos=mastered.metadata.duration_seconds,
                audio_final_sha256=mastered.metadata.sha256,
                audio_final_sample_rate=mastered.metadata.sample_rate,
                audio_final_channels=mastered.metadata.channels,
                audio_final_byte_size=mastered.metadata.byte_size,
                mastering_config=asdict(mastering_config),
                mastering_metrics=mastered.to_dict(),
            )

            try:
                await self._ensure_transcription(workflow.id, final_path, mastered.metadata.sha256)
            except Exception:
                logger.exception(
                    "final narration transcription failed without invalidating audio",
                    extra={"workflow_id": str(workflow.id)},
                )
            await self.repo.atualizar_status(
                workflow.id,
                "audio_concluido",
                audio_phase="completed",
                erro_mensagem=None,
            )
        except VoiceClientCancelledError:
            logger.info(
                "workflow cancelled during audio processing",
                extra={"workflow_id": str(workflow.id)},
            )
        except (
            VoiceClientError,
            VoiceClientTransientError,
            ProductionAudioError,
            MasteringError,
        ) as exc:
            logger.exception(
                "workflow audio production failed",
                extra={"workflow_id": str(workflow.id)},
            )
            if active_block is not None:
                await self.repo.atualizar_bloco(
                    active_block.id,
                    status="erro",
                    erro_mensagem=str(exc),
                )
            await self.repo.atualizar_status(
                workflow.id,
                "erro",
                audio_phase="failed",
                erro_mensagem=str(exc),
            )

    async def _obtain_remote_job(
        self,
        voice: VoiceClient,
        block: AudioBloco,
        synthesis_text: str,
    ) -> VoiceNarrationJob:
        if block.job_id_voz:
            try:
                existing = await voice.get_narration(block.job_id_voz)
                if existing.status != "failed":
                    return existing
                raise VoiceJobFailedError(existing.error_message or "remote narration failed")
            except VoiceJobNotFoundError:
                logger.warning(
                    "persisted remote voice job disappeared; recreating idempotently",
                    extra={"block_id": str(block.id), "job_id": block.job_id_voz},
                )
            except VoiceJobFailedError:
                attempt = block.generation_attempt + 1
                retry_key = f"{block.idempotency_key}:retry:{attempt}"
                await self.repo.atualizar_bloco(
                    block.id,
                    job_id_voz=None,
                    generation_attempt=attempt,
                    idempotency_key=retry_key,
                )
                block.job_id_voz = None
                block.generation_attempt = attempt
                block.idempotency_key = retry_key
        return await voice.create_narration(
            text=synthesis_text,
            voice_profile=block.voice_profile or self.config.voice_profile,
            generation_profile=block.generation_profile or self.config.voice_generation_profile,
            idempotency_key=block.idempotency_key or "",
            language=self.config.voice_language,
            seed=block.resolved_seed if block.resolved_seed is not None else self.config.voice_seed,
        )

    async def _persist_remote_job(self, block: AudioBloco, job: VoiceNarrationJob) -> None:
        await self.repo.atualizar_bloco(
            block.id,
            job_id_voz=job.id,
            resolved_seed=job.resolved_seed,
            model_id=job.model_id,
            model_revision=job.model_revision,
        )
        block.job_id_voz = job.id
        block.resolved_seed = job.resolved_seed

    @staticmethod
    def _can_reuse(block: AudioBloco, expected_path: Path) -> bool:
        if block.status != "concluido" or not block.audio_path:
            return False
        if Path(block.audio_path) != expected_path or not expected_path.is_file():
            return False
        try:
            metadata = inspect_wav(expected_path)
        except ProductionAudioError:
            return False
        return (
            block.audio_sha256 == metadata.sha256
            and block.audio_sample_rate == metadata.sample_rate
            and block.audio_channels == metadata.channels
            and block.audio_byte_size == metadata.byte_size
        )

    async def _cancelled(self, workflow_id: uuid.UUID) -> bool:
        workflow = await self.repo.obter_por_id(workflow_id)
        return workflow is None or workflow.status == "cancelada"

    async def _ensure_transcription(
        self, workflow_id: uuid.UUID, final_path: Path, audio_sha256: str
    ) -> None:
        existing = await self.repo.obter_transcricao(workflow_id)
        if existing is not None and existing.audio_sha256 == audio_sha256:
            return
        provider = self.transcription_provider or get_transcription_provider()
        result = provider.transcribe(final_path)
        await self.repo.upsert_transcricao(
            workflow_id,
            texto_completo=result.text,
            palavras_timestamps=[word.to_legacy_dict() for word in result.words],
            audio_sha256=audio_sha256,
            provider=result.provider,
            model=result.model,
            language=result.language,
        )


async def processar_workflow(
    session: AsyncSession,
    workflow_id: uuid.UUID,
    **service_kwargs: object,
) -> None:
    service = AudioWorkflowService(session, **service_kwargs)  # type: ignore[arg-type]
    await service.process(workflow_id)
