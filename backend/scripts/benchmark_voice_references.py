#!/usr/bin/env python3
# ruff: noqa: E402, E501
"""Opt-in, resumable x-vector-only sweep of the canonical Qwen references.

Planning is intentionally dependency-light and never creates a VoiceClient.  Add
``--run`` to permit GPU-backed synthesis.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.voice_client import VoiceClient, VoiceClientError, VoiceJobFailedError
from app.core.config import Settings
from app.services.audio_diagnostics import analyze_audio
from app.services.content_coverage import ContentCoverageThresholds, analyze_content_coverage
from app.services.production_audio import ProductionAudioError, publish_source_block
from app.services.pronunciation import PronunciationPreprocessor
from app.services.reference_benchmark import (
    CLONE_MODE,
    DEFAULT_ASR_LANGUAGE,
    FULL_SEEDS,
    QUICK_SEEDS,
    SYNTHESIS_LANGUAGE,
    ReferenceDescriptor,
    aggregate_summary,
    classify_content,
    discover_references,
    expand_matrix,
    load_reference_corpus,
    resume_decision,
    utc_run_id,
    write_listening_guide,
    write_listening_worksheet,
    write_summary_csv,
)
from app.services.transcription_service import FasterWhisperNarrationProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references-dir", type=Path, default=Path("/data/references"))
    parser.add_argument("--reference-metadata", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("/data/audio/benchmarks/reference-regression")
    )
    parser.add_argument("--mode", choices=("quick", "full"), default="quick")
    parser.add_argument("--refs", nargs="+")
    parser.add_argument("--all-refs", action="store_true")
    parser.add_argument("--generation", default="narration_v2")
    parser.add_argument("--seeds", nargs="+", type=int)
    parser.add_argument("--asr-language", default=DEFAULT_ASR_LANGUAGE)
    parser.add_argument("--resume", metavar="RUN_ID")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="return exit status 0 even when matrix cases fail; intended only for exploratory runs",
    )
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--no-asr", action="store_true")
    parser.add_argument(
        "--run", action="store_true", help="allow Qwen synthesis; otherwise plan only"
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.mode == "full" and not args.refs and not args.all_refs:
        parser.error("full mode requires --refs or --all-refs")
    if args.refs and args.all_refs:
        parser.error("use either --refs or --all-refs, not both")
    if args.max_cases is not None and args.max_cases <= 0:
        parser.error("--max-cases must be positive")
    if args.seeds is not None and any(seed < 1 or seed > 2**31 - 1 for seed in args.seeds):
        parser.error("seeds must be between 1 and 2147483647")


def _resolved_asr_language(value: str) -> str | None:
    normalized = value.strip()
    return None if not normalized or normalized.casefold() == "none" else normalized


def _benchmark_exit_status(*, failed_total: int, allow_failures: bool) -> int:
    """Fail automation on any unsuccessful matrix result unless explicitly waived."""
    return 0 if failed_total == 0 or allow_failures else 1


def _select_references(
    references: tuple[ReferenceDescriptor, ...],
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[ReferenceDescriptor, ...]:
    if not args.refs:
        return references
    by_path = {item.relative_path: item for item in references}
    selected: list[ReferenceDescriptor] = []
    for requested in args.refs:
        item = by_path.get(requested.replace("\\", "/"))
        if item is None:
            parser.error(f"reference is not a valid discovered WAV: {requested}")
        selected.append(item)
    return tuple(selected)


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        import os

        os.fsync(stream.fileno())


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("case_identity"):
                latest[str(item["case_identity"])] = item
    return latest


def _safe_reference_slug(reference: ReferenceDescriptor) -> str:
    return f"{reference.reference_identity[:16]}-{Path(reference.relative_path).stem}".replace(
        "/", "_"
    )


def _pronunciation_path(settings: Settings) -> Path:
    path = settings.pronunciation_glossary_path
    return path if path.is_file() else BACKEND_DIR / path


def _base_record(
    matrix_case: Any, run_id: str, corpus_version: str, normalized: Any, pronunciation: Any
) -> dict[str, Any]:
    return {
        "schema": "neovids_reference_regression_case_v1",
        "run_id": run_id,
        "attempt_id": utc_run_id(),
        "case_identity": matrix_case.identity,
        "reference": matrix_case.reference.relative_path,
        "reference_id": matrix_case.reference.relative_path,
        "reference_family": matrix_case.reference.reference_family,
        "reference_variant": matrix_case.reference.reference_variant,
        "reference_sha256": matrix_case.reference.sha256,
        "reference_identity": matrix_case.reference.reference_identity,
        "corpus_identity": "reference_regression_v1",
        "corpus_version": corpus_version,
        "case_id": matrix_case.case.case_id,
        "canonical_text": matrix_case.case.text,
        "actual_normalized_text": normalized.normalized_text,
        "actual_synthesis_text": pronunciation.synthesis_text,
        "pronunciation_substitutions": list(pronunciation.applied_replacements),
        "lexicon_identity": matrix_case.lexicon_identity,
        "clone_mode": CLONE_MODE,
        "language": SYNTHESIS_LANGUAGE,
        "asr_language": None,
        "generation_profile": matrix_case.generation_profile,
        "requested_seed": matrix_case.seed,
        "resolved_seed": None,
        "chunk_seeds": [],
        "generation_status": "failed",
        "artifact_integrity_status": "unavailable",
        "qwen_guard_status": "not_evaluated",
        "asr_status": "disabled",
        "content_status": "not_evaluated",
        "pronunciation_disagreement_status": "not_evaluated",
        "failure_category": None,
        "failure_reason": None,
    }


async def _run_case(
    matrix_case: Any,
    *,
    run_id: str,
    run_directory: Path,
    corpus_version: str,
    normalized: Any,
    pronunciation: Any,
    voice: VoiceClient,
    asr: FasterWhisperNarrationProvider | None,
    settings: Settings,
) -> dict[str, Any]:
    record = _base_record(matrix_case, run_id, corpus_version, normalized, pronunciation)
    record["asr_language"] = asr.asr_language if asr else None
    output = (
        run_directory
        / "refs"
        / _safe_reference_slug(matrix_case.reference)
        / f"seed_{matrix_case.seed}"
        / f"{matrix_case.case.case_id}.wav"
    )
    idempotency = (
        "reference-regression:" + hashlib.sha256(matrix_case.identity.encode()).hexdigest()
    )
    started = time.monotonic()
    print(
        f"reference: {matrix_case.reference.relative_path} case: {matrix_case.case.case_id} seed: {matrix_case.seed} stage: synthesis",
        flush=True,
    )
    created = None
    try:
        created = await voice.create_reference_narration(
            text=pronunciation.synthesis_text,
            language=SYNTHESIS_LANGUAGE,
            reference_id=matrix_case.reference.relative_path,
            generation_profile=matrix_case.generation_profile,
            seed=matrix_case.seed,
            idempotency_key=idempotency,
        )
        terminal = await voice.wait_for_narration(created.id)
        record["generation_status"] = "success"
        record["job_id"] = terminal.id
        record["resolved_seed"] = terminal.resolved_seed
        metadata = terminal.metadata or {}
        record["model_id"] = terminal.model_id
        record["model_revision"] = terminal.model_revision
        record["generation_latency_seconds"] = round(time.monotonic() - started, 6)
        record["voice_profile"] = terminal.voice_profile
        record["generation_identity"] = metadata.get("generation_identity")
        record["generation_profile_identity"] = metadata.get("generation_identity")
        record["voice_identity"] = metadata.get("voice_identity")
        record["chunking_identity"] = metadata.get("chunking_identity")
        record["chunking_strategy_version"] = metadata.get("chunking_strategy_version")
        record["qwen_guard_status"] = str(metadata.get("quality_guard_status") or "passed")
        chunks = metadata.get("chunks")
        if isinstance(chunks, list):
            record["chunk_seeds"] = [item.get("seed") for item in chunks if isinstance(item, dict)]
        audio = await voice.download_audio(terminal.id)
        print("stage: artifact_validation", flush=True)
        physical = publish_source_block(
            audio,
            output,
            expected_sha256=terminal.result_sha256,
            expected_byte_size=terminal.result_byte_size,
            expected_duration_seconds=terminal.result_duration_seconds,
            expected_sample_rate=terminal.result_sample_rate,
            expected_channels=terminal.result_channels,
            expected_subtype=terminal.result_subtype,
            expected_frame_count=terminal.result_frame_count,
            expected_riff_data_byte_size=terminal.result_riff_data_byte_size,
            text_length_chars=len(pronunciation.synthesis_text),
            max_duration_seconds=settings.audio_block_max_duration_seconds,
            max_seconds_per_char=settings.audio_block_max_seconds_per_char,
            duration_grace_seconds=settings.audio_block_duration_grace_seconds,
        )
        record.update(
            {
                "artifact_integrity_status": "valid",
                "artifact_sha256": physical.sha256,
                "artifact_size": physical.byte_size,
                "physical_duration_seconds": physical.duration_seconds,
                "physical_sample_rate": physical.sample_rate,
                "physical_channels": physical.channels,
                "physical_subtype": physical.subtype,
                "physical_frame_count": physical.frame_count,
                "physical_riff_data_byte_size": physical.riff_data_byte_size,
                "peak_dbfs": physical.sample_peak_dbfs,
                "clipping_count": physical.clipping_sample_count,
                "clipping_rate": physical.clipping_sample_count
                / max(physical.frame_count * physical.channels, 1),
                "wav": str(output.relative_to(run_directory)),
            }
        )
        record["realtime_factor"] = (
            record["generation_latency_seconds"] / physical.duration_seconds
            if physical.duration_seconds
            else None
        )
        diagnostics = analyze_audio(
            output,
            silence_threshold_dbfs=settings.audio_diagnostics_silence_threshold_dbfs,
            minimum_silence_seconds=settings.audio_diagnostics_minimum_silence_seconds,
        )
        signal = diagnostics["signal"]
        record.update(
            {
                "pause_count": signal.get("silence_run_count"),
                "silence_ratio": signal.get("silence_ratio"),
                "leading_silence_seconds": signal.get("leading_silence_seconds"),
                "trailing_silence_seconds": signal.get("trailing_silence_seconds"),
                "rms_dbfs": signal.get("rms_dbfs"),
                "dc_offset": signal.get("dc_offset"),
                "noise_floor_estimate_dbfs": signal.get("noise_floor_estimate_dbfs"),
                "longest_silence_seconds": signal.get("longest_silence_seconds"),
                "transient_count": len(diagnostics.get("transient_candidates", [])),
            }
        )
        if asr is not None:
            print("stage: ASR", flush=True)
            try:
                transcription = asr.transcribe(output)
                record["asr_status"] = "success"
                record["asr_text"] = transcription.text
                report = analyze_content_coverage(
                    pronunciation.synthesis_text,
                    transcription.text,
                    thresholds=ContentCoverageThresholds(
                        minimum_coverage_ratio=settings.audio_content_minimum_coverage_ratio,
                        maximum_deletion_ratio=settings.audio_content_maximum_deletion_ratio,
                        maximum_insertion_ratio=settings.audio_content_maximum_insertion_ratio,
                        maximum_repetition_ratio=settings.audio_content_maximum_repetition_ratio,
                        maximum_missing_contiguous_tokens=settings.audio_content_maximum_missing_tokens,
                        maximum_trailing_missing_tokens=settings.audio_content_maximum_trailing_missing_tokens,
                    ),
                )
                record["content_diagnostics"] = report.to_dict()
                content_status, disagreement = classify_content(report)
                record["content_status"] = content_status
                record["pronunciation_disagreement_status"] = disagreement
                metrics = report.metrics
                record.update(
                    {
                        "wer": metrics.get("normalized_wer"),
                        "cer": metrics.get("normalized_cer"),
                        "coverage": metrics.get("coverage_ratio"),
                    }
                )
            except (RuntimeError, OSError, ValueError) as exc:
                record["asr_status"] = "failed"
                record["content_status"] = "not_evaluated"
                record["asr_failure_reason"] = str(exc)
        print("stage: complete", flush=True)
    except VoiceJobFailedError as exc:
        if created is not None:
            try:
                terminal = await voice.get_narration(created.id)
                record["job_id"] = terminal.id
                record["resolved_seed"] = terminal.resolved_seed
                record["model_id"] = terminal.model_id
                record["model_revision"] = terminal.model_revision
                metadata = terminal.metadata or {}
                record["qwen_guard_status"] = str(
                    metadata.get("quality_guard_status")
                    or ("rejected" if exc.category == "chunk_quality_guard" else "not_evaluated")
                )
                record["guard_diagnostics"] = metadata.get("quality_guard") or metadata.get(
                    "quality_guard_diagnostics"
                )
            except VoiceClientError:
                pass
        record.update(
            {
                "failure_category": exc.category,
                "failure_reason": exc.public_message,
                "qwen_guard_status": "rejected"
                if exc.category == "chunk_quality_guard"
                else record["qwen_guard_status"],
            }
        )
    except (VoiceClientError, ProductionAudioError, OSError, RuntimeError, ValueError) as exc:
        record.update({"failure_category": type(exc).__name__, "failure_reason": str(exc)})
    return record


async def run_benchmark(
    args: argparse.Namespace,
    references: tuple[ReferenceDescriptor, ...],
    invalid: tuple[Any, ...],
    matrix: tuple[Any, ...],
    corpus: Any,
    lexicon_identity: str,
) -> int:
    run_id = args.resume or utc_run_id()
    run_directory = args.output / run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    run_metadata = {
        "schema": "neovids_reference_regression_run_v1",
        "run_id": run_id,
        "created_at": time.time(),
        "mode": args.mode,
        "clone_mode": CLONE_MODE,
        "generation_profile": args.generation,
        "seeds": list(args.seeds or (QUICK_SEEDS if args.mode == "quick" else FULL_SEEDS)),
        "asr_language": _resolved_asr_language(args.asr_language),
        "synthesis_language": SYNTHESIS_LANGUAGE,
        "corpus_version": corpus.version,
        "lexicon_identity": lexicon_identity,
        "references": [r.to_dict() for r in references],
        "invalid_references": [r.to_dict() for r in invalid],
    }
    existing_run = run_directory / "run.json"
    if existing_run.exists():
        try:
            prior = json.loads(existing_run.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read resume metadata: {existing_run}") from exc
        expected = {
            "mode": args.mode,
            "clone_mode": CLONE_MODE,
            "generation_profile": args.generation,
            "seeds": list(args.seeds or (QUICK_SEEDS if args.mode == "quick" else FULL_SEEDS)),
            "asr_language": _resolved_asr_language(args.asr_language),
            "corpus_version": corpus.version,
            "lexicon_identity": lexicon_identity,
        }
        if any(prior.get(key) != value for key, value in expected.items()):
            raise ValueError("resume configuration differs from the original run")
    else:
        _atomic_json(run_directory / "run.json", run_metadata)
        with (run_directory / "references.jsonl").open("w", encoding="utf-8") as stream:
            for reference in references:
                stream.write(
                    json.dumps(reference.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
                )
            for item in invalid:
                stream.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    manifest = run_directory / "manifest.jsonl"
    previous = _load_manifest(manifest)
    run_started = time.monotonic()
    skipped = 0
    settings = Settings(_env_file=None)
    asr = (
        None
        if args.no_asr
        else FasterWhisperNarrationProvider(
            settings,
            asr_language=_resolved_asr_language(args.asr_language),
        )
    )
    normalizer = __import__(
        "app.services.text_normalization", fromlist=["PortugueseTextNormalizer"]
    ).PortugueseTextNormalizer(version=settings.text_normalizer_version)
    pronunciation = PronunciationPreprocessor.load(_pronunciation_path(settings))
    async with VoiceClient(settings) as voice:
        for index, matrix_case in enumerate(matrix, start=1):
            print(
                f"[{index:03d}/{len(matrix):03d}] reference={matrix_case.reference.relative_path} "
                f"case={matrix_case.case.case_id} seed={matrix_case.seed} stage=prepare "
                f"elapsed={time.monotonic() - run_started:.1f}s",
                flush=True,
            )
            output = (
                run_directory
                / "refs"
                / _safe_reference_slug(matrix_case.reference)
                / f"seed_{matrix_case.seed}"
                / f"{matrix_case.case.case_id}.wav"
            )
            decision = resume_decision(
                previous.get(matrix_case.identity), output, retry_failed=args.retry_failed
            )
            if decision == "skip":
                skipped += 1
                print(
                    f"[{index:03d}/{len(matrix):03d}] skipped {matrix_case.reference.relative_path} / {matrix_case.case.case_id} / seed {matrix_case.seed}",
                    flush=True,
                )
                continue
            normalized = normalizer.normalize(matrix_case.case.text)
            prepared = pronunciation.preprocess(
                normalized.normalized_text, canonical_text=matrix_case.case.text
            )
            record = await _run_case(
                matrix_case,
                run_id=run_id,
                run_directory=run_directory,
                corpus_version=corpus.version,
                normalized=normalized,
                pronunciation=prepared,
                voice=voice,
                asr=asr,
                settings=settings,
            )
            _append_jsonl(manifest, record)
            previous[matrix_case.identity] = record
    records = list(_load_manifest(manifest).values())
    summary = aggregate_summary(records, references)
    summary["run_id"] = run_id
    _atomic_json(run_directory / "summary.json", summary)
    write_summary_csv(run_directory / "summary.csv", summary)
    write_listening_worksheet(run_directory / "listening.csv", records)
    write_listening_guide(run_directory / "listening.md")
    successful_total = sum(
        r.get("generation_status") == "success" and r.get("artifact_integrity_status") == "valid"
        for r in records
    )
    failed_total = len(records) - successful_total
    print(
        f"completed={len(records)} successful={successful_total} failed={failed_total} "
        f"skipped={skipped} remaining={max(len(matrix) - len(records), 0)} "
        f"elapsed={time.monotonic() - run_started:.1f}s output={run_directory}",
        flush=True,
    )
    exit_status = _benchmark_exit_status(
        failed_total=failed_total, allow_failures=args.allow_failures
    )
    if exit_status:
        print(
            "benchmark completed with failures; returning non-zero "
            "(use --allow-failures only for intentional exploratory collection)",
            flush=True,
        )
    return exit_status


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    if args.resume and not args.run:
        # A resume plan is safe and useful; it still performs no synthesis.
        pass
    metadata_path = args.reference_metadata
    if metadata_path is None:
        candidate_metadata = args.references_dir / "reference_metadata.yaml"
        if candidate_metadata.is_file():
            metadata_path = candidate_metadata
    references, invalid = discover_references(args.references_dir, metadata_path)
    corpus = load_reference_corpus()
    benchmark_settings = Settings(_env_file=None)
    lexicon_identity = PronunciationPreprocessor.load(
        _pronunciation_path(benchmark_settings)
    ).identity()
    selected = _select_references(references, args, parser)
    seeds = tuple(args.seeds or (QUICK_SEEDS if args.mode == "quick" else FULL_SEEDS))
    cases = corpus.cases(args.mode)
    planned = expand_matrix(
        selected,
        cases,
        seeds,
        generation_profile=args.generation,
        lexicon_identity=lexicon_identity,
        max_cases=args.max_cases,
    )
    print(f"Discovered references: {len(references)}")
    print(f"Valid references: {len(references)}")
    print(f"Invalid references: {len(invalid)}")
    for item in invalid:
        print(f"  invalid: {item.relative_path} ({item.reason})")
    print(
        f"Quick cases: {len(cases) if args.mode == 'quick' else 6}; Seeds: {len(seeds)}; Planned generations: {len(planned)}"
    )
    print(f"Selected references: {', '.join(item.relative_path for item in selected) or '(none)'}")
    print(f"Cases: {', '.join(item.case_id for item in cases)}")
    print(
        f"Generation profile: {args.generation}; Clone mode: {CLONE_MODE}; Synthesis language: {SYNTHESIS_LANGUAGE}; ASR language: {_resolved_asr_language(args.asr_language) or 'autodetect'}"
    )
    print(f"Corpus version: {corpus.version}; Output: {args.output}")
    print(f"Pronunciation lexicon identity: {lexicon_identity}")
    if not args.run:
        print("PLAN ONLY: no Qwen synthesis or CUDA inference (pass --run to execute)")
        return 0
    return asyncio.run(run_benchmark(args, selected, invalid, planned, corpus, lexicon_identity))


if __name__ == "__main__":
    raise SystemExit(main())
