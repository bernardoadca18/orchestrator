#!/usr/bin/env python3
"""Inventory historical Qwen chunks and evaluate a grouped accent-detector POC.

Unknown items are always emitted as UNCERTAIN.  Labels are accepted only from
explicit command-line mappings, and audio remains in its existing storage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from app.services.accent_qa import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    extract_accent_features,
    feature_identity,
)

LABELS = {"PT_BR_GOOD", "PT_PT_BAD", "UNCERTAIN"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _parse_labels(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        job_id, separator, label = value.partition("=")
        if not separator or label not in LABELS or not job_id:
            raise ValueError(f"invalid --job-label {value!r}")
        result[job_id] = label
    return result


def _parse_chunk_labels(values: list[str]) -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for value in values:
        identity, separator, label = value.partition("=")
        job_id, chunk_separator, raw_index = identity.rpartition(":")
        if (
            not separator
            or not chunk_separator
            or label not in LABELS
            or not job_id
            or not raw_index.isdigit()
        ):
            raise ValueError(f"invalid --chunk-label {value!r}")
        result[(job_id, int(raw_index))] = label
    return result


def inventory(args: argparse.Namespace) -> int:
    labels = _parse_labels(args.job_label)
    chunk_labels = _parse_chunk_labels(args.chunk_label)
    database = args.database.resolve(strict=True)
    output_root = args.outputs.resolve(strict=True)
    entries: list[dict[str, object]] = []
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        job_ids = set(labels) | {job_id for job_id, _ in chunk_labels}
        for job_id in sorted(job_ids):
            row = connection.execute(
                "SELECT * FROM narration_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"Qwen job {job_id} was not found")
            metadata = json.loads(row["metadata_json"])
            for chunk in metadata.get("chunks", []):
                index = int(chunk["index"])
                audio = output_root / "chunks" / job_id / f"chunk_{index:04d}.wav"
                actual_sha = _sha256(audio.resolve(strict=True))
                expected_sha = str(chunk["chunk_sha256"])
                if actual_sha != expected_sha:
                    raise RuntimeError(f"chunk SHA mismatch for {job_id}/{index}")
                label = chunk_labels.get((job_id, index), labels.get(job_id, "UNCERTAIN"))
                entries.append(
                    {
                        "schema": "neovids_accent_corpus_item_v1",
                        "workflow_id": None,
                        "block_id": None,
                        "chunk_id": f"{job_id}_{index:04d}",
                        "take_id": None,
                        "job_id": job_id,
                        "chunk_index": index,
                        "storage_uri": f"qwen://narrations/{job_id}/chunks/{index}",
                        "audio_sha256": actual_sha,
                        "seed": int(chunk["seed"]),
                        "voice_profile": row["voice_profile"],
                        "voice_profile_identity": row["voice_identity"],
                        "generation_profile": row["generation_profile"],
                        "generation_profile_identity": row["generation_identity"],
                        "model_revision": row["model_revision"],
                        "label": label,
                        "label_source": args.label_source,
                        "label_scope": (
                            "chunk" if (job_id, index) in chunk_labels else "generation_job"
                        ),
                        "notes": (args.uncertain_notes if label == "UNCERTAIN" else args.notes),
                        "group_id": job_id,
                        "audio_path": str(audio.relative_to(output_root)),
                    }
                )
    manifest_header = {
        "schema": "neovids_accent_corpus_manifest_v1",
        "item_count": len(entries),
        "labels": dict(Counter(str(item["label"]) for item in entries)),
        "groups": len({item["group_id"] for item in entries}),
    }
    manifest_header["manifest_identity"] = _identity([manifest_header, entries])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as destination:
        destination.write(json.dumps(manifest_header, ensure_ascii=False) + "\n")
        for entry in entries:
            destination.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(manifest_header, ensure_ascii=False, indent=2))
    return 0


def _load_manifest(path: Path, output_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not lines or lines[0].get("schema") != "neovids_accent_corpus_manifest_v1":
        raise ValueError("invalid accent corpus manifest")
    entries = lines[1:]
    for entry in entries:
        if entry.get("label") not in LABELS:
            raise ValueError("invalid accent corpus label")
        entry["resolved_audio_path"] = output_root / entry["audio_path"]
    return lines[0], entries


def _fit(train: list[tuple[dict[str, Any], np.ndarray]]) -> dict[str, np.ndarray]:
    matrix = np.stack([vector for _, vector in train])
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale < 1e-6] = 1.0
    normalized = (matrix - mean) / scale
    labels = [entry["label"] for entry, _ in train]
    return {
        "mean": mean,
        "scale": scale,
        "pt_br": normalized[np.array(labels) == "PT_BR_GOOD"].mean(axis=0),
        "pt_pt": normalized[np.array(labels) == "PT_PT_BAD"].mean(axis=0),
    }


def _score(vector: np.ndarray, fitted: dict[str, np.ndarray]) -> float:
    normalized = (vector - fitted["mean"]) / fitted["scale"]
    br_distance = float(np.mean(np.square(normalized - fitted["pt_br"])))
    pt_distance = float(np.mean(np.square(normalized - fitted["pt_pt"])))
    delta = max(-60.0, min(60.0, br_distance - pt_distance))
    return 1.0 / (1.0 + np.exp(-delta))


def evaluate(args: argparse.Namespace) -> int:
    header, entries = _load_manifest(args.manifest, args.outputs.resolve(strict=True))
    labeled = [entry for entry in entries if entry["label"] != "UNCERTAIN"]
    featured: list[tuple[dict[str, Any], np.ndarray]] = []
    for entry in labeled:
        features = extract_accent_features(Path(entry["resolved_audio_path"]))
        featured.append(
            (entry, np.array([features[name] for name in FEATURE_NAMES], dtype=np.float64))
        )
    groups_by_label = {
        label: sorted({entry["group_id"] for entry, _ in featured if entry["label"] == label})
        for label in ("PT_BR_GOOD", "PT_PT_BAD")
    }
    predictions: list[tuple[str, str]] = []
    skipped_groups: list[str] = []
    for group in sorted({entry["group_id"] for entry, _ in featured}):
        train = [item for item in featured if item[0]["group_id"] != group]
        test = [item for item in featured if item[0]["group_id"] == group]
        if {entry["label"] for entry, _ in train} != {"PT_BR_GOOD", "PT_PT_BAD"}:
            skipped_groups.append(str(group))
            continue
        fitted = _fit(train)
        for entry, vector in test:
            score = _score(vector, fitted)
            predicted = (
                "LIKELY_PT_PT"
                if score >= args.likely_pt_pt_min_score
                else "PT_BR_CONFIDENT"
                if score <= args.pt_br_confident_max_score
                else "UNCERTAIN"
            )
            predictions.append((entry["label"], predicted))
    tp = sum(
        actual == "PT_PT_BAD" and predicted == "LIKELY_PT_PT" for actual, predicted in predictions
    )
    fp = sum(
        actual == "PT_BR_GOOD" and predicted == "LIKELY_PT_PT" for actual, predicted in predictions
    )
    # An uncertain PT-PT result is safe for triage but is still a miss for an
    # enforcement policy that automatically rejects only LIKELY_PT_PT.
    fn = sum(
        actual == "PT_PT_BAD" and predicted != "LIKELY_PT_PT" for actual, predicted in predictions
    )
    tn = sum(
        actual == "PT_BR_GOOD" and predicted != "LIKELY_PT_PT" for actual, predicted in predictions
    )
    pt_pt_recall = tp / (tp + fn) if tp + fn else None
    pt_pt_precision = tp / (tp + fp) if tp + fp else None
    pt_br_fpr = fp / (fp + tn) if fp + tn else None
    sample_sufficient = all(
        len(groups) >= args.minimum_groups_per_class for groups in groups_by_label.values()
    ) and bool(predictions)
    metric_gates_pass = (
        pt_pt_recall is not None
        and pt_br_fpr is not None
        and pt_pt_recall >= args.minimum_pt_pt_recall
        and pt_br_fpr <= args.maximum_pt_br_false_positive_rate
    )
    safe_for_enforcement = sample_sufficient and metric_gates_pass
    result = "PASS" if safe_for_enforcement else "FAIL" if sample_sufficient else "INCONCLUSIVE"
    prediction_counts = Counter(predicted for _, predicted in predictions)
    report = {
        "schema": "neovids_accent_poc_report_v1",
        "manifest_identity": header.get("manifest_identity"),
        "feature_version": FEATURE_VERSION,
        "feature_identity": feature_identity(),
        "classifier": "standardized_nearest_centroid",
        "split_strategy": "leave_one_generation_job_out",
        "sample_count": len(featured),
        "groups_by_label": groups_by_label,
        "skipped_holdout_groups": skipped_groups,
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "prediction_counts": dict(prediction_counts),
        "pt_pt_recall": pt_pt_recall,
        "pt_pt_precision": pt_pt_precision,
        "pt_br_false_positive_rate": pt_br_fpr,
        "pt_pt_false_negative_rate": fn / (tp + fn) if tp + fn else None,
        "minimum_groups_per_class": args.minimum_groups_per_class,
        "thresholds": {
            "pt_br_confident_max_score": args.pt_br_confident_max_score,
            "likely_pt_pt_min_score": args.likely_pt_pt_min_score,
        },
        "evidence_gates": {
            "minimum_pt_pt_recall": args.minimum_pt_pt_recall,
            "maximum_pt_br_false_positive_rate": args.maximum_pt_br_false_positive_rate,
            "sample_count_sufficient": sample_sufficient,
            "metrics_pass": metric_gates_pass,
        },
        "poc_result": result,
        "safe_for_shadow_mode": bool(featured),
        "safe_for_enforcement": safe_for_enforcement,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if safe_for_enforcement else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("inventory-qwen")
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--outputs", type=Path, required=True)
    create.add_argument("--job-label", action="append", default=[])
    create.add_argument(
        "--chunk-label",
        action="append",
        default=[],
        metavar="JOB_ID:CHUNK_INDEX=LABEL",
    )
    create.add_argument("--label-source", required=True)
    create.add_argument("--notes", default="")
    create.add_argument("--uncertain-notes", default="label not established chunk-by-chunk")
    create.add_argument("--output", type=Path, required=True)
    create.set_defaults(handler=inventory)
    poc = commands.add_parser("evaluate")
    poc.add_argument("--manifest", type=Path, required=True)
    poc.add_argument("--outputs", type=Path, required=True)
    poc.add_argument("--report", type=Path)
    poc.add_argument("--minimum-groups-per-class", type=int, default=3)
    poc.add_argument("--pt-br-confident-max-score", type=float, default=0.35)
    poc.add_argument("--likely-pt-pt-min-score", type=float, default=0.65)
    poc.add_argument("--minimum-pt-pt-recall", type=float, default=0.90)
    poc.add_argument("--maximum-pt-br-false-positive-rate", type=float, default=0.10)
    poc.set_defaults(handler=evaluate)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        return int(args.handler(args))
    except (OSError, ValueError, LookupError, RuntimeError) as exc:
        print(f"accent corpus error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
