"""Controlled pronunciation experiments; never used by the production resolver path."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import io
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import soundfile as sf  # type: ignore[import-untyped]

from app.clients.voice_client import (
    VoiceClient,
    VoiceClientError,
    VoiceNarrationJob,
    VoiceProfileProvenance,
)

LAB_SCHEMA_VERSION = "neovids_pronunciation_lab_v1"
INITIALISM_STRATEGY_VERSION = "pt_br_initialism_candidates_v1"
MANUAL_SCORER_IDENTITY = "manual_review_only_v1"
MAX_LAB_CONTEXTS = 8
MAX_LAB_CANDIDATES = 32
MAX_LAB_SEEDS = 8
MAX_LAB_CELLS = 128
MAX_LAB_CONTEXT_CHARS = 4_000
MAX_LAB_CANDIDATE_CHARS = 500

_PT_BR_LETTER_NAMES = {
    "A": "Á",
    "B": "Bê",
    "C": "Cê",
    "D": "Dê",
    "E": "Ê",
    "F": "Éfe",
    "G": "Gê",
    "H": "Agá",
    "I": "I",
    "J": "Jota",
    "K": "Cá",
    "L": "Éle",
    "M": "Ême",
    "N": "Êne",
    "O": "Ó",
    "P": "Pê",
    "Q": "Quê",
    "R": "Érre",
    "S": "Ésse",
    "T": "Tê",
    "U": "U",
    "V": "Vê",
    "W": "Dáblio",
    "X": "Xis",
    "Y": "Ípsilon",
    "Z": "Zê",
}


class PronunciationLabValidationError(ValueError):
    """A lab specification or review action is invalid."""


@dataclass(frozen=True)
class LabCandidate:
    id: str
    text: str
    strategy: str = "manual"
    strategy_version: str = "manual_v1"


@dataclass(frozen=True)
class LabSpec:
    target_term: str
    contexts: tuple[str, ...]
    candidates: tuple[LabCandidate, ...]
    language: str
    locale: str
    voice_profile: str
    generation_profile: str
    seeds: tuple[int, ...]
    run_id: str | None = None

    def validate(self) -> None:
        if not self.target_term.strip():
            raise PronunciationLabValidationError("target term must not be empty")
        if not self.contexts or any(not context.strip() for context in self.contexts):
            raise PronunciationLabValidationError("at least one non-empty context is required")
        if len(self.contexts) > MAX_LAB_CONTEXTS or any(
            len(context) > MAX_LAB_CONTEXT_CHARS for context in self.contexts
        ):
            raise PronunciationLabValidationError("lab context count or size exceeds its bound")
        if not self.candidates:
            raise PronunciationLabValidationError("at least one candidate is required")
        if len(self.candidates) > MAX_LAB_CANDIDATES or any(
            len(candidate.text) > MAX_LAB_CANDIDATE_CHARS for candidate in self.candidates
        ):
            raise PronunciationLabValidationError("lab candidate count or size exceeds its bound")
        candidate_ids = [candidate.id for candidate in self.candidates]
        candidate_texts = [candidate.text for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids) or len(set(candidate_texts)) != len(
            candidate_texts
        ):
            raise PronunciationLabValidationError("candidate ids and texts must be unique")
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,95}", value) for value in candidate_ids):
            raise PronunciationLabValidationError("candidate id is invalid")
        if any(not candidate.text.strip() for candidate in self.candidates):
            raise PronunciationLabValidationError("candidate text must not be empty")
        if not self.language.strip() or not self.locale.strip():
            raise PronunciationLabValidationError("language and locale are required")
        if not self.voice_profile.strip() or not self.generation_profile.strip():
            raise PronunciationLabValidationError("voice and generation profiles are required")
        if not self.seeds or any(not 1 <= seed <= 2**31 - 1 for seed in self.seeds):
            raise PronunciationLabValidationError("controlled non-zero seeds are required")
        if len(self.seeds) > MAX_LAB_SEEDS or (
            len(self.contexts) * len(self.candidates) * len(self.seeds) > MAX_LAB_CELLS
        ):
            raise PronunciationLabValidationError("lab matrix exceeds its bounded cell count")
        for context in self.contexts:
            render_candidate_context(context, self.target_term, self.target_term)


@dataclass(frozen=True)
class LabVoiceResult:
    job_id: str | None
    status: str
    requested_seed: int
    resolved_seed: int | None
    voice_profile: str
    voice_identity: str | None
    generation_profile: str
    generation_identity: str | None
    model_id: str | None
    model_revision: str | None
    metadata: dict[str, Any]
    audio_bytes: bytes | None = None
    result_duration_seconds: float | None = None
    result_sample_rate: int | None = None
    result_sha256: str | None = None
    error_category: str | None = None
    error_message: str | None = None


class PronunciationLabGateway(Protocol):
    async def synthesize(
        self,
        *,
        text: str,
        language: str,
        voice_profile: str,
        generation_profile: str,
        seed: int,
        idempotency_key: str,
    ) -> LabVoiceResult: ...


class PronunciationCandidateSource(Protocol):
    """Extension point for deterministic or future external candidate proposers."""

    identity: str

    def propose(self, *, target_term: str, locale: str) -> tuple[LabCandidate, ...]: ...


class PronunciationScorer(Protocol):
    identity: str

    def score(
        self,
        *,
        wav_path: Path,
        target_term: str,
        candidate_text: str,
    ) -> dict[str, Any] | None: ...


class ManualOnlyScorer:
    """Explicit no-op scorer; human listening remains authoritative."""

    identity = MANUAL_SCORER_IDENTITY

    def score(
        self,
        *,
        wav_path: Path,
        target_term: str,
        candidate_text: str,
    ) -> dict[str, Any] | None:
        del wav_path, target_term, candidate_text
        return None


class ManualCandidateSource:
    identity = "manual_v1"

    def __init__(self, values: list[str]):
        self.values = values

    def propose(self, *, target_term: str, locale: str) -> tuple[LabCandidate, ...]:
        del target_term, locale
        return self.candidates(self.values)

    @staticmethod
    def candidates(values: list[str]) -> tuple[LabCandidate, ...]:
        result = []
        for index, value in enumerate(values, start=1):
            normalized = value.strip()
            if not normalized:
                raise PronunciationLabValidationError("manual candidates must not be empty")
            digest = hashlib.sha256(normalized.encode()).hexdigest()[:8]
            result.append(
                LabCandidate(
                    id=f"manual.{index:02d}.{digest}",
                    text=normalized,
                    strategy="manual",
                    strategy_version="manual_v1",
                )
            )
        return tuple(result)


class InitialismCandidateSource:
    """Experimental candidates, not linguistic truth and never production rules by themselves."""

    identity = INITIALISM_STRATEGY_VERSION

    def propose(self, *, target_term: str, locale: str) -> tuple[LabCandidate, ...]:
        return self.candidates(target_term, locale=locale)

    @staticmethod
    def candidates(term: str, *, locale: str = "pt-BR") -> tuple[LabCandidate, ...]:
        token = term.strip().upper()
        if locale.casefold() != "pt-br" or not re.fullmatch(r"[A-Z]{2,10}", token):
            raise PronunciationLabValidationError(
                "initialism strategy currently supports 2-10 Latin letters in pt-BR"
            )
        raw_values = (
            ("raw", token),
            ("spaced", " ".join(token)),
            ("localized", " ".join(_PT_BR_LETTER_NAMES[letter] for letter in token)),
        )
        return tuple(
            LabCandidate(
                id=f"initialism.{name}",
                text=value,
                strategy="initialism",
                strategy_version=INITIALISM_STRATEGY_VERSION,
            )
            for name, value in raw_values
        )


class VoiceClientLabGateway:
    """Thin adapter over the provider-neutral client, with no quality retry loop."""

    def __init__(self, client: VoiceClient):
        self.client = client
        self._provenance: dict[tuple[str, str], VoiceProfileProvenance] = {}

    async def synthesize(
        self,
        *,
        text: str,
        language: str,
        voice_profile: str,
        generation_profile: str,
        seed: int,
        idempotency_key: str,
    ) -> LabVoiceResult:
        profile_key = (voice_profile, generation_profile)
        provenance = self._provenance.get(profile_key)
        if provenance is None:
            provenance = await self.client.resolve_profiles(voice_profile, generation_profile)
            self._provenance[profile_key] = provenance
        created = await self.client.create_narration(
            text=text,
            language=language,
            voice_profile=voice_profile,
            generation_profile=generation_profile,
            seed=seed,
            idempotency_key=idempotency_key,
        )
        terminal = await self._wait_terminal(created)
        audio = (
            await self.client.download_audio(terminal.id)
            if terminal.status == "completed"
            else None
        )
        metadata = {
            "voice_identity": provenance.voice.identity,
            "generation_identity": provenance.generation.identity,
            "chunking_strategy_version": provenance.chunking_strategy_version,
            "chunking_identity": provenance.chunking_identity,
            **(terminal.metadata or {}),
        }
        return LabVoiceResult(
            job_id=terminal.id,
            status=terminal.status,
            requested_seed=seed,
            resolved_seed=terminal.resolved_seed,
            voice_profile=terminal.voice_profile,
            voice_identity=metadata.get("voice_identity"),
            generation_profile=terminal.generation_profile,
            generation_identity=metadata.get("generation_identity"),
            model_id=terminal.model_id,
            model_revision=terminal.model_revision,
            metadata=metadata,
            audio_bytes=audio,
            result_duration_seconds=terminal.result_duration_seconds,
            result_sample_rate=terminal.result_sample_rate,
            result_sha256=terminal.result_sha256,
            error_category=terminal.error_category,
            error_message=terminal.error_message,
        )

    async def _wait_terminal(self, job: VoiceNarrationJob) -> VoiceNarrationJob:
        deadline = time.monotonic() + self.client.settings.voice_job_timeout_seconds
        current = job
        while current.status not in {"completed", "failed"}:
            if time.monotonic() >= deadline:
                raise TimeoutError("pronunciation lab candidate exceeded the Qwen job timeout")
            await asyncio.sleep(self.client.settings.voice_poll_interval_seconds)
            current = await self.client.get_narration(job.id)
        return current


@dataclass(frozen=True)
class LabRunSummary:
    run_id: str
    run_directory: Path
    manifest_path: Path
    records: tuple[dict[str, Any], ...]


class PronunciationLabRunner:
    def __init__(
        self,
        gateway: PronunciationLabGateway,
        *,
        scorer: PronunciationScorer | None = None,
    ):
        self.gateway = gateway
        self.scorer = scorer or ManualOnlyScorer()

    async def run(self, spec: LabSpec, *, output_root: Path) -> LabRunSummary:
        spec.validate()
        run_id = spec.run_id or _new_run_id()
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,128}", run_id):
            raise PronunciationLabValidationError("lab run id is invalid")
        run_directory = output_root / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
        manifest_path = run_directory / "manifest.jsonl"
        _atomic_json(
            run_directory / "run.json",
            {
                "schema": LAB_SCHEMA_VERSION,
                "run_id": run_id,
                "created_at": _utc_now(),
                "target_term": spec.target_term,
                "contexts": list(spec.contexts),
                "candidates": [asdict(candidate) for candidate in spec.candidates],
                "language": spec.language,
                "locale": spec.locale,
                "voice_profile": spec.voice_profile,
                "generation_profile": spec.generation_profile,
                "seeds": list(spec.seeds),
                "scorer_identity": self.scorer.identity,
                "quality_attempts_per_cell": 1,
            },
        )
        records: list[dict[str, Any]] = []
        with manifest_path.open("x", encoding="utf-8") as manifest:
            for context_index, context in enumerate(spec.contexts, start=1):
                context_id = f"context.{context_index:02d}"
                for candidate in spec.candidates:
                    synthesis_text = render_candidate_context(
                        context, spec.target_term, candidate.text
                    )
                    for seed in spec.seeds:
                        record = await self._run_cell(
                            run_id=run_id,
                            run_directory=run_directory,
                            spec=spec,
                            context_id=context_id,
                            context=context,
                            candidate=candidate,
                            synthesis_text=synthesis_text,
                            seed=seed,
                        )
                        records.append(record)
                        manifest.write(
                            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                        )
                        manifest.flush()
                        os.fsync(manifest.fileno())
        _atomic_json(
            run_directory / "summary.json",
            {
                "schema": LAB_SCHEMA_VERSION,
                "run_id": run_id,
                "completed_at": _utc_now(),
                "total": len(records),
                "completed": sum(record["status"] == "completed" for record in records),
                "failed": sum(record["status"] != "completed" for record in records),
            },
        )
        return LabRunSummary(run_id, run_directory, manifest_path, tuple(records))

    async def _run_cell(
        self,
        *,
        run_id: str,
        run_directory: Path,
        spec: LabSpec,
        context_id: str,
        context: str,
        candidate: LabCandidate,
        synthesis_text: str,
        seed: int,
    ) -> dict[str, Any]:
        request_payload = {
            "text": synthesis_text,
            "language": spec.language,
            "voice_profile": spec.voice_profile,
            "generation_profile": spec.generation_profile,
            "seed": seed,
        }
        request_fingerprint = _canonical_sha256(request_payload)
        idempotency_key = f"pronunciation-lab:{request_fingerprint}"
        started = time.monotonic()
        try:
            result = await self.gateway.synthesize(
                text=synthesis_text,
                language=spec.language,
                voice_profile=spec.voice_profile,
                generation_profile=spec.generation_profile,
                seed=seed,
                idempotency_key=idempotency_key,
            )
        except (VoiceClientError, TimeoutError, OSError) as exc:
            result = LabVoiceResult(
                job_id=None,
                status="failed",
                requested_seed=seed,
                resolved_seed=None,
                voice_profile=spec.voice_profile,
                voice_identity=None,
                generation_profile=spec.generation_profile,
                generation_identity=None,
                model_id=None,
                model_revision=None,
                metadata={},
                error_category="lab_transport_or_timeout",
                error_message=str(exc),
            )
        elapsed = time.monotonic() - started
        metadata = result.metadata
        quality_status = metadata.get("quality_guard_status")
        if result.error_category == "chunk_quality_guard":
            quality_status = "rejected"
        elif quality_status is None:
            quality_status = "passed" if result.status == "completed" else "not_evaluated"
        wav_reference: str | None = None
        output_sha256: str | None = None
        scorer_result: dict[str, Any] | None = None
        output_duration = result.result_duration_seconds
        sample_rate = result.result_sample_rate
        status = result.status
        error_category = result.error_category
        error_message = result.error_message
        if result.status == "completed" and result.audio_bytes is None:
            status = "failed"
            error_category = "lab_audio_missing"
            error_message = "completed Qwen job returned no WAV payload"
        elif result.status == "completed" and result.audio_bytes is not None:
            output_sha256 = hashlib.sha256(result.audio_bytes).hexdigest()
            if result.result_sha256 and output_sha256 != result.result_sha256:
                status = "failed"
                error_category = "lab_audio_checksum_mismatch"
                error_message = "downloaded Qwen WAV checksum does not match job metadata"
            else:
                try:
                    info = sf.info(io.BytesIO(result.audio_bytes))
                except RuntimeError as exc:
                    status = "failed"
                    error_category = "lab_audio_invalid"
                    error_message = str(exc)
                else:
                    output_duration = info.duration
                    sample_rate = info.samplerate
                    wav_name = f"{context_id}--{candidate.id}--seed-{seed}.wav"
                    wav_path = run_directory / wav_name
                    _atomic_bytes(wav_path, result.audio_bytes)
                    wav_reference = wav_name
                    scorer_result = self.scorer.score(
                        wav_path=wav_path,
                        target_term=spec.target_term,
                        candidate_text=candidate.text,
                    )
        chunks = metadata.get("chunks", [])
        if not isinstance(chunks, list):
            chunks = []
        return {
            "schema": LAB_SCHEMA_VERSION,
            "run_id": run_id,
            "timestamp": _utc_now(),
            "target_term": spec.target_term,
            "context_id": context_id,
            "context": context,
            "candidate_id": candidate.id,
            "candidate_text": candidate.text,
            "candidate_strategy": candidate.strategy,
            "candidate_strategy_version": candidate.strategy_version,
            "synthesis_text": synthesis_text,
            "synthesis_text_sha256": hashlib.sha256(synthesis_text.encode()).hexdigest(),
            "language": spec.language,
            "locale": spec.locale,
            "requested_seed": seed,
            "resolved_seed": result.resolved_seed,
            "base_seed": result.resolved_seed,
            "chunk_seeds": [chunk.get("seed") for chunk in chunks if isinstance(chunk, dict)],
            "voice_profile": result.voice_profile,
            "voice_identity": result.voice_identity,
            "generation_profile": result.generation_profile,
            "generation_identity": result.generation_identity,
            "model_id": result.model_id,
            "model_revision": result.model_revision,
            "chunking_strategy_version": metadata.get("chunking_strategy_version"),
            "chunking_identity": metadata.get("chunking_identity"),
            "job_id": result.job_id,
            "status": status,
            "wav_reference": wav_reference,
            "duration_seconds": output_duration,
            "sample_rate": sample_rate,
            "wall_clock_seconds": elapsed,
            "synthesis_seconds": metadata.get("synthesis_seconds"),
            "quality_guard_status": quality_status,
            "quality_attempt_count": 1,
            "error_category": error_category,
            "error_message": error_message,
            "output_sha256": output_sha256,
            "request_fingerprint": request_fingerprint,
            "idempotency_key": idempotency_key,
            "scorer_identity": self.scorer.identity,
            "scorer_result": scorer_result,
        }


def render_candidate_context(context: str, target: str, candidate: str) -> str:
    if "{target}" in context:
        return context.replace("{target}", candidate)
    escaped = re.escape(target)
    prefix = r"(?<!\w)" if target[:1] and re.match(r"\w", target[0]) else ""
    suffix = r"(?!\w)" if target[-1:] and re.match(r"\w", target[-1]) else ""
    result, count = re.subn(f"{prefix}{escaped}{suffix}", lambda _: candidate, context)
    if count == 0:
        raise PronunciationLabValidationError(
            "each context must contain the exact target or a {target} placeholder"
        )
    return result


def load_manifest(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        records = tuple(json.loads(line) for line in lines if line.strip())
    except (OSError, json.JSONDecodeError) as exc:
        raise PronunciationLabValidationError("lab manifest is unreadable") from exc
    if not all(
        isinstance(record, dict) and record.get("schema") == LAB_SCHEMA_VERSION
        for record in records
    ):
        raise PronunciationLabValidationError("lab manifest schema is invalid")
    return records


def record_review(
    run_directory: Path,
    *,
    candidate_id: str,
    status: str,
    reviewer: str,
    notes: str | None = None,
) -> dict[str, Any]:
    if status not in {"approved", "rejected", "undecided"}:
        raise PronunciationLabValidationError("review status is invalid")
    if not reviewer.strip():
        raise PronunciationLabValidationError("reviewer is required")
    records = load_manifest(run_directory / "manifest.jsonl")
    if not any(record.get("candidate_id") == candidate_id for record in records):
        raise PronunciationLabValidationError("candidate is absent from the lab manifest")
    review = {
        "schema": LAB_SCHEMA_VERSION,
        "run_id": run_directory.name,
        "candidate_id": candidate_id,
        "status": status,
        "reviewer": reviewer,
        "notes": notes,
        "timestamp": _utc_now(),
    }
    review_path = run_directory / "reviews.jsonl"
    with review_path.open("a", encoding="utf-8") as output:
        fcntl.flock(output.fileno(), fcntl.LOCK_EX)
        output.write(json.dumps(review, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    return review


def latest_review(run_directory: Path, candidate_id: str) -> dict[str, Any] | None:
    path = run_directory / "reviews.jsonl"
    if not path.exists():
        return None
    try:
        reviews = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise PronunciationLabValidationError("lab review log is unreadable") from exc
    matches = [review for review in reviews if review.get("candidate_id") == candidate_id]
    return matches[-1] if matches else None


def approved_candidate(
    run_directory: Path, candidate_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    review = latest_review(run_directory, candidate_id)
    if review is None or review.get("status") != "approved":
        raise PronunciationLabValidationError(
            "candidate requires an explicit latest approved review"
        )
    records = [
        record
        for record in load_manifest(run_directory / "manifest.jsonl")
        if record.get("candidate_id") == candidate_id
    ]
    if not records or not any(record.get("status") == "completed" for record in records):
        raise PronunciationLabValidationError("approved candidate has no successful Qwen sample")
    first = records[0]
    if any(record.get("candidate_text") != first.get("candidate_text") for record in records):
        raise PronunciationLabValidationError("candidate manifest records are inconsistent")
    return first, review


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"lab-{timestamp}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_sha256(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode()
    _atomic_bytes(path, body + b"\n")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
