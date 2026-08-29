#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# NeoVids - Final Reference Regression
# ============================================================
#
# Compares the two reference finalists using two additional
# seeds. Seed 424242 was already evaluated in the previous run.
#
# Matrix:
#   2 references
#   x 12 full-corpus cases
#   x seeds 202 + 303
#   = 48 new generations
#
# Usage:
#
#   PLAN:
#     ./run_reference_finalists.sh
#
#   REAL RUN:
#     ./run_reference_finalists.sh --run
#
# ============================================================

ORCH_DIR="${ORCH_DIR:-$HOME/workspaces/neovids/orchestrator}"
QWEN_DIR="${QWEN_DIR:-$HOME/workspaces/neovids/qwen3-tts-workshop}"

REFERENCES_DIR="${REFERENCES_DIR:-/data/references}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/data/audio/benchmarks/reference-regression}"
EXPORT_DIR="${EXPORT_DIR:-$ORCH_DIR/benchmark_exports}"

QWEN_READY_URL="${QWEN_READY_URL:-http://localhost:8889/health/ready}"

REFS=(
  "ref_full_processed_raw.wav"
  "enhanced_ref_full_processed_raw.wav"
)

SEEDS=(
  "202"
  "303"
)

RUN=false

case "${1:-}" in
  "")
    ;;
  --run)
    RUN=true
    ;;
  *)
    echo "Usage:"
    echo "  $0"
    echo "  $0 --run"
    exit 2
    ;;
esac

echo
echo "============================================================"
echo " NeoVids - Final Reference Regression"
echo "============================================================"
echo
echo "References:"
for ref in "${REFS[@]}"; do
  echo "  - $ref"
done

echo
echo "Seeds:"
for seed in "${SEEDS[@]}"; do
  echo "  - $seed"
done

echo
echo "Mode:              full"
echo "Clone mode:        x_vector_only"
echo "Generation:        narration_v1"
echo "Synthesis lang:    Portuguese"
echo "ASR lang:          pt"
echo "References:        ${#REFS[@]}"
echo "Corpus cases:      12"
echo "Seeds:             ${#SEEDS[@]}"
echo "Expected matrix:   $((${#REFS[@]} * 12 * ${#SEEDS[@]})) generations"
echo

if [[ ! -d "$ORCH_DIR" ]]; then
  echo "ERROR: Orchestrator directory not found:"
  echo "  $ORCH_DIR"
  exit 1
fi

cd "$ORCH_DIR"

API_CONTAINER="$(docker compose ps -q api)"

if [[ -z "$API_CONTAINER" ]]; then
  echo "ERROR: Orchestrator API container is not running."
  echo
  echo "Start it with:"
  echo "  cd $ORCH_DIR"
  echo "  docker compose up -d api"
  exit 1
fi

echo "Checking finalist references..."

for ref in "${REFS[@]}"; do
  if docker compose exec -T api test -r "$REFERENCES_DIR/$ref"; then
    echo "  OK  $ref"
  else
    echo "  ERROR  $ref not found/readable at $REFERENCES_DIR/$ref"
    exit 1
  fi
done

META_ARGS=()

if docker compose exec -T api test -f "$REFERENCES_DIR/reference_metadata.yaml"; then
  META_ARGS=(
    --reference-metadata
    "$REFERENCES_DIR/reference_metadata.yaml"
  )
fi

BENCHMARK_CMD=(
  docker compose exec -T api
  python scripts/benchmark_voice_references.py
  --references-dir "$REFERENCES_DIR"
  --mode full
  --refs "${REFS[@]}"
  --seeds "${SEEDS[@]}"
  --output "$OUTPUT_ROOT"
  "${META_ARGS[@]}"
)

echo
echo "============================================================"
echo " DRY PLAN"
echo "============================================================"
echo

PLAN_LOG="$(mktemp)"
trap 'rm -f "$PLAN_LOG"' EXIT

"${BENCHMARK_CMD[@]}" | tee "$PLAN_LOG"

if ! grep -q "Planned generations: 48" "$PLAN_LOG"; then
  echo
  echo "ERROR: expected 48 generations."
  echo "Review the dry-plan output before running."
  exit 1
fi

if [[ "$RUN" != true ]]; then
  echo
  echo "============================================================"
  echo " PLAN COMPLETE"
  echo "============================================================"
  echo
  echo "Expected matrix confirmed: 48 generations."
  echo "No synthesis was performed."
  echo
  echo "Run for real with:"
  echo
  echo "  ./run_reference_finalists.sh --run"
  echo
  exit 0
