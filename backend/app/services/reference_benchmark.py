# ruff: noqa: E501

"""Pure, CPU-safe helpers for the opt-in voice reference regression benchmark.

The runner deliberately keeps discovery/matrix/reporting separate from the Qwen
transport.  This makes the dry plan and the normal unit-test suite incapable of
loading CUDA or invoking synthesis.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import struct
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import yaml

from app.services.content_coverage import ContentCoverageReport
from app.services.production_audio import ProductionAudioError

REFERENCE_BENCHMARK_SCHEMA = "neovids_reference_regression_v1"
REFERENCE_CORPUS_SCHEMA = "neovids_reference_regression_corpus_v1"
DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "reference_regression_corpus.yaml"
)
DEFAULT_PRONUNCIATION_CORPUS_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "pronunciation_reference_corpus.yaml"
)
CLONE_MODE = "x_vector_only"
SYNTHESIS_LANGUAGE = "Portuguese"
DEFAULT_ASR_LANGUAGE = "pt"
QUICK_SEEDS = (101,)
FULL_SEEDS = (101, 202, 303)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReferenceDescriptor:
    filename: str
    relative_path: str
    path: str
    reference_family: str | None
    reference_variant: str | None
    reference_notes: str | None
    sha256: str
    reference_identity: str
    file_size: int
    duration_seconds: float
    sample_rate: int
    channels: int
    subtype: str
    frame_count: int
    block_alignment: int | None
    riff_declared_byte_size: int | None
    riff_data_byte_size: int | None
    riff_integrity_status: str
    peak_dbfs: float
    rms_dbfs: float | None
    clipping_count: int
    clipping_rate: float
    dc_offset: float | None
    leading_silence_seconds: float | None
    trailing_silence_seconds: float | None
    silence_ratio: float | None
    longest_silence_seconds: float | None
    noise_floor_estimate_dbfs: float | None
    transient_candidates: int | None
    hum_indicators: dict[str, Any] | None

    def to_dict(self, *, public: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["reference_id"] = self.relative_path
        if public:
            value.pop("path", None)
        return value


@dataclass(frozen=True)
class InvalidReference:
    filename: str
    relative_path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    text: str


@dataclass(frozen=True)
class ReferenceCorpus:
    version: str
    quick_cases: tuple[CorpusCase, ...]
    full_cases: tuple[CorpusCase, ...]

    def cases(self, mode: str) -> tuple[CorpusCase, ...]:
        if mode == "quick":
            return self.quick_cases
        if mode == "full":
            return self.quick_cases + self.full_cases
        raise ValueError("mode must be quick or full")


@dataclass(frozen=True)
class MatrixCase:
    reference: ReferenceDescriptor
    case: CorpusCase
    seed: int
    generation_profile: str
    lexicon_identity: str
    identity: str


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _metadata_mapping(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"reference metadata is unreadable: {path}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("references", {}), dict):
        raise ValueError("reference metadata must contain a references mapping")
    result: dict[str, dict[str, str]] = {}
    for key, value in raw["references"].items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError("reference metadata entries must be mappings")
        result[key.replace("\\", "/")] = {
            field: str(value[field]).strip()
            for field in ("family", "variant", "notes")
            if value.get(field) is not None and str(value[field]).strip()
        }
    return result


def _signal_values(diagnostics: dict[str, Any]) -> tuple[Any, ...]:
    signal = diagnostics.get("signal", {})
    return (
        signal.get("rms_dbfs"),
        signal.get("dc_offset"),
        signal.get("leading_silence_seconds"),
        signal.get("trailing_silence_seconds"),
        signal.get("silence_ratio"),
        signal.get("longest_silence_seconds"),
        signal.get("noise_floor_estimate_dbfs"),
        diagnostics.get("transient_candidates"),
        signal.get("hum_indicators"),
    )


def _inspect_source_wav(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inspect reference WAVs without imposing the PCM16 *output* contract."""
    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("reference WAV is unreadable") from exc
    if info.format != "WAV" or info.frames <= 0 or info.channels <= 0 or info.samplerate <= 0:
        raise ProductionAudioError("reference WAV is empty or not a supported WAV")
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ProductionAudioError("reference WAV has an invalid RIFF envelope")
        declared_size = struct.unpack_from("<I", header, 4)[0] + 8
        if declared_size != file_size:
            raise ProductionAudioError("reference WAV RIFF size does not match file size")
        data_size: int | None = None
        position = 12
        while position < file_size:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise ProductionAudioError("reference WAV RIFF chunk header is truncated")
            chunk_id = chunk[:4]
            chunk_size = struct.unpack_from("<I", chunk, 4)[0]
            position += 8
            if position + chunk_size + (chunk_size % 2) > file_size:
                raise ProductionAudioError("reference WAV RIFF chunk exceeds file size")
            if chunk_id == b"data":
                data_size = chunk_size
            stream.seek(chunk_size + (chunk_size % 2), os.SEEK_CUR)
            position += chunk_size + (chunk_size % 2)
        if position != file_size or data_size is None:
            raise ProductionAudioError("reference WAV has no complete data chunk")
    sample_rate = int(info.samplerate)
    threshold = 10.0 ** (-45.0 / 20.0)
    minimum_silence_frames = round(0.15 * sample_rate)
    peak = 0.0
    clipping = 0
    mono_sum = 0.0
    mono_square_sum = 0.0
    mono_count = 0
    silence_start: int | None = None
    spans_frames: list[tuple[int, int]] = []
    global_frame = 0
    try:
        with sf.SoundFile(str(path), mode="r") as source:
            for samples in source.blocks(
                blocksize=262_144,
                dtype="float64",
                always_2d=True,
            ):
                if not np.all(np.isfinite(samples)):
                    raise ProductionAudioError("reference WAV contains non-finite samples")
                absolute = np.abs(samples)
                peak = max(peak, float(np.max(absolute)))
                clipping += int(np.count_nonzero(absolute >= 0.999))
                mono = np.mean(samples, axis=1, dtype=np.float64)
                mono_sum += float(np.sum(mono, dtype=np.float64))
                mono_square_sum += float(np.sum(np.square(mono), dtype=np.float64))
                mono_count += len(mono)
                mask = np.abs(mono) <= threshold
                if silence_start is not None and not mask[0]:
                    if global_frame - silence_start >= minimum_silence_frames:
                        spans_frames.append((silence_start, global_frame))
                    silence_start = None
                padded = np.pad(mask.astype(np.int8), (1, 1))
                edges = np.flatnonzero(np.diff(padded))
                for start, end in edges.reshape(-1, 2):
                    run_start = (
                        silence_start
                        if int(start) == 0 and silence_start is not None
                        else global_frame + int(start)
                    )
                    if int(end) < len(mask):
                        if global_frame + int(end) - run_start >= minimum_silence_frames:
                            spans_frames.append((run_start, global_frame + int(end)))
                        silence_start = None
                    else:
                        silence_start = run_start
                global_frame += len(mono)
    except ProductionAudioError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ProductionAudioError("reference WAV is unreadable") from exc
    if silence_start is not None and global_frame - silence_start >= minimum_silence_frames:
        spans_frames.append((silence_start, global_frame))
    if mono_count != info.frames:
        raise ProductionAudioError("reference WAV decoded frame count is inconsistent")
    rms = math.sqrt(mono_square_sum / mono_count)

    def db(value: float) -> float:
        return 20.0 * math.log10(value) if value > 0 else -120.0

    spans = [
        ((end - start) / sample_rate, start, end)
        for start, end in spans_frames
    ]
    silence_total = sum(item[0] for item in spans)
    diagnostics = {
        "signal": {
            "rms_dbfs": round(db(rms), 4),
            "dc_offset": round(mono_sum / mono_count, 9),
            "leading_silence_seconds": round(spans[0][0] if spans and spans[0][1] == 0 else 0.0, 6),
            "trailing_silence_seconds": round(
                spans[-1][0] if spans and spans[-1][2] >= info.frames else 0.0, 6
            ),
            "silence_ratio": round(silence_total / max(float(info.duration), 1e-9), 6),
            "longest_silence_seconds": round(max((item[0] for item in spans), default=0.0), 6),
            "noise_floor_estimate_dbfs": None,
            "silence_run_count": len(spans),
            "hum_indicators": None,
        },
        "transient_candidates": [],
    }
    metadata = {
        "duration_seconds": float(info.duration),
        "sample_rate": int(sample_rate),
        "channels": int(info.channels),
        "subtype": str(info.subtype),
        "byte_size": path.stat().st_size,
        "sha256": _sha256_file(path),
        "frame_count": int(info.frames),
        "block_alignment": int(info.channels)
        * ({"PCM_16": 2, "PCM_24": 3, "PCM_32": 4}.get(str(info.subtype), 0)),
        "riff_declared_byte_size": declared_size,
        "riff_data_byte_size": data_size,
        "riff_integrity_status": "valid",
        "sample_peak_dbfs": db(peak),
        "clipping_sample_count": clipping,
    }
    bytes_per_sample = {"PCM_16": 2, "PCM_24": 3, "PCM_32": 4}.get(str(info.subtype))
    if bytes_per_sample is not None and data_size != info.frames * info.channels * bytes_per_sample:
        raise ProductionAudioError("reference WAV data size does not match its sample count")
    return metadata, diagnostics


