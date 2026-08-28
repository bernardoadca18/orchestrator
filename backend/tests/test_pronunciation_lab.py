from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from app.services.pronunciation_lab import (
    InitialismCandidateSource,
    LabCandidate,
    LabSpec,
    LabVoiceResult,
    ManualCandidateSource,
    PronunciationLabRunner,
    PronunciationLabValidationError,
    approved_candidate,
    latest_review,
    load_manifest,
    record_review,
    render_candidate_context,
)


def _wav_bytes() -> bytes:
    output = io.BytesIO()
    sf.write(output, np.zeros(2_400, dtype=np.float32), 24_000, format="WAV", subtype="PCM_16")
    return output.getvalue()


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, **request: Any) -> LabVoiceResult:
        self.calls.append(request)
        failed = "Falha" in request["text"]
        metadata = {
            "voice_identity": "voice-id",
            "generation_identity": "generation-id",
            "chunking_strategy_version": "chunk-v1",
            "chunking_identity": "chunk-id",
            "chunks": [{"seed": request["seed"] + 7}],
            "quality_guard_status": "rejected" if failed else "passed",
            "synthesis_seconds": 0.02,
        }
        audio = None if failed else _wav_bytes()
        return LabVoiceResult(
            job_id=f"job-{len(self.calls)}",
            status="failed" if failed else "completed",
            requested_seed=request["seed"],
            resolved_seed=request["seed"],
            voice_profile=request["voice_profile"],
            voice_identity="voice-id",
            generation_profile=request["generation_profile"],
            generation_identity="generation-id",
            model_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            model_revision="pinned-revision",
            metadata=metadata,
            audio_bytes=audio,
            result_duration_seconds=0.1 if audio else None,
            result_sample_rate=24_000 if audio else None,
            error_category="chunk_quality_guard" if failed else None,
            error_message="runaway output" if failed else None,
        )


def _spec(*, run_id: str = "lab-test-run") -> LabSpec:
    return LabSpec(
        target_term="Rockstar Games",
        contexts=("O jogo foi criado pela {target}.",),
        candidates=(
            LabCandidate("manual.raw", "Rockstar Games"),
            LabCandidate("manual.failed", "Falha"),
        ),
        language="Portuguese",
        locale="pt-BR",
        voice_profile="bernardo_v1",
        generation_profile="narration_temp08",
        seeds=(101, 202),
        run_id=run_id,
    )


@pytest.mark.asyncio
async def test_controlled_matrix_persists_success_and_quality_failure(tmp_path: Path) -> None:
    gateway = FakeGateway()
    summary = await PronunciationLabRunner(gateway).run(_spec(), output_root=tmp_path)
    records = load_manifest(summary.manifest_path)
    assert len(gateway.calls) == len(records) == 4
    assert {call["language"] for call in gateway.calls} == {"Portuguese"}
    assert {call["voice_profile"] for call in gateway.calls} == {"bernardo_v1"}
    assert {call["generation_profile"] for call in gateway.calls} == {"narration_temp08"}
    assert {call["seed"] for call in gateway.calls} == {101, 202}
    assert all(record["quality_attempt_count"] == 1 for record in records)

    successful = [record for record in records if record["status"] == "completed"]
    failed = [record for record in records if record["status"] == "failed"]
    assert len(successful) == len(failed) == 2
    assert all((summary.run_directory / record["wav_reference"]).is_file() for record in successful)
    assert all(record["quality_guard_status"] == "rejected" for record in failed)
    assert all(record["error_category"] == "chunk_quality_guard" for record in failed)
    assert all(record["wav_reference"] is None for record in failed)


@pytest.mark.asyncio
async def test_semantically_identical_runs_reuse_the_same_qwen_idempotency_keys(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    runner = PronunciationLabRunner(gateway)
    first = await runner.run(_spec(run_id="lab-first"), output_root=tmp_path)
    first_keys = [record["idempotency_key"] for record in first.records]
    second = await runner.run(_spec(run_id="lab-second"), output_root=tmp_path)
    second_keys = [record["idempotency_key"] for record in second.records]
    assert first_keys == second_keys


@pytest.mark.asyncio
async def test_review_is_explicit_and_rejection_cannot_be_promoted(tmp_path: Path) -> None:
    summary = await PronunciationLabRunner(FakeGateway()).run(_spec(), output_root=tmp_path)
    assert latest_review(summary.run_directory, "manual.raw") is None
    record_review(
        summary.run_directory,
        candidate_id="manual.raw",
        status="rejected",
        reviewer="operator",
        notes="Pronúncia artificial.",
    )
    with pytest.raises(PronunciationLabValidationError, match="approved review"):
        approved_candidate(summary.run_directory, "manual.raw")

    review = record_review(
        summary.run_directory,
        candidate_id="manual.raw",
        status="approved",
        reviewer="operator",
        notes="Ouvido em ambos os seeds.",
    )
    candidate, latest = approved_candidate(summary.run_directory, "manual.raw")
    assert candidate["candidate_text"] == "Rockstar Games"
    assert latest == review


def test_manual_and_initialism_candidate_sources_are_deterministic() -> None:
    manual = ManualCandidateSource.candidates(["GTA", "G T A"])
    assert [candidate.text for candidate in manual] == ["GTA", "G T A"]
    assert manual == ManualCandidateSource.candidates(["GTA", "G T A"])
    assert [candidate.text for candidate in InitialismCandidateSource.candidates("GTA")] == [
        "GTA",
        "G T A",
        "Gê Tê Á",
    ]


def test_context_rendering_is_token_safe_and_supports_placeholder() -> None:
    assert render_candidate_context("{target}: teste", "GPU", "Gê Pê U") == "Gê Pê U: teste"
    assert render_candidate_context("A GPU, não xGPU.", "GPU", "Gê Pê U") == (
        "A Gê Pê U, não xGPU."
    )
    with pytest.raises(PronunciationLabValidationError, match="context"):
        render_candidate_context("Sem o termo.", "GPU", "Gê Pê U")


@pytest.mark.parametrize(
    "spec",
    [
        replace(_spec(), target_term=""),
        replace(_spec(), contexts=()),
        replace(_spec(), candidates=()),
        replace(_spec(), seeds=(0,)),
        replace(_spec(), candidates=(LabCandidate("bad id", "value"),)),
    ],
)
def test_invalid_lab_specs_are_rejected(spec: LabSpec) -> None:
    with pytest.raises(PronunciationLabValidationError):
        spec.validate()
