"""Durable Qwen narration orchestration and final-audio production."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

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
from app.models.workflow import AudioBloco, AudioBlocoTake
from app.repositories.workflow_repository import WorkflowRepository
from app.services.mastering import MasteringConfig, MasteringError, master_narration
from app.services.narration_alignment_service import NarrationAlignmentService
from app.services.production_audio import (
    ProductionAudioError,
    assemble_narration,
    inspect_wav,
    publish_source_block,
)
from app.services.pronunciation import PronunciationPreprocessor
from app.services.text_normalization import PortugueseTextNormalizer
from app.services.transcription_service import (
    NarrationTranscriptionProvider,
    get_transcription_provider,
)

logger = logging.getLogger(__name__)
QUALITY_RETRY_CATEGORIES = frozenset({"chunk_quality_guard"})
QUALITY_RETRY_SEED_STRATEGY = "qwen-orchestrator-quality-retry-v1"
MANUAL_TAKE_SEED_STRATEGY = "qwen-orchestrator-manual-take-v1"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_text(serialized)


def derive_quality_retry_seed(initial_seed: int, generation_attempt: int) -> int:
    if not 1 <= initial_seed <= 2**31 - 1 or generation_attempt <= 0:
        raise ValueError("quality retry seed inputs are invalid")
    payload = f"{QUALITY_RETRY_SEED_STRATEGY}:{initial_seed}:{generation_attempt}".encode()
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value % (2**31 - 1) + 1


def derive_manual_take_seed(
    block_id: uuid.UUID, synthesis_identity: str, attempt_index: int, kind: str
) -> int:
    payload = (
        f"{MANUAL_TAKE_SEED_STRATEGY}:{block_id}:{synthesis_identity}:{attempt_index}:{kind}"
    ).encode()
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value % (2**31 - 1) + 1


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
        text_normalizer: PortugueseTextNormalizer | None = None,
        transcription_provider: NarrationTranscriptionProvider | None = None,
        alignment_service_factory: Callable[[AsyncSession], NarrationAlignmentService]
        | None = None,
    ) -> None:
        self.session = session
        self.repo = WorkflowRepository(session)
        self.config = config
        self.voice_client_factory = voice_client_factory or (lambda: VoiceClient(config))
        self.preprocessor = preprocessor or PronunciationPreprocessor.load(
            config.pronunciation_glossary_path
        )
        self.text_normalizer = text_normalizer or PortugueseTextNormalizer(
            version=config.text_normalizer_version
        )
        self.transcription_provider = transcription_provider
        self.alignment_service_factory = alignment_service_factory or NarrationAlignmentService

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
        active_take: AudioBlocoTake | None = None
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
                    normalization = self.text_normalizer.normalize(canonical_text)
                    pronunciation = self.preprocessor.preprocess(
                        normalization.normalized_text,
                        project=str(workflow.ideia_id),
                    )
                    source_hash = _sha256_text(canonical_text)
                    synthesis_hash = _sha256_text(pronunciation.synthesis_text)
                    identity = _canonical_hash(
                        {
                            "version": "qwen_audio_block_v2",
                            "source_text_sha256": source_hash,
                            "synthesis_text_sha256": synthesis_hash,
                            "text_normalizer_version": normalization.version,
                            "text_normalizer_identity": normalization.identity,
                            "pronunciation_glossary_identity": pronunciation.glossary_identity,
                            "voice_profile": provenance.voice.name,
                            "voice_profile_identity": provenance.voice.identity,
                            "generation_profile": provenance.generation.name,
                            "generation_profile_identity": provenance.generation.identity,
                            "voice_chunking_identity": provenance.chunking_identity,
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
                        normalized_text=normalization.normalized_text,
                        synthesis_text=pronunciation.synthesis_text,
                        source_text_sha256=source_hash,
                        synthesis_text_sha256=synthesis_hash,
                        synthesis_identity=identity,
                        pronunciation_glossary_identity=pronunciation.glossary_identity,
                        text_normalizer_version=normalization.version,
                        text_normalizer_identity=normalization.identity,
                        idempotency_key=idempotency_key,
                        voice_profile=provenance.voice.name,
                        voice_profile_identity=provenance.voice.identity,
                        generation_profile=provenance.generation.name,
                        generation_profile_identity=provenance.generation.identity,
                        pronunciation_replacements=list(pronunciation.applied_replacements),
                        voice_chunking_identity=provenance.chunking_identity,
                    )
                    active_block = block
                    source_path = source_dir / f"block_{order:03d}_{identity[:12]}_source.wav"
                    if (
                        unchanged
                        and not getattr(block, "requested_take_id", None)
                        and self._can_reuse(block)
                    ):
                        assert block.audio_path is not None
                        source_paths.append(Path(block.audio_path))
                        logger.info(
                            "reusing completed voice block",
                            extra={"workflow_id": str(workflow.id), "block_order": order},
                        )
                        continue
                    take = await self._ensure_take(block, identity, provenance.chunking_identity)
                    active_take = take
                    await self.repo.atualizar_bloco(
                        block.id, status="gerando", erro_mensagem=None, requested_take_id=take.id
                    )
                    take.status = "running"
                    if hasattr(self.repo, "atualizar_take"):
                        await self.repo.atualizar_take(take.id, status="running")
                    while True:
                        try:
                            job = await self._obtain_remote_job(
                                voice, block, pronunciation.synthesis_text
                            )
                            await self._persist_remote_job(block, job, take)
                            completed = await voice.wait_for_narration(
                                job.id,
                                should_stop=lambda: self._cancelled(workflow.id),
                            )
                            if hasattr(self.repo, "obter_bloco_por_id"):
                                current = await self.repo.obter_bloco_por_id(block.id)
                                if current is not None and current.requested_take_id != take.id:
                                    if hasattr(self.repo, "atualizar_take"):
                                        await self.repo.atualizar_take(
                                            take.id,
                                            status="superseded",
                                            error_category="superseded",
                                            error_message="A newer take was requested",
                                        )
                                    active_block = None
                                    active_take = None
                                    return
                        except VoiceJobFailedError as exc:
                            take = await self._prepare_quality_retry(block, take, exc)
                            active_take = take
                            continue
                        await self._persist_remote_job(block, completed, take)
                        break
                    payload = await voice.download_audio(completed.id)
                    take_dir = source_dir / "takes"
                    take_dir.mkdir(parents=True, exist_ok=True)
                    source_path = take_dir / f"take_{take.id}.wav"
                    metadata = publish_source_block(
                        payload,
                        source_path,
                        expected_sha256=completed.result_sha256,
                        expected_byte_size=completed.result_byte_size,
                        text_length_chars=len(pronunciation.synthesis_text),
                        max_duration_seconds=self.config.audio_block_max_duration_seconds,
                        max_seconds_per_char=self.config.audio_block_max_seconds_per_char,
                        duration_grace_seconds=self.config.audio_block_duration_grace_seconds,
                    )
                    if hasattr(self.repo, "obter_bloco_por_id"):
                        current = await self.repo.obter_bloco_por_id(block.id)
                        if current is not None and current.requested_take_id != take.id:
                            if hasattr(self.repo, "atualizar_take"):
                                await self.repo.atualizar_take(
                                    take.id,
                                    status="superseded",
                                    error_category="superseded",
                                    error_message="A newer take was requested",
                                    audio_path=str(source_path),
                                    audio_duration_seconds=metadata.duration_seconds,
                                    audio_sha256=metadata.sha256,
                                    audio_byte_size=metadata.byte_size,
                                )
                            active_block = None
                            active_take = None
                            return
                    if hasattr(self.repo, "promover_take"):
                        promoted = await self.repo.promover_take(
                            block.id,
                            take.id,
                            status="completed",
                            job_id_voz=completed.id,
                            resolved_seed=completed.resolved_seed,
                            model_id=completed.model_id,
                            model_revision=completed.model_revision,
                            voice_profile=completed.voice_profile,
                            generation_profile=completed.generation_profile,
                            voice_chunking_identity=completed.metadata.get("chunking_identity"),
                            audio_path=str(source_path),
                            audio_duration_seconds=metadata.duration_seconds,
                            audio_sha256=metadata.sha256,
                            audio_byte_size=metadata.byte_size,
                            completed_at=dt.datetime.now(dt.UTC),
                            block_fields={
                                "status": "concluido",
                                "audio_path": str(source_path),
                                "duracao_segundos": metadata.duration_seconds,
                                "audio_sha256": metadata.sha256,
                                "audio_sample_rate": metadata.sample_rate,
                                "audio_channels": metadata.channels,
                                "audio_byte_size": metadata.byte_size,
                                "erro_mensagem": None,
                            },
                        )
                        if promoted is False:
                            active_block = None
                            active_take = None
                            return
                    else:
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
                    active_take = None

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

            transcription_available = True
            try:
                await self._ensure_transcription(workflow.id, final_path, mastered.metadata.sha256)
            except Exception:
                transcription_available = False
                logger.exception(
                    "final narration transcription failed without invalidating audio",
                    extra={"workflow_id": str(workflow.id)},
                )
            await self.repo.atualizar_status(
                workflow.id,
                "audio_concluido",
                audio_phase="completed" if transcription_available else "quality_unvalidated",
                erro_mensagem=None,
            )
            try:
                alignment_result = await self.alignment_service_factory(self.session).ensure(
                    workflow.id
                )
                if (
                    isinstance(alignment_result, tuple)
                    and alignment_result
                    and getattr(alignment_result[0], "status", None) in {"blocked", "failed"}
                ):
                    await self.repo.atualizar_status(
                        workflow.id,
                        "audio_concluido",
                        audio_phase="quality_unvalidated",
                    )
            except Exception:
                logger.exception(
                    "automatic narration alignment failed without invalidating audio",
                    extra={"workflow_id": str(workflow.id)},
                )
                await self.repo.atualizar_status(
                    workflow.id,
                    "audio_concluido",
                    audio_phase="quality_unvalidated",
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
                retained_current = bool(
                    getattr(active_block, "current_take_id", None)
                    and getattr(active_block, "audio_path", None)
                )
                await self.repo.atualizar_bloco(
                    active_block.id,
                    status="concluido" if retained_current else "erro",
                    erro_mensagem=str(exc),
                )
            if active_take is not None and hasattr(self.repo, "atualizar_take"):
                await self.repo.atualizar_take(
                    active_take.id,
                    status="failed",
                    error_category=type(exc).__name__,
                    error_message=str(exc),
                )
            await self.repo.atualizar_status(
                workflow.id,
                "erro",
                audio_phase="failed",
                erro_mensagem=str(exc),
            )

    async def _ensure_take(
        self, block: AudioBloco, synthesis_identity: str, chunking_identity: str
    ) -> AudioBlocoTake:
        request_key = block.idempotency_key
        if (
            hasattr(self.repo, "criar_take")
            and getattr(block, "requested_take_id", None) is not None
        ):
            requested_take_id = block.requested_take_id
            assert requested_take_id is not None
            take = await self.repo.obter_take(requested_take_id)
            if take is not None and take.status in {"queued", "running"}:
                return take
            # A failed requested take must never be resurrected by a stale
            # worker retry; allocate a distinct deterministic request record.
            request_key = f"{request_key or synthesis_identity}:recovery:{uuid.uuid4()}"
        if not hasattr(self.repo, "criar_take"):
            return SimpleNamespace(
                id=uuid.uuid4(),
                status="queued",
                requested_seed=self.config.voice_seed,
                chunking_identity=chunking_identity,
            )  # type: ignore[return-value]
        take, _ = await self.repo.criar_take(
            bloco=block,
            kind="initial",
            request_key=request_key,
            idempotency_key=request_key or "",
            requested_seed=self.config.voice_seed,
            synthesis_identity=synthesis_identity,
            chunking_identity=chunking_identity,
        )
        await self.repo.atualizar_bloco(
            block.id,
            requested_take_id=take.id,
            resolved_seed=take.requested_seed,
            initial_resolved_seed=take.requested_seed,
        )
        block.requested_take_id = take.id
        block.resolved_seed = take.requested_seed
        block.initial_resolved_seed = take.requested_seed
        return take

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
                raise VoiceJobFailedError(
                    existing.error_category or "provider_failure",
                    existing.error_message or "remote narration failed",
                )
            except VoiceJobNotFoundError:
                logger.warning(
                    "persisted remote voice job disappeared; recreating idempotently",
                    extra={"block_id": str(block.id), "job_id": block.job_id_voz},
                )
        return await voice.create_narration(
            text=synthesis_text,
            voice_profile=block.voice_profile or self.config.voice_profile,
            generation_profile=block.generation_profile or self.config.voice_generation_profile,
            idempotency_key=block.idempotency_key or "",
            language=self.config.voice_language,
            seed=block.resolved_seed if block.resolved_seed is not None else self.config.voice_seed,
        )

    async def _prepare_quality_retry(
        self, block: AudioBloco, take: AudioBlocoTake, failure: VoiceJobFailedError
    ) -> AudioBlocoTake:
        if failure.category not in QUALITY_RETRY_CATEGORIES:
            raise failure
        initial_seed = block.initial_resolved_seed or block.resolved_seed
        if initial_seed is None:
            raise VoiceJobFailedError(
                "quality_retry_unavailable",
                "voice quality retry has no persisted seed provenance",
            )
        history = list(block.generation_attempt_history or [])
        entry = {
            "generation_attempt": block.generation_attempt,
            "job_id": block.job_id_voz,
            "seed": block.resolved_seed,
            "error_category": failure.category,
            "error_message": failure.public_message,
        }
        if not any(
            item.get("job_id") == block.job_id_voz
            and item.get("generation_attempt") == block.generation_attempt
            for item in history
            if isinstance(item, dict)
        ):
            history.append(entry)
        if block.generation_attempt >= self.config.voice_quality_max_retries:
            if hasattr(self.repo, "atualizar_take"):
                await self.repo.atualizar_take(
                    take.id,
                    status="failed",
                    error_category="quality_retry_exhausted",
                    error_message=failure.public_message,
                    error_diagnostics={"history": history},
                )
            await self.repo.atualizar_bloco(
                block.id,
                generation_attempt_history=history,
                erro_mensagem="quality_retry_exhausted: voice quality retry limit was exhausted",
            )
            block.generation_attempt_history = history
            raise VoiceJobFailedError(
                "quality_retry_exhausted",
                "voice quality retry limit was exhausted",
            )
        attempt = block.generation_attempt + 1
        retry_seed = derive_quality_retry_seed(initial_seed, attempt)
        if retry_seed == block.resolved_seed:
            retry_seed = retry_seed % (2**31 - 1) + 1
        retry_key = (
            f"qwen-narration:{block.workflow_execucao_id}:{block.ordem}:"
            f"{block.synthesis_identity}:quality-retry:{attempt}"
        )
        if hasattr(self.repo, "atualizar_take"):
            await self.repo.atualizar_take(
                take.id,
                status="failed",
                error_category=failure.category,
                error_message=failure.public_message,
                error_diagnostics=entry,
            )
        if hasattr(self.repo, "criar_take"):
            next_take, _ = await self.repo.criar_take(
                bloco=block,
                kind="quality_retry",
                request_key=retry_key,
                idempotency_key=retry_key,
                requested_seed=retry_seed,
                synthesis_identity=block.synthesis_identity,
                chunking_identity=getattr(take, "chunking_identity", None),
            )
        else:
            next_take = SimpleNamespace(
                id=uuid.uuid4(),
                status="queued",
                requested_seed=retry_seed,
                chunking_identity=getattr(take, "chunking_identity", None),
            )  # type: ignore[assignment]
        await self.repo.atualizar_bloco(
            block.id,
            job_id_voz=None,
            resolved_seed=retry_seed,
            initial_resolved_seed=initial_seed,
            generation_attempt=attempt,
            idempotency_key=retry_key,
            retry_seed_strategy=QUALITY_RETRY_SEED_STRATEGY,
            generation_attempt_history=history,
            requested_take_id=next_take.id,
        )
        block.job_id_voz = None
        block.resolved_seed = retry_seed
        block.initial_resolved_seed = initial_seed
        block.generation_attempt = attempt
        block.idempotency_key = retry_key
        block.retry_seed_strategy = QUALITY_RETRY_SEED_STRATEGY
        block.generation_attempt_history = history
        block.requested_take_id = next_take.id
        return next_take

    async def _persist_remote_job(
        self, block: AudioBloco, job: VoiceNarrationJob, take: AudioBlocoTake
    ) -> None:
        initial_seed = block.initial_resolved_seed or job.resolved_seed
        await self.repo.atualizar_bloco(
            block.id,
            job_id_voz=job.id,
            resolved_seed=job.resolved_seed,
            initial_resolved_seed=initial_seed,
            model_id=job.model_id,
            model_revision=job.model_revision,
            voice_chunking_identity=job.metadata.get("chunking_identity")
            or getattr(block, "voice_chunking_identity", None),
        )
        if hasattr(self.repo, "atualizar_take"):
            await self.repo.atualizar_take(
                take.id,
                job_id_voz=job.id,
                resolved_seed=job.resolved_seed,
                model_id=job.model_id,
                model_revision=job.model_revision,
                voice_profile=job.voice_profile,
                generation_profile=job.generation_profile,
                chunking_identity=getattr(block, "voice_chunking_identity", None),
                voice_chunking_identity=job.metadata.get("chunking_identity")
                or getattr(block, "voice_chunking_identity", None),
            )
        block.job_id_voz = job.id
        block.resolved_seed = job.resolved_seed
        block.initial_resolved_seed = initial_seed

    @staticmethod
    def _can_reuse(block: AudioBloco) -> bool:
        if block.status != "concluido" or not block.audio_path:
            return False
        expected_path = Path(block.audio_path)
        if not expected_path.is_file():
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