def discover_references(
    references_dir: Path,
    reference_metadata: Path | None = None,
) -> tuple[tuple[ReferenceDescriptor, ...], tuple[InvalidReference, ...]]:
    """Discover and inspect every WAV deterministically without changing it."""
    root = references_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"references directory does not exist: {references_dir}")
    metadata = _metadata_mapping(reference_metadata)
    valid: list[ReferenceDescriptor] = []
    invalid: list[InvalidReference] = []
    candidates = sorted(
        (item for item in root.rglob("*") if item.is_file() or item.is_symlink()),
        key=lambda item: item.relative_to(root).as_posix().casefold(),
    )
    for candidate in candidates:
        relative = candidate.relative_to(root).as_posix()
        if candidate.suffix.casefold() != ".wav":
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if not _inside(resolved, root):
                raise ProductionAudioError("reference symlink escapes canonical directory")
            source_info, diagnostics = _inspect_source_wav(resolved)
        except (OSError, RuntimeError, ProductionAudioError, ValueError) as exc:
            invalid.append(InvalidReference(candidate.name, relative, str(exc)))
            continue
        digest = source_info["sha256"]
        identity_payload = {
            "schema": REFERENCE_BENCHMARK_SCHEMA,
            "relative_path": relative,
            "sha256": digest,
            "metadata": metadata.get(relative, {}),
        }
        identity = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        values = _signal_values(diagnostics)
        transient = values[7]
        valid.append(
            ReferenceDescriptor(
                filename=candidate.name,
                relative_path=relative,
                path=str(resolved),
                reference_family=metadata.get(relative, {}).get("family"),
                reference_variant=metadata.get(relative, {}).get("variant"),
                reference_notes=metadata.get(relative, {}).get("notes"),
                sha256=digest,
                reference_identity=identity,
                file_size=source_info["byte_size"],
                duration_seconds=source_info["duration_seconds"],
                sample_rate=source_info["sample_rate"],
                channels=source_info["channels"],
                subtype=source_info["subtype"],
                frame_count=source_info["frame_count"],
                block_alignment=source_info["block_alignment"] or None,
                riff_declared_byte_size=source_info["riff_declared_byte_size"],
                riff_data_byte_size=source_info["riff_data_byte_size"],
                riff_integrity_status=source_info["riff_integrity_status"],
                peak_dbfs=source_info["sample_peak_dbfs"],
                rms_dbfs=values[0],
                clipping_count=source_info["clipping_sample_count"],
                clipping_rate=source_info["clipping_sample_count"]
                / max(source_info["frame_count"] * source_info["channels"], 1),
                dc_offset=values[1],
                leading_silence_seconds=values[2],
                trailing_silence_seconds=values[3],
                silence_ratio=values[4],
                longest_silence_seconds=values[5],
                noise_floor_estimate_dbfs=values[6],
                transient_candidates=len(transient) if isinstance(transient, list) else None,
                hum_indicators=values[8] if isinstance(values[8], dict) else None,
            )
        )
    return tuple(valid), tuple(invalid)


