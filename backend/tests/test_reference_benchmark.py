from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services.content_coverage import analyze_content_coverage
from app.services.reference_benchmark import (
    CLONE_MODE,
    DEFAULT_PRONUNCIATION_CORPUS_PATH,
    aggregate_human_ratings,
    aggregate_summary,
    classify_content,
    discover_references,
    expand_matrix,
    load_pronunciation_corpus,
    load_reference_corpus,
    resume_decision,
    write_listening_guide,
    write_listening_worksheet,
)
from scripts import benchmark_voice_references


def _write_wav(path: Path, *, rate: int = 24_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(rate // 2, dtype=np.float32), rate, format="WAV", subtype="PCM_16")


def test_discovery_is_dynamic_sorted_and_preserves_optional_metadata(tmp_path: Path) -> None:
    _write_wav(tmp_path / "z.wav")
    _write_wav(tmp_path / "nested" / "a.wav")
    (tmp_path / "ignore.txt").write_text("not audio")
    metadata = tmp_path / "reference_metadata.yaml"
    metadata.write_text(
        "references:\n  z.wav:\n    family: family-a\n    variant: full_raw\n    notes: source\n",
        encoding="utf-8",
    )
    references, invalid = discover_references(tmp_path, metadata)
    assert not invalid
    assert [item.relative_path for item in references] == ["nested/a.wav", "z.wav"]
    assert references[1].reference_family == "family-a"
    assert references[0].reference_family is None
    assert references[0].sha256 and references[0].reference_identity


def test_invalid_reference_is_reported_without_crashing(tmp_path: Path) -> None:
    _write_wav(tmp_path / "valid.wav")
    (tmp_path / "broken.wav").write_bytes(b"not a wav")
    valid, invalid = discover_references(tmp_path)
    assert [item.relative_path for item in valid] == ["valid.wav"]
    assert invalid[0].relative_path == "broken.wav"


def test_symlink_escape_is_invalid(tmp_path: Path) -> None:
    outside = tmp_path / "outside.wav"
    _write_wav(outside)
    root = tmp_path / "refs"
    root.mkdir()
    (root / "escape.wav").symlink_to(outside)
    valid, invalid = discover_references(root)
    assert not valid
    assert "escapes" in invalid[0].reason


def test_quick_and_full_matrix_counts_and_stable_identity() -> None:
    corpus = load_reference_corpus()
    references, invalid = discover_references(
        Path(__file__).resolve().parents[3] / "qwen3-tts-workshop" / "data" / "references"
    )
    assert not invalid
    quick = expand_matrix(references[:2], corpus.quick_cases, (101,))
    full = expand_matrix(references[:2], corpus.cases("full"), (101, 202, 303))
    assert len(quick) == 12
    assert len(full) == 72
    assert (
        quick[0].identity == expand_matrix(references[:2], corpus.quick_cases, (101,))[0].identity
    )
    assert quick[0].reference.relative_path
    assert CLONE_MODE == "x_vector_only"


def test_pronunciation_corpus_is_independently_versioned() -> None:
    corpus = load_pronunciation_corpus(DEFAULT_PRONUNCIATION_CORPUS_PATH)
    assert corpus["version"] == "pronunciation_reference_v1"
    assert len(corpus["targets"]) >= 10
    assert not hasattr(corpus, "quick_cases")


def test_resume_decisions_preserve_success_and_failures(tmp_path: Path) -> None:
    artifact = tmp_path / "take.wav"
    artifact.write_bytes(b"audio")
    import hashlib

    success = {
        "generation_status": "success",
        "artifact_sha256": hashlib.sha256(b"audio").hexdigest(),
    }
    assert resume_decision(success, artifact) == "skip"
    artifact.write_bytes(b"changed")
    assert resume_decision(success, artifact) == "run"
    assert resume_decision({"generation_status": "failed"}, artifact) == "skip"
    assert resume_decision({"generation_status": "failed"}, artifact, retry_failed=True) == "run"


def test_full_mode_requires_explicit_reference_selection() -> None:
    parser = benchmark_voice_references.build_parser()
    args = parser.parse_args(["--mode", "full"])
    with pytest.raises(SystemExit):
        benchmark_voice_references._validate_args(args, parser)


def test_plan_never_constructs_voice_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_wav(tmp_path / "reference.wav")

    class ExplodingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("dry plan must not construct VoiceClient")

    monkeypatch.setattr(benchmark_voice_references, "VoiceClient", ExplodingClient)
    assert (
        benchmark_voice_references.main(
            ["--references-dir", str(tmp_path), "--output", str(tmp_path / "out")]
        )
        == 0
    )


def test_blank_benchmark_asr_language_resolves_to_autodetect() -> None:
    assert benchmark_voice_references._resolved_asr_language("  ") is None


def test_reference_benchmark_failures_are_nonzero_unless_explicitly_allowed() -> None:
    assert (
        benchmark_voice_references._benchmark_exit_status(
            failed_total=1, allow_failures=False
        )
        == 1
    )
    assert (
        benchmark_voice_references._benchmark_exit_status(
            failed_total=5, allow_failures=True
        )
        == 0
    )
    assert (
        benchmark_voice_references._benchmark_exit_status(
            failed_total=0, allow_failures=False
        )
        == 0
    )


def test_reference_benchmark_uses_frozen_generation_profile_by_default() -> None:
    args = benchmark_voice_references.build_parser().parse_args([])
    assert args.generation == "narration_v2"
    assert args.allow_failures is False


@pytest.mark.asyncio
async def test_real_run_path_returns_nonzero_for_failed_matrix_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    references_dir = tmp_path / "references"
    _write_wav(references_dir / "reference.wav")
    references, invalid = discover_references(references_dir)
    corpus = load_reference_corpus()
    matrix = expand_matrix(
        references,
        corpus.quick_cases,
        (101,),
        generation_profile="narration_v2",
        lexicon_identity="l" * 64,
        max_cases=1,
    )
    args = benchmark_voice_references.build_parser().parse_args(
        [
            "--references-dir",
            str(references_dir),
            "--output",
            str(tmp_path / "output"),
            "--max-cases",
            "1",
            "--no-asr",
            "--run",
        ]
    )

    class FakeVoiceClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self) -> FakeVoiceClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def failed_case(matrix_case: object, **_: object) -> dict[str, object]:
        return {
            "case_identity": matrix_case.identity,  # type: ignore[attr-defined]
            "reference_identity": matrix_case.reference.reference_identity,  # type: ignore[attr-defined]
            "generation_status": "failed",
            "artifact_integrity_status": "unavailable",
            "qwen_guard_status": "not_evaluated",
            "asr_status": "disabled",
            "content_status": "not_evaluated",
            "pronunciation_disagreement_status": "not_evaluated",
            "failure_category": "VoiceClientTransientError",
            "failure_reason": "systemic connectivity failure",
        }

    monkeypatch.setattr(benchmark_voice_references, "VoiceClient", FakeVoiceClient)
    monkeypatch.setattr(benchmark_voice_references, "_run_case", failed_case)

    assert (
        await benchmark_voice_references.run_benchmark(
            args,
            references,
            invalid,
            matrix,
            corpus,
            "l" * 64,
        )
        == 1
    )


def test_content_pronunciation_disagreement_is_inconclusive() -> None:
    report = analyze_content_coverage(
        "Rockstar Games iniciou a missão", "Rock star games iniciou a missão"
    )
    status, disagreement = classify_content(report)
    assert status in {"complete", "inconclusive"}
    assert disagreement in {"none", "possible", "likely"}


def test_worksheet_and_human_ratings_are_separate(tmp_path: Path) -> None:
    record = {
        "generation_status": "success",
        "artifact_integrity_status": "valid",
        "reference": "ref.wav",
        "case_id": "natural_ptbr",
        "requested_seed": 101,
        "wav": "refs/ref/seed_101/take.wav",
    }
    worksheet = tmp_path / "listening.csv"
    write_listening_worksheet(worksheet, [record])
    write_listening_guide(tmp_path / "listening.md")
    text = worksheet.read_text(encoding="utf-8")
    assert "timbre_similarity_1_5" in text and "take-0001" in text
    lines = text.splitlines()
    header = lines[0].split(",")
    values = lines[1].split(",")
    values[header.index("overall_1_5")] = "5"
    worksheet.write_text("\n".join([lines[0], ",".join(values)]) + "\n", encoding="utf-8")
    ratings = aggregate_human_ratings(worksheet)
    assert ratings["ratings"]["overall_1_5"]["mean"] == 5.0
    assert (tmp_path / "listening.md").exists()


def test_summary_aggregates_without_winner() -> None:
    references, _ = discover_references(
        Path(__file__).resolve().parents[3] / "qwen3-tts-workshop" / "data" / "references"
    )
    records = [
        {
            "reference_identity": references[0].reference_identity,
            "generation_status": "success",
            "asr_status": "success",
            "content_status": "complete",
            "qwen_guard_status": "passed",
            "artifact_integrity_status": "valid",
            "wer": 0.1,
            "cer": 0.2,
            "coverage": 0.9,
            "physical_duration_seconds": 1.0,
            "generation_latency_seconds": 2.0,
            "realtime_factor": 2.0,
        }
    ]
    summary = aggregate_summary(records, references[:1])
    assert summary["references"][0]["successful_case_count"] == 1
    assert "winner" not in json.dumps(summary)