fi

echo
echo "============================================================"
echo " QWEN READINESS"
echo "============================================================"
echo

READY_BODY="$(mktemp)"

HTTP_CODE="$(
  curl -sS \
    -o "$READY_BODY" \
    -w '%{http_code}' \
    "$QWEN_READY_URL" \
    || true
)"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: Qwen is not ready. HTTP $HTTP_CODE"
  echo
  cat "$READY_BODY" || true
  echo

  if [[ -d "$QWEN_DIR" ]]; then
    echo "Recent Qwen logs:"
    (
      cd "$QWEN_DIR"
      docker compose logs --tail=100 qwen-tts || true
    )
  fi

  rm -f "$READY_BODY"
  exit 1
fi

rm -f "$READY_BODY"

echo "Qwen ready: HTTP 200"

echo
echo "============================================================"
echo " REAL GPU RUN"
echo "============================================================"
echo

RUN_LOG="$(mktemp)"

set +e
"${BENCHMARK_CMD[@]}" --run | tee "$RUN_LOG"
BENCHMARK_EXIT="${PIPESTATUS[0]}"
set -e

if [[ "$BENCHMARK_EXIT" -ne 0 ]]; then
  echo
  echo "ERROR: benchmark exited with code $BENCHMARK_EXIT"
  echo "Run log preserved at:"
  echo "  $RUN_LOG"
  exit "$BENCHMARK_EXIT"
fi

RUN_PATH="$(
  grep -oE 'output=[^ ]+' "$RUN_LOG" \
  | tail -1 \
  | cut -d= -f2-
)"

if [[ -z "$RUN_PATH" ]]; then
  echo "ERROR: could not determine benchmark run path."
  exit 1
fi

RUN_ID="$(basename "$RUN_PATH")"

echo
echo "Run:"
echo "  $RUN_ID"
echo
echo "Container path:"
echo "  $RUN_PATH"

# ============================================================
# Blind listening package
# ============================================================

echo
echo "============================================================"
echo " CREATING BLIND LISTENING PACK"
echo "============================================================"
echo

docker compose exec -T api python - "$RUN_PATH" <<'PY'
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path

run_dir = Path(sys.argv[1])
manifest_path = run_dir / "manifest.jsonl"
blind_dir = run_dir / "blind"

if not manifest_path.is_file():
    raise SystemExit(f"manifest not found: {manifest_path}")

entries = []

with manifest_path.open("r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue

        row = json.loads(line)

        if row.get("generation_status") != "success":
            continue

        if row.get("artifact_integrity_status") != "valid":
            continue

        wav = row.get("wav")

        if not wav:
            continue

        source = run_dir / wav

        if not source.is_file():
            continue

        entries.append(row)

if not entries:
    raise SystemExit("no successful valid benchmark WAVs found")

references = sorted({x["reference"] for x in entries})

# Blind mapping is stable inside this run, but deliberately opaque.
labels = [chr(ord("A") + i) for i in range(len(references))]

mapping_seed = int.from_bytes(
    hashlib.sha256(
        (run_dir.name + "|final-reference-blind-v1").encode()
    ).digest()[:8],
    "big",
)

rng = random.Random(mapping_seed)
rng.shuffle(labels)

ref_to_label = dict(zip(references, labels, strict=True))

blind_dir.mkdir(parents=True, exist_ok=True)

# Private identity mapping.
first = {}

for row in entries:
    first.setdefault(row["reference"], row)

with (blind_dir / "map_PRIVATE.csv").open(
    "w",
    newline="",
    encoding="utf-8",
) as f:
    writer = csv.writer(f)

    writer.writerow([
        "blind_reference",
        "reference",
        "reference_sha256",
        "reference_identity",
    ])

    for reference in sorted(references):
        row = first[reference]

        writer.writerow([
            ref_to_label[reference],
            reference,
            row.get("reference_sha256", ""),
            row.get("reference_identity", ""),
        ])

listening_rows = []

for row in entries:
    label = ref_to_label[row["reference"]]
    case_id = row["case_id"]
    seed = row["requested_seed"]

    source = run_dir / row["wav"]

    destination_dir = (
        blind_dir
        / f"seed_{seed}"
        / case_id
    )

    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / f"{label}.wav"

    if destination.exists():
        destination.unlink()

    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)

    listening_rows.append({
        "seed": seed,
        "case": case_id,
        "blind_reference": label,
        "wav": str(destination.relative_to(run_dir)),
        "canonical_text": row.get("canonical_text", ""),
        "timbre_similarity_1_5": "",
        "naturalness_1_5": "",
        "rhythm_prosody_1_5": "",
        "ptbr_accent_1_5": "",
        "pronunciation_1_5": "",
        "speaker_identity_stability_1_5": "",
        "breath_artifacts_1_5": "",
        "click_artifacts_1_5": "",
        "overall_1_5": "",
        "preferred": "",
        "notes": "",
    })