def load_reference_corpus(path: Path = DEFAULT_CORPUS_PATH) -> ReferenceCorpus:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"reference corpus is unreadable: {path}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != REFERENCE_CORPUS_SCHEMA:
        raise ValueError("reference corpus schema is invalid")
    version = str(raw.get("version", "")).strip()
    if not version:
        raise ValueError("reference corpus version is required")

    def parse(name: str) -> tuple[CorpusCase, ...]:
        values = raw.get(name)
        if not isinstance(values, list):
            raise ValueError(f"reference corpus {name} must be a list")
        result: list[CorpusCase] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("reference corpus cases must be mappings")
            case_id = str(value.get("id", "")).strip()
            text = str(value.get("text", "")).strip()
            if not case_id or not text or case_id in seen:
                raise ValueError("reference corpus case ids/text must be non-empty and unique")
            seen.add(case_id)
            result.append(CorpusCase(case_id, text))
        return tuple(result)

    quick = parse("quick_cases")
    full = parse("full_cases")
    if {item.case_id for item in quick} & {item.case_id for item in full}:
        raise ValueError("quick and full case ids must be unique")
    return ReferenceCorpus(version, quick, full)


def load_pronunciation_corpus(path: Path = DEFAULT_PRONUNCIATION_CORPUS_PATH) -> dict[str, Any]:
    """Load the independently versioned Pronunciation Lab input corpus."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"pronunciation corpus is unreadable: {path}") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != "neovids_pronunciation_reference_corpus_v1"
    ):
        raise ValueError("pronunciation corpus schema is invalid")
    version = str(raw.get("version", "")).strip()
    targets = raw.get("targets")
    if not version or not isinstance(targets, list):
        raise ValueError("pronunciation corpus version and targets are required")
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("pronunciation corpus targets must be mappings")
        term = str(target.get("term", "")).strip()
        tier = str(target.get("tier", "")).strip()
        contexts = target.get("contexts")
        if (
            not term
            or not tier
            or term.casefold() in seen
            or not isinstance(contexts, list)
            or not contexts
            or any(not str(item).strip() for item in contexts)
        ):
            raise ValueError("pronunciation corpus targets require unique terms and contexts")
        seen.add(term.casefold())
        parsed.append(
            {"term": term, "tier": tier, "contexts": tuple(str(item).strip() for item in contexts)}
        )
    return {"schema": raw["schema"], "version": version, "targets": tuple(parsed)}


def expand_matrix(
    references: Iterable[ReferenceDescriptor],
    cases: Iterable[CorpusCase],
    seeds: Iterable[int],
    *,
    generation_profile: str = "narration_v1",
    lexicon_identity: str = "unknown",
    max_cases: int | None = None,
) -> tuple[MatrixCase, ...]:
    result: list[MatrixCase] = []
    for reference in references:
        for case in cases:
            for seed in seeds:
                payload = {
                    "schema": REFERENCE_BENCHMARK_SCHEMA,
                    "reference_identity": reference.reference_identity,
                    "clone_mode": CLONE_MODE,
                    "corpus_case": case.case_id,
                    "corpus_text": case.text,
                    "generation_profile": generation_profile,
                    "lexicon_identity": lexicon_identity,
                    "seed": seed,
                }
                identity = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                result.append(
                    MatrixCase(
                        reference, case, int(seed), generation_profile, lexicon_identity, identity
                    )
                )
                if max_cases is not None and len(result) >= max_cases:
                    return tuple(result)
    return tuple(result)


def normalize_metric(value: Any) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        else None
    )


def classify_content(report: ContentCoverageReport) -> tuple[str, str]:
    """Keep pronunciation/ASR disagreement distinct from likely lost content."""
    metrics = report.metrics
    disagreement = str(metrics.get("disagreement_classification", ""))
    if disagreement == "pronunciation_or_asr_disagreement":
        return "inconclusive", "likely" if report.status == "rejected" else "possible"
    if "obvious_out_of_order_segments" in report.reasons:
        return "probable_reordering", "none"
    if any("repetition" in reason for reason in report.reasons):
        return "probable_repetition", "none"
    if report.status == "passed":
        return "complete", "none"
    return "probable_missing_content", "none"


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def aggregate_summary(
    records: Iterable[dict[str, Any]], references: Iterable[ReferenceDescriptor]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    all_records = list(records)
    for reference in references:
        selected = [
            r for r in all_records if r.get("reference_identity") == reference.reference_identity
        ]
        successful = [
            r
            for r in selected
            if r.get("generation_status") == "success"
            and r.get("artifact_integrity_status") == "valid"
        ]

        def vals(rows: list[dict[str, Any]], key: str) -> list[float]:
            return [v for r in rows if (v := normalize_metric(r.get(key))) is not None]

        def count(rows: list[dict[str, Any]], value: str, key: str) -> int:
            return sum(r.get(key) == value for r in rows)

        row: dict[str, Any] = {
            "reference": reference.relative_path,
            "reference_identity": reference.reference_identity,
            "reference_family": reference.reference_family,
            "reference_variant": reference.reference_variant,
            "requested_case_count": len(selected),
            "successful_case_count": len(successful),
            "failure_count": len(selected) - len(successful),
            "failure_rate": round((len(selected) - len(successful)) / len(selected), 6)
            if selected
            else 0.0,
            "quality_guard_rejection_count": count(selected, "rejected", "qwen_guard_status"),
            "quality_guard_rejection_rate": round(
                count(selected, "rejected", "qwen_guard_status") / len(selected), 6
            )
            if selected
            else 0.0,
            "artifact_integrity_failure_count": count(
                selected, "invalid", "artifact_integrity_status"
            ),
            "artifact_integrity_failure_rate": round(
                count(selected, "invalid", "artifact_integrity_status") / len(selected), 6
            )
            if selected
            else 0.0,
            "asr_evaluated_count": sum(
                r.get("asr_status") in {"success", "low_confidence"} for r in selected
            ),
            "asr_unavailable_count": sum(
                r.get("asr_status") in {"unavailable", "disabled"} for r in selected
            ),
            "mean_wer": _mean(vals(successful, "wer")),
            "median_wer": _median(vals(successful, "wer")),
            "mean_cer": _mean(vals(successful, "cer")),
            "median_cer": _median(vals(successful, "cer")),
            "mean_coverage": _mean(vals(successful, "coverage")),
            "median_coverage": _median(vals(successful, "coverage")),
            "probable_content_failure_count": count(
                selected, "probable_missing_content", "content_status"
            ),
            "probable_repetition_count": count(selected, "probable_repetition", "content_status"),
            "probable_reordering_count": count(selected, "probable_reordering", "content_status"),
            "pronunciation_disagreement_count": sum(
                r.get("pronunciation_disagreement_status") in {"possible", "likely"}
                for r in selected
            ),
            "inconclusive_count": count(selected, "inconclusive", "content_status"),
            "runaway_guard_related_count": sum(
                r.get("failure_category") in {"chunk_quality_guard", "runaway_generation"}
                for r in selected
            ),
            "mean_duration_seconds": _mean(vals(successful, "physical_duration_seconds")),
            "median_duration_seconds": _median(vals(successful, "physical_duration_seconds")),
            "mean_generation_latency_seconds": _mean(
                vals(successful, "generation_latency_seconds")
            ),
            "median_generation_latency_seconds": _median(
                vals(successful, "generation_latency_seconds")
            ),
            "mean_realtime_factor": _mean(vals(successful, "realtime_factor")),
            "median_realtime_factor": _median(vals(successful, "realtime_factor")),
            "mean_pause_count": _mean(vals(successful, "pause_count")),
            "median_pause_count": _median(vals(successful, "pause_count")),
            "mean_silence_ratio": _mean(vals(successful, "silence_ratio")),
            "median_silence_ratio": _median(vals(successful, "silence_ratio")),
            "mean_longest_silence_seconds": _mean(vals(successful, "longest_silence_seconds")),
            "mean_leading_silence_seconds": _mean(vals(successful, "leading_silence_seconds")),
            "mean_trailing_silence_seconds": _mean(vals(successful, "trailing_silence_seconds")),
            "mean_transient_count": _mean(vals(successful, "transient_count")),
            "median_transient_count": _median(vals(successful, "transient_count")),
        }
        rows.append(row)
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("reference_family"):
            families.setdefault(str(row["reference_family"]), []).append(row)
    return {"schema": REFERENCE_BENCHMARK_SCHEMA, "references": rows, "families": families}


LISTENING_COLUMNS = (
    "display_id",
    "reference",
    "reference_family",
    "reference_variant",
    "case",
    "seed",
    "wav",
    "timbre_similarity_1_5",
    "naturalness_1_5",
    "rhythm_prosody_1_5",
    "ptbr_accent_1_5",
    "pronunciation_1_5",
    "breath_artifacts_1_5",
    "click_artifacts_1_5",
    "overall_1_5",
    "preferred",
    "notes",
)


def write_listening_worksheet(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "display_id": f"take-{index:04d}",
            "reference": record.get("reference"),
            "reference_family": record.get("reference_family"),
            "reference_variant": record.get("reference_variant"),
            "case": record.get("case_id"),
            "seed": record.get("requested_seed"),
            "wav": record.get("wav"),
            **{column: "" for column in LISTENING_COLUMNS[7:]},
        }
        for index, record in enumerate(records, start=1)
        if record.get("generation_status") == "success"
        and record.get("artifact_integrity_status") == "valid"
        and record.get("wav")
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LISTENING_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    rows = summary.get("references", [])
    if not rows:
        path.write_text("reference\n", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_listening_guide(path: Path) -> None:
    path.write_text(
        """# Reference regression listening worksheet

Rate each dimension from 1 (very poor) to 5 (excellent). Higher is always better.

- timbre similarity: resemblance to the target voice.
- naturalness: absence of synthetic or distracting delivery.
- rhythm/prosody: timing, emphasis and intonation.
- PT-BR accent: natural Brazilian Portuguese accent.
- pronunciation: intelligibility of words and names.
- breath artifacts: 1 = severe/distracting artifacts; 5 = no problematic artifacts.
- click artifacts: 1 = severe/distracting clicks/transients; 5 = no problematic artifacts.
- overall: your holistic preference.

Machine metrics are filters and safety signals, not an automatic winner. Record comments in `notes` and mark `preferred` explicitly.
""",
        encoding="utf-8",
    )


def aggregate_human_ratings(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rating_columns = [column for column in LISTENING_COLUMNS if column.endswith("_1_5")]
    result: dict[str, Any] = {"source": path.name, "rows": len(rows), "ratings": {}, "comments": []}
    for column in rating_columns:
        values = [float(row[column]) for row in rows if row.get(column, "").strip()]
        result["ratings"][column] = {"count": len(values), "mean": _mean(values)}
    result["comments"] = [
        {"display_id": row.get("display_id"), "notes": row.get("notes")}
        for row in rows
        if row.get("notes", "").strip()
    ]
    return result


def utc_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(4).hex()


def resume_decision(
    previous: dict[str, Any] | None,
    artifact_path: Path,
    *,
    retry_failed: bool = False,
) -> str:
    """Return ``skip`` or ``run`` while preserving all previous attempts."""
    if previous is None:
        return "run"
    if previous.get("generation_status") == "success":
        expected = previous.get("artifact_sha256")
        if artifact_path.is_file() and expected:
            try:
                actual = _sha256_file(artifact_path)
            except OSError:
                return "run"
            return "skip" if actual == expected else "run"
        return "run"
    return "run" if retry_failed else "skip"