groups = defaultdict(list)

for row in listening_rows:
    groups[(row["seed"], row["case"])].append(row)

ordered = []

for group_key in sorted(groups):
    group = groups[group_key]

    local_seed = int.from_bytes(
        hashlib.sha256(
            (
                run_dir.name
                + "|"
                + repr(group_key)
                + "|play-order-v1"
            ).encode()
        ).digest()[:8],
        "big",
    )

    local_rng = random.Random(local_seed)
    local_rng.shuffle(group)

    for index, row in enumerate(group, start=1):
        row["play_order"] = index
        ordered.append(row)

fields = [
    "seed",
    "case",
    "play_order",
    "blind_reference",
    "wav",
    "canonical_text",
    "timbre_similarity_1_5",
    "naturalness_1_5",
    "rhythm_prosody_1_5",
    "ptbr_accent_1_5",
    "pronunciation_1_5",
    "speaker_identity_stability_1_5",
    "breath_artifacts_1_5",
    "click_artifacts_1_5",
    "overall_1_5",
    "preferred",
    "notes",
]

with (blind_dir / "listening.csv").open(
    "w",
    newline="",
    encoding="utf-8",
) as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(ordered)

(blind_dir / "README.txt").write_text(
    """NeoVids - Final Reference Blind Evaluation

DO NOT OPEN map_PRIVATE.csv until listening is finished.

This run compares two reference finalists across:

12 cases
x seeds 202 and 303.

Seed 424242 was evaluated in the previous auditory run.

Score:

5 = excellent
4 = good
3 = acceptable
2 = poor
1 = very poor

Higher is always better.

speaker_identity_stability_1_5:

5 = same expected speaker/timbre throughout the entire WAV
4 = very small drift
3 = noticeable but acceptable change
2 = major identity/timbre drift
1 = clearly wrong speaker/voice

Pay special attention to LONG_FORM.

Previous testing exposed a case where content/ASR was perfect while
speaker identity changed dramatically for part of the WAV.

For breath/click scores:

5 = no problematic artifact
4 = barely perceptible
3 = noticeable but acceptable
2 = distracting
1 = severe

Do not choose based on loudness.

Recommended priority:

1. naturalness
2. rhythm/prosody
3. PT-BR accent
4. pronunciation
5. speaker identity stability
6. breathing/artifacts
7. timbre similarity
""",
    encoding="utf-8",
)

print(f"Blind references: {len(references)}")
print(f"Blind outputs:    {len(entries)}")
print(f"Listening sheet:  {blind_dir / 'listening.csv'}")
print(f"Private map:      {blind_dir / 'map_PRIVATE.csv'}")
PY

# ============================================================
# Export
# ============================================================

echo
echo "============================================================"
echo " EXPORTING"
echo "============================================================"
echo

mkdir -p "$EXPORT_DIR"

ARCHIVE="${RUN_ID}-finalists.tar.gz"

docker compose exec -T api sh -lc "
  cd '$(dirname "$RUN_PATH")' &&
  tar -czf '/tmp/$ARCHIVE' '$RUN_ID'
"

docker cp \
  "$API_CONTAINER:/tmp/$ARCHIVE" \
  "$EXPORT_DIR/$ARCHIVE"

docker compose exec -T api rm -f "/tmp/$ARCHIVE" || true

echo
echo "============================================================"
echo " COMPLETE"
echo "============================================================"
echo
echo "New results:"
echo "  2 refs x 12 cases x 2 seeds = 48 WAVs"
echo
echo "Run ID:"
echo "  $RUN_ID"
echo
echo "Host archive:"
echo "  $EXPORT_DIR/$ARCHIVE"
echo
echo "Blind evaluation:"
echo "  $RUN_PATH/blind/listening.csv"
echo
echo "DO NOT OPEN:"
echo "  $RUN_PATH/blind/map_PRIVATE.csv"
echo
echo "Remember:"
echo "  seed 424242 results already exist in the previous run."
echo
